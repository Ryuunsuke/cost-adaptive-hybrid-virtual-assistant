"""
tools/summarize_document.py
----------------------------
MCP tool: summarize_document  (thesis §3.5.2)
Budget pool: visible / bonus pool cascade.

Produces a structured summary of the student's uploaded document.
Result is cached so re-requests within the same session cost nothing.
"""

from __future__ import annotations

import json
from typing import Optional

from services.db_con import get_cached_tool_output, get_session_file, save_tool_output
from services.LLMs import cloud_response


async def summarize_document(
    session_id: int,
    focus: Optional[str] = None,
) -> str:
    """
    Summarise the document uploaded in the current session.

    Produces a structured summary with a title, key points, a multi-paragraph
    summary, and a conclusion.  If a focus string is provided the summary
    centres on that aspect of the document.

    Result is cached in tool_output so re-requests return immediately at
    zero token cost.

    Parameters
    ----------
    session_id : active session PK
    focus      : optional aspect to focus the summary on (e.g. "methodology")

    Returns
    -------
    str
        JSON object:
        {
          "title":      str,
          "key_points": [str, …],
          "summary":    str,
          "conclusion": str
        }
        Returns {"error": str} if no document has been uploaded.
    """
    # ── Retrieve document (needed before cache check to compare timestamps) ─
    file_row = await get_session_file(session_id)
    if not file_row or not file_row.get("extracted_text"):
        return json.dumps({
            "error": "No document found for this session. Please upload a document first."
        })

    # ── Cache hit — only valid if built from the current file ─────────────
    cached = await get_cached_tool_output(session_id, "summarize_document")
    if cached and cached["created_at"] >= file_row["uploaded_at"]:
        return json.dumps(cached["output_json"])

    text              = file_row["extracted_text"][:6000]
    focus_instruction = f"\nFocus particularly on: {focus}" if focus else ""

    prompt = f"""Summarise the following academic document.{focus_instruction}

    Return ONLY a valid JSON object with no markdown, no preamble:
    {{
    "title":      "<inferred document title or 'Untitled'>",
    "key_points": ["<point 1>", "<point 2>", ...],
    "summary":    "<2-3 paragraph summary>",
    "conclusion": "<one paragraph conclusion>"
    }}

    Document:
    {text}"""

    system_prompt = (
        "You are an academic summariser. Respond ONLY with a valid JSON object. "
        "No markdown fences, no text before or after the object."
    )

    raw, _, _ = await cloud_response(prompt, model="gpt-4o-mini", system_prompt=system_prompt)

    # ── Parse ─────────────────────────────────────────────────────────────
    try:
        clean        = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        summary_data = json.loads(clean)
    except (json.JSONDecodeError, ValueError) as exc:
        return json.dumps({"error": f"Summarisation failed: {exc}", "raw": raw})

    await save_tool_output(session_id, "summarize_document", summary_data)
    return json.dumps(summary_data)