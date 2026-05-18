"""
tools/quiz/generate_quiz.py
---------------------------
MCP tool: generate_quiz  (thesis §3.5.3)
Budget pool: shadow reserve — always available even when visible budget = 0.

Generates a multiple-choice quiz from the student's uploaded document or from
a topic string when no document is present.

Parameters
----------
topic          : subject string; used as sole source when no document is uploaded,
                 or as a focus hint when a document is present.
question_count : number of questions to generate (default 5, capped at 10).

Redo: every subsequent call returns the cached questions at zero cost.
"""

from __future__ import annotations

import json

from services.db_con import get_cached_tool_output, get_session_file, save_tool_output, get_quiz_attempts_for_session
from services.LLMs import cloud_response, local_response


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _summarise_for_quiz(doc_text: str) -> str:
    """
    Condense a document into ~1000 words using llama3.2:3b (free, local).

    Summarising first ensures quiz questions are distributed across the whole
    document rather than clustering around the opening paragraphs.  The
    summary also keeps the GPT-4o mini prompt within a comfortable context
    window without any truncation of key content.
    """
    prompt = (
        "Summarise the following academic document in approximately 1000 words. "
        "Cover all major sections and key concepts proportionally. "
        "Do not omit important facts, arguments, or conclusions.\n\n"
        f"Document:\n{doc_text}"
    )
    summary = await local_response(prompt)
    return summary or doc_text[:3000]   # fallback: truncated raw text


def _strip_answers(quiz_data: list[dict]) -> list[dict]:
    """
    Return questions with correct_answer removed and a positional index added.

    The frontend only ever receives this stripped version.  correct_answer
    lives solely in the tool_output cache until submit_quiz_answers is called.
    """
    return [
        {
            "index":    idx,
            "question": q.get("question", ""),
            "options":  q.get("options", {}),
        }
        for idx, q in enumerate(quiz_data)
    ]


def _validate_quiz_schema(quiz_data: list[dict]) -> None:
    """
    Raise ValueError if any question is missing required fields.

    Every question must have 'question', all four 'options' (A–D), and a
    'correct_answer' that is one of A, B, C, D.
    """
    required = {"question", "options", "correct_answer"}
    opts     = {"A", "B", "C", "D"}

    for i, q in enumerate(quiz_data):
        if missing := required - q.keys():
            raise ValueError(f"Question {i} missing fields: {missing}")
        if not opts.issubset(q["options"].keys()):
            raise ValueError(f"Question {i} options must include A, B, C, D")
        if q["correct_answer"].upper() not in opts:
            raise ValueError(f"Question {i} correct_answer must be A, B, C or D")


# ── Tool function ─────────────────────────────────────────────────────────────

async def generate_quiz(
    session_id: int,
    topic: str = "",
    question_count: int = 5,
) -> str:
    """
    Generate a multiple-choice quiz from an uploaded document or a topic string.

    If the session has an uploaded document, questions are drawn from it
    (topic narrows the focus if provided).  If no document is present, topic
    is used as the sole knowledge source and must be non-empty.

    Redo behaviour
    --------------
    Once generated, the quiz is cached in tool_output.  Every subsequent call
    returns the cached questions at zero cost.  Each attempt is recorded
    separately by submit_quiz_answers.

    Parameters
    ----------
    session_id     : active session PK
    topic          : subject string (required when no document is uploaded)
    question_count : number of questions to generate (default 5, max 10)
    """
    question_count = max(1, min(int(question_count), 10))

    file_row = await get_session_file(session_id)
    has_document = bool(file_row and file_row.get("extracted_text"))

    if not has_document and not topic.strip():
        return json.dumps({
            "error": "No document found and no topic provided. "
                     "Upload a document or specify a topic to generate a quiz."
        })

    # ── Cache hit — valid if no file (topic-based) or file hasn't changed ──
    cached = await get_cached_tool_output(session_id, "generate_quiz")
    cache_valid = cached and (
        not has_document or cached["created_at"] >= file_row["uploaded_at"]
    )
    if cache_valid:
        attempts = await get_quiz_attempts_for_session(session_id)
        perfect = next(
            (a for a in attempts
             if a["tool_output_id"] == cached["id_tool"]
             and a["score"] == a["total_questions"]),
            None,
        )
        if perfect:
            return json.dumps({
                "quiz_completed":  True,
                "score":           perfect["score"],
                "total_questions": perfect["total_questions"],
                "budget_reward":   int(perfect["budget_reward"]),
            })
        return json.dumps({
            "tool_output_id": cached["id_tool"],
            "questions":      _strip_answers(cached["output_json"]),
        })

    # ── Build knowledge source ────────────────────────────────────────────
    if has_document:
        summary = await _summarise_for_quiz(file_row["extracted_text"][:8000])
        focus_line = f"Focus particularly on topics related to: {topic}.\n" if topic.strip() else ""
        source_block = f"{focus_line}Document summary:\n{summary}"
    else:
        source_block = f"Topic: {topic}"

    prompt = f"""Generate exactly {question_count} multiple-choice questions based on the content below.
Questions must cover different aspects — do not cluster around a single point.

Return ONLY a valid JSON array with no markdown, no explanation, no preamble.
Each element must follow this exact schema:
{{
  "question":      "<question text>",
  "options":       {{"A": "<option>", "B": "<option>", "C": "<option>", "D": "<option>"}},
  "correct_answer": "<A|B|C|D>"
}}

{source_block}"""

    system_prompt = (
        f"You are a quiz generator. Respond ONLY with a valid JSON array of exactly {question_count} elements. "
        "No markdown fences, no explanation, no text before or after the array."
    )

    raw, _, _ = await cloud_response(prompt, model="gpt-4o-mini", system_prompt=system_prompt)

    # ── Parse and validate ────────────────────────────────────────────────
    try:
        clean     = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        quiz_data = json.loads(clean)
        if not isinstance(quiz_data, list) or not quiz_data:
            raise ValueError("Expected a non-empty JSON array")
        quiz_data = quiz_data[:question_count]
        _validate_quiz_schema(quiz_data)
    except (json.JSONDecodeError, ValueError) as exc:
        return json.dumps({"error": f"Quiz generation failed: {exc}", "raw": raw})

    # ── Persist full quiz (with answers) to cache ─────────────────────────
    saved = await save_tool_output(session_id, "generate_quiz", quiz_data)

    return json.dumps({
        "tool_output_id": saved["id_tool"],
        "questions":      _strip_answers(quiz_data),
    })