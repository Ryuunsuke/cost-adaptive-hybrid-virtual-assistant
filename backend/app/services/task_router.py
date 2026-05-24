from __future__ import annotations

import json
import re
import time
from typing import TypedDict, Literal, Optional

from langgraph.graph import StateGraph, END  # type: ignore

from services.LLMs import local_response, cloud_response
from services.tools import get_tool, list_tools
from services.db_con import get_session_context_summary
from services.cost_tracker import (
    Pool,
    BudgetExhaustedError,
    check_pool_available,
    deduct_cloud_actual,
    check_and_deduct_shadow,
    log_route_event,
)

CONFIDENCE_THRESHOLD_ADMINISTRATIVE: float = 0.70
CONFIDENCE_THRESHOLD_INFORMATIONAL:  float = 0.85

# Tool name that draws from the shadow reserve
SHADOW_RESERVE_TOOL: str = "generate_quiz"

# Sentinel returned to the frontend when a budget pool is exhausted
_BUDGET_EXHAUSTED_MSG = (
    "Your session budget has been reached for this type of request. "
    "You can still generate quizzes and answer them to earn bonus tokens, "
    "or wait for your daily budget to reset."
)
_SHADOW_EXHAUSTED_MSG = (
    "The quiz generation reserve for this session is currently exhausted. "
    "Please try again after your budget resets."
)

def _extract_json_block(text: str) -> str:
    """Strip markdown fences and return the first JSON-looking substring."""
    text = text.strip()
    text = re.sub(r"```(?:json)?", "", text).strip("`").strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else text

def _get_message(state: "AgentState") -> str:
    """
    Resolve the student's raw input regardless of which key was used.

    Callers that invoke app_instance.ainvoke() directly may still pass the
    legacy 'user_input' key.  This helper checks both so that old call-sites
    work without modification.
    """
    return state.get("message") or state.get("user_input") or ""  # type: ignore[attr-defined]

def _build_history_block(state: "AgentState") -> str:
    """Format the last ≤6 conversation turns as a prompt block."""
    turns = (state.get("session_history") or [])[-6:]
    if not turns:
        return ""
    lines = "\n".join(
        f"{t['role'].capitalize()}: {t['content']}" for t in turns
    )
    return f"\nConversation history:\n{lines}\n"

class AgentState(TypedDict):
    # ── backward-compatible alias (original field name used by FastAPI) ──
    user_input: str

    message:          str    # raw student input
    session_history:  list   # last ≤6 conversation turns
    session_id:       int    # FK to session.id_session
    category:         str    # "administrative" | "informational" | "analytical"
    confidence:       float  # 0.0–1.0, produced by llama3.2:3b classifier
    requires_tool:    bool   # True then route to tool_executor regardless of category
    routing_decision: str    # which path/model was selected
    response:         str    # final answer returned to the student

    budget_pool_used:  Optional[str]   # "visible" | "bonus" | "shadow" | None
    forced_tool_name:  Optional[str]   # set by the frontend to bypass llama tool selection

    document_context:  Optional[str]   # combined text of user-selected source files; forces local grounded path
    source_file_ids:   list            # IDs of files activated as source; passed to quiz/flashcard tools

    tool_calls:       list   # tool names invoked during this turn
    tool_results:     dict   # tool name → output mapping
    reasoning_steps:  list   # breadcrumb trail for debugging / evaluation
    timings:          dict   # wall-clock ms per node/tool

