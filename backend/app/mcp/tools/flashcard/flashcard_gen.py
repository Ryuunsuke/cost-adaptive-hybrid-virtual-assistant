from __future__ import annotations

import json

from services.db_con import get_cached_tool_output, get_session_file, get_files_by_ids, save_tool_output
from services.LLMs import local_response

_CARD_COUNT = 10

def _validate_cards(cards: list[dict]) -> None:
    for i, card in enumerate(cards):
        if not card.get("term") or not isinstance(card["term"], str):
            raise ValueError(f"Card {i} missing or invalid 'term'")
        if not card.get("definition") or not isinstance(card["definition"], str):
            raise ValueError(f"Card {i} missing or invalid 'definition'")

async def generate_flashcards(
    session_id: int,
    topic: str = "",
    file_ids: list[int] | None = None,
) -> str:
    # force_regen is True if specific file_ids are provided, so the flashcards are always fresh for the selected documents. If no file_ids, rely on cache if available and valid.
    force_regen = False
    if file_ids:
        force_regen = True
        source_files = [
            f for f in await get_files_by_ids(session_id, file_ids)
            if f.get("extracted_text")
        ]
        has_document = bool(source_files)
        file_row = None
    else:
        source_files = None
        file_row = await get_session_file(session_id)
        has_document = bool(file_row and file_row.get("extracted_text"))

    if not has_document and not topic.strip():
        return json.dumps({
            "error": "No document found and no topic provided. "
                     "Upload a document or specify a topic to generate flashcards."
        })

    # cache check, skip regeneration if have a cached quiz that’s still valid
    if not force_regen:
        cached = await get_cached_tool_output(session_id, "generate_flashcards")
        cache_valid = cached and (
            not has_document or cached["created_at"] >= file_row["uploaded_at"]
        )
        if cache_valid:
            return json.dumps({
                "tool_output_id": cached["id_tool"],
                "cards": cached["output_json"],
            })

    # source block construction with dynamic scaling based on number of files, prioritising local model context limits and relevance
    if source_files:
        per_file = max(1500, 4000 // len(source_files))
        combined = "\n\n---\n\n".join(
            f"[{f['filename']}]\n{f['extracted_text'][:per_file]}"
            for f in source_files
        )
        focus_line = f"Focus on topics related to: {topic}.\n" if topic.strip() else ""
        source_block = f"{focus_line}Document excerpts:\n{combined}"
    elif has_document:
        doc_excerpt = file_row["extracted_text"][:4000]
        focus_line = f"Focus on topics related to: {topic}.\n" if topic.strip() else ""
        source_block = f"{focus_line}Document excerpt:\n{doc_excerpt}"
    else:
        source_block = f"Topic: {topic}"

    prompt = f"""Create exactly {_CARD_COUNT} flashcard pairs based on the content below.
        Each card should capture one key concept, term, or fact.

        Return ONLY a valid JSON array — no markdown, no explanation, no preamble.
        Each element must follow this exact schema:
        {{
        "term":       "<the term, concept, or question>",
        "definition": "<the definition, explanation, or answer>"
        }}

        {source_block}"""

    system_prompt = (
        f"You are a flashcard generator. Respond ONLY with a valid JSON array of exactly "
        f"{_CARD_COUNT} elements. No markdown fences, no explanation, no text before or after the array."
    )

    raw = await local_response(prompt, system_prompt=system_prompt)

    # Retry if needed
    for attempt in range(2):
        try:
            clean = (raw or "").strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            cards = json.loads(clean)
            if not isinstance(cards, list) or not cards:
                raise ValueError("Expected a non-empty JSON array")
            cards = cards[:_CARD_COUNT]
            _validate_cards(cards)
            break
        except (json.JSONDecodeError, ValueError) as exc:
            if attempt == 0:
                raw = await local_response(prompt, system_prompt=system_prompt)
            else:
                return json.dumps({"error": f"Flashcard generation failed: {exc}", "raw": raw})

    # Add positional index
    indexed_cards = [{"index": i, **card} for i, card in enumerate(cards)]

    # cache the generated flashcards for future reference, associating with session and source document if applicable
    saved = await save_tool_output(session_id, "generate_flashcards", indexed_cards)

    return json.dumps({
        "tool_output_id": saved["id_tool"],
        "cards": indexed_cards,
    })
