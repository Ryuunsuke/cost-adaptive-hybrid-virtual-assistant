from __future__ import annotations

import json
from typing import Optional

from services.db_con import get_cached_tool_output, get_files_by_ids, get_session_file, save_tool_output
from services.LLMs import cloud_response

# session_id : active session PK
# focus      : optional aspect to focus the summary on
async def summarize_document(
    session_id: int,
    focus: Optional[str] = None,
    file_ids: Optional[list] = None,
) -> str:
 
    if file_ids:
        rows = await get_files_by_ids(session_id, file_ids)
        rows = [r for r in rows if r.get("extracted_text")]
        if not rows:
            return json.dumps({
                "error": "No document found for this session. Please upload a document first."
            })
        if len(rows) == 1:
            # If only one file, summarise the whole thing up to 6000 chars
            text = rows[0]["extracted_text"][:6000]
        else:
            # If multiple files, summarise the first 6000 chars of each with clear labels
            per_file = max(1500, 6000 // len(rows))
            parts = [f"[{r['filename']}]\n{r['extracted_text'][:per_file]}" for r in rows]
            text = "\n\n---\n\n".join(parts)
    else:
        # Session-wide cache
        file_row = await get_session_file(session_id)
        if not file_row or not file_row.get("extracted_text"):
            return json.dumps({
                "error": "No document found for this session. Please upload a document first."
            })
        cached = await get_cached_tool_output(session_id, "summarize_document")
        if cached and cached["created_at"] >= file_row["uploaded_at"]:
            return json.dumps(cached["output_json"])
        text = file_row["extracted_text"][:6000]


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

    try:
        clean        = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        summary_data = json.loads(clean)
    except (json.JSONDecodeError, ValueError) as exc:
        return json.dumps({"error": f"Summarisation failed: {exc}", "raw": raw})

    await save_tool_output(session_id, "summarize_document", summary_data)

    # JSON object:
    # {
    #   "title":      str,
    #   "key_points": [str, …],
    #   "summary":    str,
    #   "conclusion": str
    # }
    return json.dumps(summary_data)