# local classifier
async def triage_node(state: AgentState) -> dict:

    _t0 = time.perf_counter()

    # Frontend explicitly flagged this as a tool call — skip LLM triage.
    if state.get("requires_tool"):
        print("[LANGGRAPH]: Triage skipped — force_tool pre-classified.")
        return {
            "category":        "informational",
            "confidence":      1.0,
            "requires_tool":   True,
            "reasoning_steps": ["triaged:pre_classified"],
            "timings":         {**state.get("timings", {}), "triage_ms": 0},
        }

    print("[LANGGRAPH]: Triaging (llama3.2:3b classification)...")

    msg = _get_message(state)

    prompt = f"""Classify the student query below. Respond with ONLY a valid JSON object — no markdown, no explanation.

        Categories (pick exactly one):
          "administrative" — scheduling, reminders, deadlines, study plans, calendar tasks
          "informational"  — explain what something is, define a term, describe how something works, list facts
          "analytical"     — critically evaluate, compare options, assess trade-offs, argue pros/cons, analyse implications

        Confidence calibration:
          0.90–1.00  query fits its category clearly (e.g. "What is X?", "Set a reminder for ...")
          0.75–0.89  clear category, moderate complexity
          0.50–0.74  genuinely uncertain about the right category

        Use "analytical" when the query contains ANY of these signals:
          critically, evaluate, assess, compare, contrast, versus, trade-off, trade-offs,
          pros and cons, advantages and disadvantages, implications, justify, argue, recommend

        Required output (no extra keys):
        {{
          "category": "administrative" | "informational" | "analytical",
          "confidence": <0.0-1.0>,
          "requires_tool": <true | false>
        }}

        requires_tool is true only when the student explicitly asks to generate a quiz,
        generate flashcards, summarise a document, or create a schedule.

        Student query: "{msg}"
    """
    system_prompt = (
        "You are a strict JSON classifier. Respond ONLY with a valid JSON object. "
        "No explanation, no markdown, no extra text."
    )

    raw = await local_response(prompt, system_prompt=system_prompt)

    try:
        parsed = json.loads(_extract_json_block(raw))
        category = str(parsed.get("category", "informational")).lower()
        if category not in ("administrative", "informational", "analytical"):
            category = "informational"
        confidence = float(parsed.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
        requires_tool = bool(parsed.get("requires_tool", False))
    except (json.JSONDecodeError, ValueError, TypeError):
        # treat as informational with sub-threshold confidence
        category = "informational"
        confidence = 0.5
        requires_tool = False

    msg_lower = msg.lower()

    # analytical signals override: if any analytical signal is present, 
    # boost to "analytical" with high confidence even if the LLM misclassified it as "informational" 
    # or returned low confidence
    _ANALYTICAL_VERBS = (
        "critically", "evaluate", "assess", "compare", "contrast",
        "justify", "argue",
    )
    _ANALYTICAL_NOUNS = ("trade-off", "trade-offs", "pros and cons", "implications",
                         "advantages and disadvantages")
    _COMPARISON_MARKERS = (" versus ", " vs ")
    _has_verb = any(v in msg_lower for v in _ANALYTICAL_VERBS)
    _has_noun = any(n in msg_lower for n in _ANALYTICAL_NOUNS)
    _has_cmp  = any(c in msg_lower for c in _COMPARISON_MARKERS)
    if category != "analytical" and (_has_verb or (_has_noun and _has_cmp)):
        category   = "analytical"
        confidence = 0.95

    # analytical signals override: if the LLM misclassified the query as "informational"
    if category == "analytical" and not _has_verb and not _has_cmp:
        category   = "informational"
        confidence = 0.50  # guaranteed below any threshold → cloud_standard via Override 4

    # confidence boost for factual queries
    _FACTUAL_PREFIXES = (
        "what is", "what are", "what does", "what do", "what was",
        "how does", "how do", "how is", "how are",
        "why is",  "why are",  "why does",
        "define ", "explain ", "describe ",
    )
    if (category == "informational"
            and confidence < CONFIDENCE_THRESHOLD_INFORMATIONAL
            and len(msg.split()) <= 14
            and any(msg_lower.startswith(p) for p in _FACTUAL_PREFIXES)
            and not _has_verb and not _has_noun and not _has_cmp):
        confidence = 0.90

    # confidence reduction for long, complex queries without analytical signals that may have been misclassified as informational
    if (category == "informational"
            and len(msg.split()) >= 15
            and not _has_verb and not _has_noun and not _has_cmp):
        confidence = min(confidence, 0.82)

    # intent override for explicit tool requests
    _ADMIN_SIGNALS = (
        "remind", "reminder",
        "assignment due", "report due", "project due",
        "due this", "due next", "due on", "due by", "is due",
        "deadline",
        "study plan", "revision plan", "study schedule",
        "prioritise my", "prioritize my",
        "exam next", "exam on",
    )
    if any(s in msg_lower for s in _ADMIN_SIGNALS):
        category   = "administrative"
        confidence = max(confidence, 0.80)

    print(
        f"[LANGGRAPH]: category={category}, confidence={confidence:.2f}, "
        f"requires_tool={requires_tool}"
    )
    return {
        "category":        category,
        "confidence":      confidence,
        "requires_tool":   requires_tool,
        "reasoning_steps": ["triaged"],
        "timings":         {**state.get("timings", {}), "triage_ms": round((time.perf_counter() - _t0) * 1000)},
    }

async def local_generation_node(state: AgentState) -> dict:

    print("[LANGGRAPH]: Local Generation Path (llama3.2:3b)...")
    _t0 = time.perf_counter()

    msg = _get_message(state)
    document_context = state.get("document_context")

    # ── Document source mode: grounded prompt, no context injection ───────
    if document_context:
        synthesis_prompt = f"""You are helping a student understand their study material.
            Answer the student's question by reasoning from the document excerpts provided below.
            Base your answer on the content in the excerpts. You may paraphrase, explain, or summarise — do not invent facts not supported by the text.
            If the topic genuinely does not appear anywhere in the excerpts, say so briefly and describe what the excerpts do cover.

            Document excerpts:
            {document_context}

            Student question: {msg}
        """
        system_prompt = (
            "You are a helpful study assistant. Answer from the provided document excerpts. "
            "Reason from the content — paraphrasing and summarising are fine. "
            "Do not fabricate facts not present in the excerpts."
        )
    else:
        # personalised synthesis prompt
        history_block = _build_history_block(state)

        try:
            ctx = await get_session_context_summary(state["session_id"])
            sched = ctx.get("schedule", [])
            sched_str = (
                ", ".join(
                    f"{'/'.join(s['topics']) if s['topics'] else 'study'} on {s['date']}"
                    for s in sched
                )
                if sched else "none scheduled"
            )
            lq = ctx.get("last_quiz")
            quiz_str = f"{lq['score']} / {lq['total']}" if lq else "not attempted"
            files = ctx.get("files", [])
            files_str = ", ".join(f["filename"] for f in files) if files else "none"
            budget = ctx.get("budget", {})
            budget_str = (
                f"{int(budget.get('visible_remaining', 0))} visible"
                f" + {int(budget.get('quiz_bonus', 0))} bonus"
            )
            _context_block = (
                f"\nStudent context:\n"
                f"- Upcoming schedule: {sched_str}\n"
                f"- Last quiz: {quiz_str}\n"
                f"- Uploaded documents: {files_str}\n"
                f"- Remaining tokens: {budget_str}\n"
            )
        except Exception:
            _context_block = ""

        synthesis_prompt = f"""You are a helpful and friendly academic assistant.
            Answer the student's request directly and naturally.
            Do not provide hypothetical examples, meta-commentary, or templates.
            {_context_block}{history_block}
            Student request: {msg}
        """
        system_prompt = (
            "You are a strict assistant. Reply with a single direct response only. "
            "Do not include examples, hypothetical wording, or descriptions of "
            "how the answer was generated."
        )

    res = (
        await local_response(synthesis_prompt, system_prompt=system_prompt)
        or "I've processed your request. How can I help further?"
    )

    # Log the local routing event (cost = 0, pool = None)
    await log_route_event(
        session_id=state["session_id"],
        message_id=None,
        model_name="llama3.2:3b",
        category=state.get("category", ""),
        confidence=state.get("confidence", 0.0),
        input_tokens=0, 
        output_tokens=0,
        pool=None,
    )

    reasoning_steps = list(state.get("reasoning_steps") or [])
    return {
        "response":         res,
        "routing_decision": "llama3.2:3b",
        "budget_pool_used": None,
        "reasoning_steps":  reasoning_steps + ["local_generation_executed"],
        "timings":          {**state.get("timings", {}), "generation_ms": round((time.perf_counter() - _t0) * 1000)},
    }

async def cloud_standard_node(state: AgentState) -> dict:
    print("[LANGGRAPH]: Cloud Standard Path (GPT-4o mini)...")
    _t0 = time.perf_counter()

    msg = _get_message(state)
    history_block = _build_history_block(state)

    synthesis_prompt = f"""You are a helpful and friendly academic assistant.
        Answer the student's request directly and naturally.
        Do not provide hypothetical examples, meta-commentary, or templates.
        {history_block}
        Student request: {msg}
    """
    system_prompt = (
        "You are a knowledgeable academic assistant. "
        "Provide a clear, accurate, and well-reasoned response."
    )

    # ── Pre-call: check which pool is available (no deduction yet) ────────
    try:
        pool_used = await check_pool_available(session_id=state["session_id"])
    except BudgetExhaustedError:
        reasoning_steps = list(state.get("reasoning_steps") or [])
        return {
            "response":         _BUDGET_EXHAUSTED_MSG,
            "routing_decision": "blocked:budget_exhausted",
            "budget_pool_used": None,
            "reasoning_steps":  reasoning_steps + ["cloud_standard_blocked:no_budget"],
            "timings":          {**state.get("timings", {}), "generation_ms": round((time.perf_counter() - _t0) * 1000)},
        }

    res = await cloud_response(
        synthesis_prompt,
        model="gpt-4o-mini",
        system_prompt=system_prompt,
    )

    # ── Post-call: deduct actual cost then log ────────────────────────────
    if isinstance(res, tuple):
        response_text, input_tokens, output_tokens = res
    else:
        response_text, input_tokens, output_tokens = res or "", 0, 0

    await deduct_cloud_actual(
        session_id=state["session_id"],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        pool=pool_used,
    )
    await log_route_event(
        session_id=state["session_id"],
        message_id=None,
        model_name="gpt-4o-mini",
        category=state.get("category", ""),
        confidence=state.get("confidence", 0.0),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        pool=pool_used,
    )

    reasoning_steps = list(state.get("reasoning_steps") or [])
    return {
        "response":         response_text,
        "routing_decision": "GPT-4o mini",
        "budget_pool_used": pool_used.value,
        "reasoning_steps":  reasoning_steps + ["cloud_standard_executed"],
        "timings":          {**state.get("timings", {}), "generation_ms": round((time.perf_counter() - _t0) * 1000)},
    }

async def cloud_complex_node(state: AgentState) -> dict:
    print("[LANGGRAPH]: Cloud Complex Path (GPT-4o)...")
    _t0 = time.perf_counter()

    msg = _get_message(state)
    history_block = _build_history_block(state)

    synthesis_prompt = f"""You are a helpful and friendly academic assistant.
        Answer the student's request with deep, multi-step reasoning where necessary.
        Support your answer with clear logical steps. Do not fabricate facts or references.
        {history_block}
        Student request: {msg}
    """
    system_prompt = (
        "You are an expert academic assistant capable of advanced reasoning. "
        "Provide thorough, well-structured, and accurate responses."
    )

    # pre-call pool check
    try:
        pool_used = await check_pool_available(session_id=state["session_id"])
    except BudgetExhaustedError:
        reasoning_steps = list(state.get("reasoning_steps") or [])
        return {
            "response":         _BUDGET_EXHAUSTED_MSG,
            "routing_decision": "blocked:budget_exhausted",
            "budget_pool_used": None,
            "reasoning_steps":  reasoning_steps + ["cloud_complex_blocked:no_budget"],
            "timings":          {**state.get("timings", {}), "generation_ms": round((time.perf_counter() - _t0) * 1000)},
        }

    res = await cloud_response(
        synthesis_prompt,
        model="gpt-4o",
        system_prompt=system_prompt,
    )

    if isinstance(res, tuple):
        response_text, input_tokens, output_tokens = res
    else:
        response_text, input_tokens, output_tokens = res or "", 0, 0

    await deduct_cloud_actual(
        session_id=state["session_id"],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        pool=pool_used,
    )
    await log_route_event(
        session_id=state["session_id"],
        message_id=None,
        model_name="gpt-4o",
        category=state.get("category", ""),
        confidence=state.get("confidence", 0.0),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        pool=pool_used,
    )

    reasoning_steps = list(state.get("reasoning_steps") or [])
    return {
        "response":         response_text,
        "routing_decision": "GPT-4o",
        "budget_pool_used": pool_used.value,
        "reasoning_steps":  reasoning_steps + ["cloud_complex_executed"],
        "timings":          {**state.get("timings", {}), "generation_ms": round((time.perf_counter() - _t0) * 1000)},
    }

async def tool_executor_node(state: AgentState) -> dict:
    print("[LANGGRAPH]: Tool Executor (cloud path) – selecting tools...")

    msg = _get_message(state)
    available_tools = list_tools()
    tools_description = "\n".join(
        [f"- {name}: {desc}" for name, desc in available_tools.items()]
    )

    tool_calls: list = []
    tool_results: dict = {}

    # If the frontend specified a tool directly, skip the LLM selection step.
    forced = state.get("forced_tool_name")
    if forced:
        tool_calls = [forced]
        print(f"[LANGGRAPH]: Tool pre-selected by frontend: {forced}")
    else:
        tool_select_prompt = f"""Available academic tools:
            {tools_description}

            Which of the tools above should be called to answer this request?
            Respond with a JSON array of tool names, e.g. ["summarize_document"] or [].
            Do NOT include tools that are not listed above.

            Student request: "{msg}"
        """
        raw = await local_response(tool_select_prompt)
        try:
            match = re.search(r"\[.*?\]", raw, re.DOTALL)
            tool_calls = json.loads(match.group(0)) if match else []
        except (json.JSONDecodeError, AttributeError):
            tool_calls = []

    print(f"[LANGGRAPH]: Tools selected: {tool_calls}")

    pool_used_for_synthesis: Optional[Pool] = None
    tool_timings: dict = {}
    _t0 = time.perf_counter()

    for tool_name in tool_calls:
        tool = get_tool(tool_name)
        if not tool:
            print(f"[LANGGRAPH]: Tool '{tool_name}' not found – skipping")
            continue

        # Route generate_quiz through shadow reserve; all others through visible/bonus
        is_quiz_tool = (tool_name == SHADOW_RESERVE_TOOL)

        if is_quiz_tool:
            try:
                await check_and_deduct_shadow(
                    session_id=state["session_id"],
                    input_tokens=0,
                    output_tokens=0,
                )
            except BudgetExhaustedError:
                tool_results[tool_name] = (
                    "Quiz generation is unavailable: the shadow reserve for this "
                    "session is exhausted."
                )
                print(f"[LANGGRAPH]: Shadow reserve exhausted for '{tool_name}'")
                continue
        else:
            try:
                pool_used_for_synthesis = await check_pool_available(
                    session_id=state["session_id"],
                )
            except BudgetExhaustedError:
                tool_results[tool_name] = (
                    "This tool cannot run: your session budget is exhausted."
                )
                print(f"[LANGGRAPH]: Budget exhausted for tool '{tool_name}'")
                continue

        _tool_t0 = time.perf_counter()
        try:
            result = await tool.execute(
                session_id=state["session_id"],
                user_input=msg,
                source_file_ids=state.get("source_file_ids") or [],
            )
            tool_results[tool_name] = result
            print(f"[LANGGRAPH]: Tool '{tool_name}' executed successfully")
        except Exception as exc:
            tool_results[tool_name] = f"Error: {exc}"
            print(f"[LANGGRAPH]: Tool '{tool_name}' failed: {exc}")
        tool_timings[tool_name] = round((time.perf_counter() - _tool_t0) * 1000)

    # Always bypass synthesis for these tools — even for errors/budget exhausted.
    reasoning_steps = list(state.get("reasoning_steps") or [])
    _base_timings = {**state.get("timings", {}), "generation_ms": round((time.perf_counter() - _t0) * 1000), "tools": tool_timings}

    if tool_calls == ["generate_quiz"]:
        quiz_str = tool_results.get("generate_quiz", "")
        if quiz_str:
            return {
                "tool_calls":       tool_calls,
                "tool_results":     tool_results,
                "response":         quiz_str,
                "routing_decision": "GPT-4o mini (tool path)",
                "budget_pool_used": "shadow",
                "reasoning_steps":  reasoning_steps + ["quiz_generated:direct_return"],
                "timings":          _base_timings,
            }

    if tool_calls == ["generate_flashcards"]:
        fc_str = tool_results.get("generate_flashcards", "")
        if fc_str:
            return {
                "tool_calls":       tool_calls,
                "tool_results":     tool_results,
                "response":         fc_str,
                "routing_decision": "llama3.2:3b (tool path)",
                "budget_pool_used": None,
                "reasoning_steps":  reasoning_steps + ["flashcards_generated:direct_return"],
                "timings":          _base_timings,
            }

    # ── Synthesis – GPT-4o mini with tool results as grounded context ─────
    tool_context = ""
    if tool_results:
        tool_context = "\n\nTool Results:\n" + "\n".join(
            f"- {name}: {result}" for name, result in tool_results.items()
        )

    synthesis_prompt = f"""You are a helpful and friendly academic assistant.
        Answer the student's request directly using the tool results provided below.
        Do not fabricate information not present in the tool results.
        Student request: {msg}{tool_context}
    """
    system_prompt = (
        "You are a knowledgeable academic assistant. "
        "Use the provided tool results to answer the student accurately. "
        "Do not invent information beyond what the tools returned."
    )

    # Synthesis step uses visible/bonus pool (it is a regular cloud call)
    if pool_used_for_synthesis is None:
        # Only quiz tool was called (shadow pool); synthesis still needs visible/bonus
        try:
            pool_used_for_synthesis = await check_pool_available(
                session_id=state["session_id"],
            )
        except BudgetExhaustedError:
            raw_results = "\n".join(f"{k}: {v}" for k, v in tool_results.items())
            reasoning_steps = list(state.get("reasoning_steps") or [])
            return {
                "tool_calls":       tool_calls,
                "tool_results":     tool_results,
                "response":         raw_results or _BUDGET_EXHAUSTED_MSG,
                "routing_decision": "GPT-4o mini (tool path – synthesis blocked)",
                "budget_pool_used": None,
                "reasoning_steps":  reasoning_steps + [f"tools_executed:{tool_calls}", "synthesis_blocked:no_budget"],
                "timings":          {**_base_timings, "generation_ms": round((time.perf_counter() - _t0) * 1000)},
            }

    res = await cloud_response(
        synthesis_prompt,
        model="gpt-4o-mini",
        system_prompt=system_prompt,
    )

    if isinstance(res, tuple):
        response_text, input_tokens, output_tokens = res
    else:
        response_text, input_tokens, output_tokens = res or "", 0, 0

    await deduct_cloud_actual(
        session_id=state["session_id"],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        pool=pool_used_for_synthesis,
    )
    await log_route_event(
        session_id=state["session_id"],
        message_id=None,
        model_name="gpt-4o-mini",
        category=state.get("category", ""),
        confidence=state.get("confidence", 0.0),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        pool=pool_used_for_synthesis,
    )

    reasoning_steps = list(state.get("reasoning_steps") or [])
    return {
        "tool_calls":       tool_calls,
        "tool_results":     tool_results,
        "response":         response_text,
        "routing_decision": "GPT-4o mini (tool path)",
        "budget_pool_used": pool_used_for_synthesis.value if pool_used_for_synthesis else None,
        "reasoning_steps":  reasoning_steps + [f"tools_executed: {tool_calls}"],
        "timings":          {**_base_timings, "generation_ms": round((time.perf_counter() - _t0) * 1000)},
    }

def route_decision(state: AgentState) -> str:
    category   = state.get("category", "informational")
    confidence = state.get("confidence", 0.5)

    # Tool-required always goes to the cloud tool executor
    if state.get("requires_tool"):
        return "tool_executor"

    # Document source mode always local
    if state.get("document_context"):
        return "local_generation"

    # Analytical unconditional cloud escalation
    if category == "analytical":
        return "cloud_complex"

    # Administrative threshold 0.70
    if category == "administrative":
        return (
            "local_generation"
            if confidence >= CONFIDENCE_THRESHOLD_ADMINISTRATIVE
            else "cloud_standard"
        )

    # Informational threshold 0.85
    return (
        "local_generation"
        if confidence >= CONFIDENCE_THRESHOLD_INFORMATIONAL
        else "cloud_standard"
    )

workflow = StateGraph(AgentState)

workflow.add_node("triage",           triage_node)
workflow.add_node("local_generation", local_generation_node)
workflow.add_node("cloud_standard",   cloud_standard_node)
workflow.add_node("cloud_complex",    cloud_complex_node)
workflow.add_node("tool_executor",    tool_executor_node)

workflow.set_entry_point("triage")

workflow.add_conditional_edges(
    "triage",
    route_decision,
    {
        "local_generation": "local_generation",
        "cloud_standard":   "cloud_standard",
        "cloud_complex":    "cloud_complex",
        "tool_executor":    "tool_executor",
    },
)

workflow.add_edge("local_generation", END)
workflow.add_edge("cloud_standard",   END)
workflow.add_edge("cloud_complex",    END)
workflow.add_edge("tool_executor",    END)

app_instance = workflow.compile()

async def routing_logic(
    text: str,
    session_id: int,
    session_history: list | None = None,
    document_context: str | None = None,
    source_file_ids: list[int] | None = None,
) -> dict:
    initial_state: AgentState = {
        "message":          text,
        "user_input":       text,           # legacy alias kept for direct ainvoke() callers
        "session_history":  (session_history or [])[-6:],
        "session_id":       session_id,
        "category":         "",
        "confidence":       0.0,
        "requires_tool":    False,
        "forced_tool_name": None,
        "document_context": document_context,
        "source_file_ids":  source_file_ids or [],
        "routing_decision": "",
        "response":         "",
        "budget_pool_used": None,
        "tool_calls":       [],
        "tool_results":     {},
        "reasoning_steps":  [],
    }
    final_state = await app_instance.ainvoke(initial_state)
    return {
        "response":         final_state["response"],
        "routing_decision": final_state["routing_decision"],
        "budget_pool_used": final_state.get("budget_pool_used"),
        "reasoning_steps":  final_state.get("reasoning_steps", []),
    }