from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Optional

# Ensure both backend/app/ and backend/app/mcp/ are on sys.path.
# backend/app/ is needed so that `from services.LLMs import ...` works inside
# tool functions; mcp/ is needed so that `import tools` resolves to mcp/tools/.
_app_dir = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')
)
_mcp_dir = os.path.join(_app_dir, 'mcp')

for _d in [_app_dir, _mcp_dir]:
    if _d not in sys.path:
        sys.path.insert(0, _d)

from services.LLMs import local_response  # noqa: E402 — inserted after path setup


# ════════════════════════════════════════════════════════════════════════════
# Tool registry
# ════════════════════════════════════════════════════════════════════════════

_TOOL_REGISTRY: dict[str, dict] = {
    "generate_quiz": {
        "description": (
            "Generate a multiple-choice quiz from the student's uploaded document or "
            "from a topic string. Use when the student asks to be tested, quizzed, or "
            "wants practice questions. If a document is uploaded, questions come from it. "
            "Otherwise the topic string is used. Once generated, the same quiz is "
            "returned on every redo request at zero cost."
        ),
        "args": ["topic", "question_count"],
    },
    "submit_quiz_answers": {
        "description": (
            "Submit answers to a generated quiz and receive per-question feedback. "
            "Use when the student has answered all quiz questions and wants to see "
            "their results. ALL answers must be provided together — partial "
            "submissions are rejected. Correct answers are only revealed after "
            "all answers are submitted."
        ),
        "args": ["tool_output_id", "answers"],
    },
    "summarize_document": {
        "description": (
            "Summarise an uploaded document into key points and a structured "
            "overview. Use when the student asks to summarise, explain, or get "
            "the main points from their uploaded file."
        ),
        "args": ["focus"],
    },
    "create_schedule": {
        "description": (
            "Build a day-by-day study schedule from a list of topics and a "
            "deadline. Use when the student asks for a study plan, revision "
            "timetable, or schedule."
        ),
        "args": ["topics", "deadline", "daily_hours"],
    },
}


def list_tools() -> dict[str, str]:
    return {name: meta["description"] for name, meta in _TOOL_REGISTRY.items()}


def get_tool(tool_name: str) -> Optional["ToolProxy"]:
    if tool_name not in _TOOL_REGISTRY:
        return None
    return ToolProxy(tool_name, _TOOL_REGISTRY[tool_name])


# ════════════════════════════════════════════════════════════════════════════
# ToolProxy
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class ToolProxy:
    name: str
    meta: dict

    async def execute(self, session_id: int, user_input: str) -> str:
        args = await self._extract_args(user_input)
        args["session_id"] = session_id
        return await self._call_tool(**args)

    async def _extract_args(self, user_input: str) -> dict:
        if self.name == "generate_quiz":
            return await _extract_quiz_args(user_input)
        elif self.name == "submit_quiz_answers":
            return await _extract_submit_args(user_input)
        elif self.name == "summarize_document":
            return await _extract_summary_args(user_input)
        elif self.name == "create_schedule":
            return await _extract_schedule_args(user_input)
        return {}

    async def _call_tool(self, **kwargs) -> str:
        # Import mcp/tools/__init__.py as the tool function namespace.
        # mcp/ was added to sys.path above so `import tools` resolves there.
        import tools as _mcp_tools  # noqa: PLC0415
        tool_fn = getattr(_mcp_tools, self.name, None)
        if tool_fn is None:
            return json.dumps({"error": f"Tool '{self.name}' not found."})
        try:
            return await tool_fn(**kwargs)
        except Exception as exc:
            return json.dumps({"error": str(exc)})


# ════════════════════════════════════════════════════════════════════════════
# Argument extractors (local, zero-cost)
# ════════════════════════════════════════════════════════════════════════════

def _parse_json_from(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


async def _extract_quiz_args(user_input: str) -> dict:
    prompt = f"""Extract quiz generation parameters from the student message below.
Return ONLY a JSON object with exactly these keys:
  "topic":          string — the subject or topic to quiz on (empty string if not mentioned)
  "question_count": integer — number of questions requested (default 5 if not stated, max 10)

Student message: "{user_input}"
"""
    raw = await local_response(prompt)
    parsed = _parse_json_from(raw)
    topic = parsed.get("topic", "")
    if not isinstance(topic, str):
        topic = str(topic)
    try:
        count = int(parsed.get("question_count", 5))
        count = max(1, min(count, 10))
    except (TypeError, ValueError):
        count = 5
    return {"topic": topic, "question_count": count}


async def _extract_submit_args(user_input: str) -> dict:
    prompt = f"""Extract quiz submission parameters from the message below.
Return ONLY a JSON object with exactly these keys:
  "tool_output_id": integer — the quiz ID to submit answers for
  "answers": object — mapping of question index (string) to chosen option letter (A/B/C/D)

Example: {{"tool_output_id": 42, "answers": {{"0": "A", "1": "C", "2": "B"}}}}

Student message: "{user_input}"
"""
    raw = await local_response(prompt)
    parsed = _parse_json_from(raw)
    return {
        "tool_output_id": int(parsed.get("tool_output_id", 0)),
        "answers":        parsed.get("answers", {}),
    }


async def _extract_summary_args(user_input: str) -> dict:
    prompt = f"""Extract summary parameters from the student message below.
Return ONLY a JSON object with exactly this key:
  "focus": string or null — specific aspect to focus the summary on, null if not mentioned

Student message: "{user_input}"
"""
    raw = await local_response(prompt)
    parsed = _parse_json_from(raw)
    focus = parsed.get("focus")
    return {"focus": focus if focus and str(focus).lower() != "null" else None}


async def _extract_schedule_args(user_input: str) -> dict:
    prompt = f"""Extract study schedule parameters from the student message below.
Return ONLY a JSON object with exactly these keys:
  "topics":      array of strings — list of topics to study
  "deadline":    string — target date in YYYY-MM-DD format
  "daily_hours": number — hours available per day (default 2.0 if not stated)

Student message: "{user_input}"
"""
    raw = await local_response(prompt)
    parsed = _parse_json_from(raw)

    topics = parsed.get("topics", [user_input])
    if not isinstance(topics, list):
        topics = [str(topics)]

    return {
        "topics":      topics,
        "deadline":    parsed.get("deadline", "2025-12-31"),
        "daily_hours": float(parsed.get("daily_hours", 2.0)),
    }


# ════════════════════════════════════════════════════════════════════════════
# Direct function re-exports
# Eagerly import the mcp tools package so callers (e.g. main.py) can do
#   from services.tools import submit_quiz_answers
# without needing to know about mcp/ or sys.path manipulation.
# ════════════════════════════════════════════════════════════════════════════

import tools as _mcp_tools_pkg  # noqa: E402  (mcp/ added to sys.path above)

submit_quiz_answers = _mcp_tools_pkg.submit_quiz_answers
