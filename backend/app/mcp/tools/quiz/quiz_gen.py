from __future__ import annotations

import json

from services.db_con import get_cached_tool_output, get_session_file, get_files_by_ids, save_tool_output, get_quiz_attempts_for_session
from services.LLMs import cloud_response, local_response

# Condense document text to a manageable length using the local model
async def _summarise_for_quiz(doc_text: str) -> str:

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
    Return questions with correct_answer and explanation removed, plus a positional index.

    Both fields live solely in the tool_output cache and are only revealed
    after the student submits all answers via submit_quiz_answers.
    """
    return [
        {
            "index":    idx,
            "question": q.get("question", ""),
            "options":  q.get("options", {}),
        }
        for idx, q in enumerate(quiz_data)
    ]

# raise ValueError if any question dict is missing required fields or has invalid values
def _validate_quiz_schema(quiz_data: list[dict]) -> None:

    required = {"question", "options", "correct_answer"}
    opts     = {"A", "B", "C", "D"}

    for i, q in enumerate(quiz_data):
        if missing := required - q.keys():
            raise ValueError(f"Question {i} missing fields: {missing}")
        if not opts.issubset(q["options"].keys()):
            raise ValueError(f"Question {i} options must include A, B, C, D")
        if q["correct_answer"].upper() not in opts:
            raise ValueError(f"Question {i} correct_answer must be A, B, C or D")

# Takes the session ID and quiz parameters, returns a JSON string with either the quiz questions or an error message
async def generate_quiz(
    session_id: int,
    topic: str = "",
    question_count: int = 10,
    force_regen: bool = False,
    used_questions: list | None = None,
    file_ids: list[int] | None = None,
) -> str:

    if file_ids:
        # Multi-file source mode always regenerate so the quiz matches selection.
        force_regen = True
        source_files = [
            f for f in await get_files_by_ids(session_id, file_ids)
            if f.get("extracted_text")
        ]
        has_document = bool(source_files)
        file_row = None
        # scale question count with number of files
        if has_document:
            question_count = 10 + (len(source_files) - 1) * 5
        else:
            question_count = max(1, min(question_count, 10))
    else:
        source_files = None
        file_row = await get_session_file(session_id)
        has_document = bool(file_row and file_row.get("extracted_text"))
        question_count = max(1, min(question_count, 10))

    if not has_document and not topic.strip():
        return json.dumps({
            "error": "No document found and no topic provided. "
                     "Upload a document or specify a topic to generate a quiz."
        })

    # cache check, skip regeneration if have a cached quiz that’s still valid
    cached = await get_cached_tool_output(session_id, "generate_quiz")
    cache_valid = (not force_regen) and cached and (
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

    if source_files:
        # Multiple activated files combine then summarise
        per_file = max(2000, 8000 // len(source_files))
        combined_raw = "\n\n---\n\n".join(
            f"[{f['filename']}]\n{f['extracted_text'][:per_file]}"
            for f in source_files
        )
        summary = await _summarise_for_quiz(combined_raw)
        focus_line = f"Focus particularly on topics related to: {topic}.\n" if topic.strip() else ""
        source_block = f"{focus_line}Combined document summary:\n{summary}"
    elif has_document:
        summary = await _summarise_for_quiz(file_row["extracted_text"][:8000])
        focus_line = f"Focus particularly on topics related to: {topic}.\n" if topic.strip() else ""
        source_block = f"{focus_line}Document summary:\n{summary}"
    else:
        source_block = f"Topic: {topic}"

    # Build "avoid these" block when regenerating with prior questions
    avoid_block = ""
    if used_questions:
        avoid_lines = "\n".join(
            f"- {q['question']}"
            for q in used_questions
            if q.get("question")
        )
        avoid_block = (
            f"\nThe following questions have ALREADY been used — do NOT repeat them "
            f"or closely related questions. Cover different aspects:\n{avoid_lines}\n"
        )

    prompt = f"""Generate exactly {question_count} multiple-choice questions based on the content below.
        Questions must cover different aspects — do not cluster around a single point.
        {avoid_block}
        Return ONLY a valid JSON array with no markdown, no explanation, no preamble.
        Each element must follow this exact schema:
        {{
        "question":      "<question text>",
        "options":       {{"A": "<option>", "B": "<option>", "C": "<option>", "D": "<option>"}},
        "correct_answer": "<A|B|C|D>",
        "explanation":   "<one sentence explaining why the correct answer is right>"
        }}

        {source_block}"""

    system_prompt = (
        f"You are a quiz generator. Respond ONLY with a valid JSON array of exactly {question_count} elements. "
        "No markdown fences, no explanation, no text before or after the array."
    )

    raw, _, _ = await cloud_response(prompt, model="gpt-4o-mini", system_prompt=system_prompt)

    # Validate and parse the quiz data, ensuring it adheres to the expected schema. Return an error message if validation fails
    try:
        clean     = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        quiz_data = json.loads(clean)
        if not isinstance(quiz_data, list) or not quiz_data:
            raise ValueError("Expected a non-empty JSON array")
        quiz_data = quiz_data[:question_count]
        _validate_quiz_schema(quiz_data)
    except (json.JSONDecodeError, ValueError) as exc:
        return json.dumps({"error": f"Quiz generation failed: {exc}", "raw": raw})

    saved = await save_tool_output(session_id, "generate_quiz", quiz_data)

    return json.dumps({
        "tool_output_id": saved["id_tool"],
        "questions":      _strip_answers(quiz_data),
    })