"""
tools/quiz/submit_quiz_answers.py
----------------------------------
MCP tool: submit_quiz_answers  (thesis §3.5.3)
Budget pool: none — zero token cost, pure in-memory evaluation.

Validates all 10 answers in one submission, returns per-question feedback,
and credits quiz_bonus for each correct answer via cost_tracker.
Correct answers are only revealed AFTER all questions are answered.
"""

from __future__ import annotations

import json

from services.db_con import get_tool_output_by_id, get_quiz_attempts_for_session
from services.cost_tracker import credit_quiz_bonus, save_quiz_attempt


async def submit_quiz_answers(
    session_id: int,
    tool_output_id: int,
    answers: dict[str, str],
) -> str:
    """
    Validate all quiz answers and return per-question feedback.

    All 10 answers must be submitted together.  Partial submissions are
    rejected so that the student cannot probe for correct answers one at a
    time.  Correct answers are only included in the response after the full
    submission is received and evaluated.

    This call costs zero tokens: correct answers are retrieved from the
    tool_output cache and the comparison is performed in memory.

    Parameters
    ----------
    session_id     : active session PK
    tool_output_id : the id_tool value returned by generate_quiz
    answers        : {"0": "A", "1": "C", …}  (question index → chosen option)

    Returns
    -------
    str
        JSON object:
        {
          "score":         int,
          "total":         int,
          "budget_reward": int,   ← tokens credited (50 × correct count)
          "results": [
            {
              "index":          0,
              "question":       str,
              "your_answer":    "A",
              "correct_answer": "B",
              "is_correct":     false,
              "explanation":    str   ← text of the correct option
            },
            …
          ]
        }
        Returns {"error": str} on invalid input.
    """
    # ── Retrieve cached quiz (with correct answers) ───────────────────────
    cached = await get_tool_output_by_id(tool_output_id)
    if not cached or cached["session_id"] != session_id:
        return json.dumps({"error": "Quiz not found. Please generate a quiz first."})

    quiz_data       = cached["output_json"]
    total_questions = len(quiz_data)

    # ── Require all answers before revealing results ──────────────────────
    if len(answers) < total_questions:
        missing = total_questions - len(answers)
        return json.dumps({
            "error": (
                f"Please answer all {total_questions} questions before submitting. "
                f"{missing} answer(s) still missing."
            )
        })

    # ── Evaluate ──────────────────────────────────────────────────────────
    results       = []
    correct_count = 0

    for idx, question in enumerate(quiz_data):
        submitted  = str(answers.get(str(idx), "")).strip().upper()
        correct    = question.get("correct_answer", "").strip().upper()
        is_correct = submitted == correct
        if is_correct:
            correct_count += 1

        results.append({
            "index":          idx,
            "question":       question.get("question", ""),
            "your_answer":    submitted,
            "correct_answer": correct,
            "is_correct":     is_correct,
            "explanation":    question.get("options", {}).get(correct, ""),
        })

    # ── Credit quiz_bonus only for the first perfect completion ──────────
    existing_attempts = await get_quiz_attempts_for_session(session_id)
    already_perfect = any(
        a["tool_output_id"] == tool_output_id and a["score"] == a["total_questions"]
        for a in existing_attempts
    )

    if correct_count == total_questions and not already_perfect:
        # First perfect score — give full token reward.
        budget_reward = await credit_quiz_bonus(
            session_id=session_id,
            correct_count=correct_count,
            total_questions=total_questions,
            tool_output_id=tool_output_id,
            submitted_answers=answers,
        )
    else:
        # Retry or partial score — record attempt with no reward.
        await save_quiz_attempt(
            session_id=session_id,
            correct_count=correct_count,
            total_questions=total_questions,
            tool_output_id=tool_output_id,
            submitted_answers=answers,
        )
        budget_reward = 0

    return json.dumps({
        "score":         correct_count,
        "total":         total_questions,
        "budget_reward": budget_reward,
        "results":       results,
    })