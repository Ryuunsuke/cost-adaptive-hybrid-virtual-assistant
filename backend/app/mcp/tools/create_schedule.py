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

    # Check cache first
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

    # Attempt to parse and validate the JSON response
    try:
        clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        schedule_data = json.loads(clean)
        if not isinstance(schedule_data, list):
            raise ValueError("Expected a JSON array")
    except (json.JSONDecodeError, ValueError) as exc:
        return json.dumps({"error": f"Schedule creation failed: {exc}", "raw": raw})

    await save_tool_output(session_id, "create_schedule", schedule_data)
    await save_schedule_entries(session_id, schedule_data)
    return json.dumps(schedule_data)