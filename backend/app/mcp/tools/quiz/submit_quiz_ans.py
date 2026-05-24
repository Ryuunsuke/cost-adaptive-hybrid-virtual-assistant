from __future__ import annotations

import json

from services.db_con import get_tool_output_by_id
from services.cost_tracker import credit_quiz_bonus, save_quiz_attempt

# validate submitted answers against cached quiz, return per-question feedback and credit quiz_bonus if all correct
async def submit_quiz_answers(
    session_id: int,
    tool_output_id: int,
    answers: dict[str, str],
) -> str:
    
    # retrieve the quiz data from cache
    cached = await get_tool_output_by_id(tool_output_id)
    if not cached or cached["session_id"] != session_id:
        return json.dumps({"error": "Quiz not found. Please generate a quiz first."})

    quiz_data       = cached["output_json"]
    total_questions = len(quiz_data)

    # require an answer for every question before evaluating
    if len(answers) < total_questions:
        missing = total_questions - len(answers)
        return json.dumps({
            "error": (
                f"Please answer all {total_questions} questions before submitting. "
                f"{missing} answer(s) still missing."
            )
        })

    # evaluate submitted answers
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
            "explanation":    question.get("explanation") or question.get("options", {}).get(correct, ""),
        })

    # credit quiz_bonus if all correct
    if correct_count == total_questions:
        budget_reward = await credit_quiz_bonus(
            session_id=session_id,
            correct_count=correct_count,
            total_questions=total_questions,
            tool_output_id=tool_output_id,
            submitted_answers=answers,
        )
    else:
        # save the attempt for record-keeping and potential partial rewards
        await save_quiz_attempt(
            session_id=session_id,
            correct_count=correct_count,
            total_questions=total_questions,
            tool_output_id=tool_output_id,
            submitted_answers=answers,
        )
        budget_reward = 0

    is_perfect = correct_count == total_questions
    return json.dumps({
        "score":         correct_count,
        "total":         total_questions,
        "budget_reward": budget_reward,
        "results":       results,
        "regen_needed":  not is_perfect,
    })