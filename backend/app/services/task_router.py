"""
task_router.py
--------------
LangGraph routing pipeline for the cost-adaptive hybrid assistant (thesis §3.4).

Graph topology
--------------
triage → [route_decision] → local_generation   (llama3.2:3b, zero cost)
                          → cloud_standard      (GPT-4o mini, visible/bonus pool)
                          → cloud_complex       (GPT-4o, visible/bonus pool)
                          → tool_executor       (GPT-4o mini; quiz-gen → shadow pool)

Budget enforcement is delegated entirely to cost_tracker.py.
No monetary logic lives in this file.
"""

from __future__ import annotations

import json
import re
from typing import TypedDict, Literal, Optional

from langgraph.graph import StateGraph, END  # type: ignore

from services.LLMs import local_response, cloud_response
from services.tools import get_tool, list_tools
from services.cost_tracker import (
    Pool,
    BudgetExhaustedError,
    check_pool_available,
    deduct_cloud_actual,
    check_and_deduct_shadow,
    log_route_event,
)

# ---------------------------------------------------------------------------
# Confidence threshold constants (thesis §3.4.2 – Confidence Threshold Policy)
#
# Administrative tasks have a bounded, predictable output structure with
# low factual-recall risk → threshold 0.70.
#
# Informational tasks carry higher factual risk; llama3.2:3b scores 63.4% on
# MMLU, so a stricter threshold guards against domain-specific errors → 0.85.
# The 15-point gap encodes the asymmetric risk profiles of the two categories.
# ---------------------------------------------------------------------------
CONFIDENCE_THRESHOLD_ADMINISTRATIVE: float = 0.70
CONFIDENCE_THRESHOLD_INFORMATIONAL:  float = 0.85

# Tool name that draws from the shadow reserve (thesis §3.4.3, §3.5.3)
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Agent State
# ---------------------------------------------------------------------------
class AgentState(TypedDict):
    # ── backward-compatible alias (original field name used by FastAPI) ──
    user_input: str

    # ── core routing fields (thesis §3.4.1) ─────────────────────────────
    message:          str    # raw student input (preferred name)
    session_history:  list   # last ≤6 conversation turns
    session_id:       int    # FK → session.id_session (required for budget checks)
    category:         str    # "administrative" | "informational" | "analytical"
    confidence:       float  # 0.0–1.0, produced by llama3.2:3b classifier
    requires_tool:    bool   # True → route to tool_executor regardless of category
    routing_decision: str    # which path/model was selected (set by conditional edge)
    response:         str    # final answer returned to the student

    # ── budget transparency fields (thesis §3.4.3) ───────────────────────
    budget_pool_used:  Optional[str]   # "visible" | "bonus" | "shadow" | None
    forced_tool_name:  Optional[str]   # set by the frontend to bypass llama tool selection

    # ── evaluation / transparency fields ────────────────────────────────
    tool_calls:       list   # tool names invoked during this turn
    tool_results:     dict   # tool name → output mapping
    reasoning_steps:  list   # breadcrumb trail for debugging / evaluation


