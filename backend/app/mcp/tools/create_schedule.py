"""
tools/create_schedule.py
------------------------
MCP tool: create_schedule  (thesis §3.5.2)
Budget pool: visible / bonus pool cascade.

Builds a day-by-day study schedule from a list of topics and a deadline.
Result is cached so re-requests within the same session cost nothing.
"""

from __future__ import annotations

import json

from services.db_con import get_cached_tool_output, save_tool_output, save_schedule_entries
from services.LLMs import cloud_response


async def create_schedule(
    session_id: int,
    topics: list[str],
    deadline: str,
    daily_hours: float = 2.0,
) -> str:
    """
    Build a day-by-day study schedule from a list of topics and a deadline.

    Topics are distributed across the available days weighted by estimated
    complexity.  A revision day is inserted before the deadline when possible.

    Result is cached in tool_output so re-requests return immediately at
    zero token cost.

    Parameters
    ----------
    session_id  : active session PK
    topics      : list of topics to cover, e.g. ["Linear Algebra", "Calculus"]
    deadline    : target date in ISO format, e.g. "2025-12-01"
    daily_hours : hours available per day (default 2.0)

    Returns
    -------
    str
        JSON array of day objects:
        [
          {
            "day":    "YYYY-MM-DD",
            "topics": ["<subtopic or session description>"],
            "hours":  float,
            "notes":  str
          },
          …
        ]
    """
    # ── Cache hit ─────────────────────────────────────────────────────────
    cached = await get_cached_tool_output(session_id, "create_schedule")
    if cached:
        return json.dumps(cached["output_json"])

    topics_str = "\n".join(f"- {t}" for t in topics)

    prompt = f"""Create a day-by-day study schedule for the following topics.
Deadline: {deadline}
Daily study hours available: {daily_hours}

Topics to cover:
{topics_str}

Return ONLY a valid JSON array with no markdown, no preamble.
Each element must follow this exact schema:
{{
  "day":    "<YYYY-MM-DD>",
  "topics": ["<specific subtopic or session description>"],
  "hours":  <float>,
  "notes":  "<optional study tip for this session>"
}}

Distribute topics across the available days. Weight more complex topics with
more days. Include a revision day before the deadline if possible."""

    system_prompt = (
        "You are an academic study planner. Respond ONLY with a valid JSON array. "
        "No markdown fences, no explanation, no text before or after the array."
    )

    raw, _, _ = await cloud_response(prompt, model="gpt-4o-mini", system_prompt=system_prompt)

    # ── Parse ─────────────────────────────────────────────────────────────
    try:
        clean         = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        schedule_data = json.loads(clean)
        if not isinstance(schedule_data, list):
            raise ValueError("Expected a JSON array")
    except (json.JSONDecodeError, ValueError) as exc:
        return json.dumps({"error": f"Schedule creation failed: {exc}", "raw": raw})

    await save_tool_output(session_id, "create_schedule", schedule_data)
    await save_schedule_entries(session_id, schedule_data)
    return json.dumps(schedule_data)