# ---------------------------------------------------------------------------
# Node 1 – Triage (classification)
#
# Uses llama3.2:3b (local, zero cost) to classify the student message into one
# of three task categories and produce a confidence score plus a tool-need flag.
# 100% structured JSON compliance at Q4_K_M quantisation (thesis §2.1.1).
# (Sections 3.1.2, 3.4.1)
# ---------------------------------------------------------------------------
async def triage_node(state: AgentState) -> dict:
    """
    llama3.2:3b classifies the student query into:
      category      : administrative | informational | analytical
      confidence    : float 0–1
      requires_tool : bool (True when summarise_document / generate_quiz /
                             create_schedule tool output is expected)

    Category definitions (thesis §3.4.1):
      administrative  – scheduling, reminders, deadlines, study plans
      informational   – explain, summarise, define, find, generate quiz/flashcard
      analytical      – analyse, evaluate, compare, debug, write/draft argument

    Classification is local-only (llama3.2:3b via Ollama) and carries no
    monetary cost; the budget check only applies from Node 2 onwards.
    """
    # Frontend explicitly flagged this as a tool call — skip LLM triage.
    if state.get("requires_tool"):
        print("[LANGGRAPH]: Triage skipped — force_tool pre-classified.")
        return {
            "category":        "informational",
            "confidence":      1.0,
            "requires_tool":   True,
            "reasoning_steps": ["triaged:pre_classified"],
        }

    print("[LANGGRAPH]: Triaging (llama3.2:3b classification)...")

    msg = _get_message(state)

    prompt = f"""Classify the student query below and respond with ONLY a valid JSON object.

        Category definitions:
        "administrative" - scheduling, reminders, deadlines, study plans
        "informational"  - explain, summarise, define, find, generate quiz or flashcard
        "analytical"     - analyse, evaluate, compare, debug, write or draft argument

        Required JSON format (no extra keys, no markdown):
        {{
          "category": "administrative" | "informational" | "analytical",
          "confidence": <float 0.0-1.0>,
          "requires_tool": <true | false>
        }}

        requires_tool is true only when the query explicitly needs the
        summarize_document, generate_quiz, or create_schedule tool.

        Student query: "{msg}"
    """
    system_prompt = (
        "You are a strict classifier. Respond ONLY with a valid JSON object. "
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
        # Safe fallback: treat as informational with sub-threshold confidence
        # to ensure uncertain queries escalate rather than stay local.
        category = "informational"
        confidence = 0.5
        requires_tool = False

    print(
        f"[LANGGRAPH]: category={category}, confidence={confidence:.2f}, "
        f"requires_tool={requires_tool}"
    )
    return {
        "category":        category,
        "confidence":      confidence,
        "requires_tool":   requires_tool,
        "reasoning_steps": ["triaged"],
    }


# ---------------------------------------------------------------------------
# Node 2 – Local generation (llama3.2:3b)
#
# Handles Administrative tasks (confidence ≥ 0.70) and Informational tasks
# (confidence ≥ 0.85, no tool required).  Zero marginal cost; does not
# touch any budget pool.  Latency target < 200 ms first token (thesis §3.4.1).
# ---------------------------------------------------------------------------
async def local_generation_node(state: AgentState) -> dict:
    """
    llama3.2:3b synthesises the final response for low-complexity queries.
    Session history (last ≤6 turns) is included for multi-turn context.

    No budget pool is charged; log_route_event records cost = 0.0.
    """
    print("[LANGGRAPH]: Local Generation Path (llama3.2:3b)...")

    msg = _get_message(state)
    history_block = _build_history_block(state)

    synthesis_prompt = f"""You are a helpful and friendly academic assistant.
        Answer the student's request directly and naturally.
        Do not provide hypothetical examples, meta-commentary, or templates.
        {history_block}
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
        input_tokens=0,    # Ollama does not return token counts reliably
        output_tokens=0,
        pool=None,
    )

    reasoning_steps = list(state.get("reasoning_steps") or [])
    return {
        "response":         res,
        "routing_decision": "llama3.2:3b",
        "budget_pool_used": None,
        "reasoning_steps":  reasoning_steps + ["local_generation_executed"],
    }


# ---------------------------------------------------------------------------
# Node 3 – Cloud standard escalation (GPT-4o mini)
#
# Handles Informational tasks below the 0.85 confidence threshold, or
# Administrative tasks below 0.70.  Tool-calling accuracy 95–97% (§3.2.4).
# Charges the visible pool first, then the quiz-bonus pool.
# ---------------------------------------------------------------------------
async def cloud_standard_node(state: AgentState) -> dict:
    """
    GPT-4o mini path: low-confidence queries where llama3.2:3b self-reported
    uncertainty below the category-specific threshold (thesis §3.4.2).

    Budget check order:
      1. Visible pool  → if sufficient, charge and proceed.
      2. Bonus pool    → if sufficient, charge and proceed.
      3. Neither       → return a budget-exhausted message (no LLM call).
    """
    print("[LANGGRAPH]: Cloud Standard Path (GPT-4o mini)...")

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
    }


# ---------------------------------------------------------------------------
# Node 4 – Cloud complex escalation (GPT-4o)
#
# Reserved for all Analytical tasks (unconditional escalation – thesis §3.4.2).
# Tool-calling accuracy 97–99% (§3.2.4).  Charges visible/bonus pool.
# ---------------------------------------------------------------------------
async def cloud_complex_node(state: AgentState) -> dict:
    """
    GPT-4o path: analytical queries requiring multi-step reasoning where
    output quality and tool-argument accuracy outweigh the higher per-token cost.

    Analytical tasks escalate unconditionally regardless of confidence score
    (thesis §3.4.2: "Analytical tasks and all tool-bearing requests escalate
    unconditionally").
    """
    print("[LANGGRAPH]: Cloud Complex Path (GPT-4o)...")

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

    # ── Pre-call: check which pool is available (no deduction yet) ────────
    try:
        pool_used = await check_pool_available(session_id=state["session_id"])
    except BudgetExhaustedError:
        reasoning_steps = list(state.get("reasoning_steps") or [])
        return {
            "response":         _BUDGET_EXHAUSTED_MSG,
            "routing_decision": "blocked:budget_exhausted",
            "budget_pool_used": None,
            "reasoning_steps":  reasoning_steps + ["cloud_complex_blocked:no_budget"],
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
    }


# ---------------------------------------------------------------------------
# Node 5 – Tool executor (cloud-only path)
#
# Only reached when requires_tool=True.  llama3.2:3b is excluded from tool
# invocation because it does not support reliable function calling at 3B
# parameters (thesis §3.2.3, §3.5.1).
#
# Budget routing by tool type (thesis §3.4.3, §3.5.3):
#   generate_quiz        → shadow reserve (hidden pool, always available)
#   summarize_document   → visible/bonus pool
#   create_schedule      → visible/bonus pool
#
# Registered MCP tools: summarize_document, generate_quiz, create_schedule.
# ---------------------------------------------------------------------------
async def tool_executor_node(state: AgentState) -> dict:
    """
    Determines which FastMCP tools are needed, applies the correct budget pool
    per tool type, executes them, then synthesises the final response using
    GPT-4o mini with tool results as grounded context.

    Quiz generation is gated by the shadow reserve so it remains available
    even when the student's visible balance is zero.  All other tool calls
    are gated by the visible/bonus pool cascade.
    """
    print("[LANGGRAPH]: Tool Executor (cloud path) – selecting tools...")

    msg = _get_message(state)
    available_tools = list_tools()
    tools_description = "\n".join(
        [f"- {name}: {desc}" for name, desc in available_tools.items()]
    )

    tool_calls: list = []
    tool_results: dict = {}

    # ── Tool selection ────────────────────────────────────────────────────
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

    # ── Execute each selected tool with appropriate pool check ────────────
    pool_used_for_synthesis: Optional[Pool] = None

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

        try:
            result = await tool.execute(session_id=state["session_id"], user_input=msg)
            tool_results[tool_name] = result
            print(f"[LANGGRAPH]: Tool '{tool_name}' executed successfully")
        except Exception as exc:
            tool_results[tool_name] = f"Error: {exc}"
            print(f"[LANGGRAPH]: Tool '{tool_name}' failed: {exc}")

    # ── Quiz early-return: skip synthesis, send raw JSON to the frontend ────
    # The React QuizDisplay component renders interactive multiple-choice UI
    # directly from the structured data. Synthesis would only mangle the JSON.
    if tool_calls == ["generate_quiz"]:
        quiz_str = tool_results.get("generate_quiz", "")
        try:
            quiz_json = json.loads(quiz_str)
            if "questions" in quiz_json:
                reasoning_steps = list(state.get("reasoning_steps") or [])
                return {
                    "tool_calls":       tool_calls,
                    "tool_results":     tool_results,
                    "response":         quiz_str,
                    "routing_decision": "GPT-4o mini (tool path)",
                    "budget_pool_used": "shadow",
                    "reasoning_steps":  reasoning_steps + ["quiz_generated:direct_return"],
                }
        except (json.JSONDecodeError, TypeError):
            pass  # fall through to synthesis so the error text gets a reply

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
    }


# ---------------------------------------------------------------------------
# Conditional routing edge (thesis §3.4.2 – Confidence Threshold Policy)
#
# Priority order:
#   1. analytical        → cloud_complex  (unconditional, always needs deep reasoning)
#   2. requires_tool     → tool_executor  (cloud; quiz gen uses shadow pool inside node)
#   3. administrative, confidence ≥ 0.70 → local_generation
#   4. informational,   confidence ≥ 0.85 → local_generation
#   5. otherwise         → cloud_standard (confidence below category threshold)
#
# The asymmetric thresholds encode the asymmetric factual-error risk of each
# category (thesis §3.4.2): administrative errors are cosmetic and recoverable;
# informational errors may be accepted as fact by a student with no reference.
# ---------------------------------------------------------------------------
def route_decision(state: AgentState) -> str:
    category   = state.get("category", "informational")
    confidence = state.get("confidence", 0.5)

    # ── Analytical: unconditional cloud escalation ────────────────────────
    if category == "analytical":
        return "cloud_complex"

    # ── Tool-required: always goes to the cloud tool executor ─────────────
    if state.get("requires_tool"):
        return "tool_executor"

    # ── Administrative: threshold 0.70 ────────────────────────────────────
    if category == "administrative":
        return (
            "local_generation"
            if confidence >= CONFIDENCE_THRESHOLD_ADMINISTRATIVE
            else "cloud_standard"
        )

    # ── Informational (default): threshold 0.85 ───────────────────────────
    return (
        "local_generation"
        if confidence >= CONFIDENCE_THRESHOLD_INFORMATIONAL
        else "cloud_standard"
    )


# ---------------------------------------------------------------------------
# Graph construction (thesis §3.2.5 – LangGraph orchestration)
# Five nodes, one conditional edge from triage, four terminal edges.
# ---------------------------------------------------------------------------
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

# Compiled once at application startup inside the FastAPI lifespan context
# manager; FastAPI awaits the Runnable directly inside the request handler
# without a thread-pool wrapper (thesis §3.2.5).
app_instance = workflow.compile()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
async def routing_logic(
    text: str,
    session_id: int,
    session_history: list | None = None,
) -> dict:
    """
    Route a student message through the cost-adaptive hybrid pipeline.

    Parameters
    ----------
    text            : raw student input
    session_id      : active session PK (required for budget pool checks)
    session_history : list of {"role": "user"|"assistant", "content": str}
                      representing recent turns (trimmed to ≤6 internally).

    Returns
    -------
    dict with keys:
        response         : str   – the assistant's answer
        routing_decision : str   – which model/path handled the request
        budget_pool_used : str | None – "visible" | "bonus" | "shadow" | None
        reasoning_steps  : list  – breadcrumb trail for evaluation (thesis §4)
    """
    initial_state: AgentState = {
        "message":          text,
        "user_input":       text,           # legacy alias kept for direct ainvoke() callers
        "session_history":  (session_history or [])[-6:],
        "session_id":       session_id,
        "category":         "",
        "confidence":       0.0,
        "requires_tool":    False,
        "forced_tool_name": None,
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