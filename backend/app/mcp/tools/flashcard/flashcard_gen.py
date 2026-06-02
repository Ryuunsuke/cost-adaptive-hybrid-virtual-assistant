from __future__ import annotations

import json

from services.db_con import get_cached_tool_output, get_session_file, get_files_by_ids, save_tool_output
from services.LLMs import local_response

_CARD_COUNT = 10

# First words that indicate the model generated a question instead of a concept name
_BAD_TERM_STARTERS = frozenset({
    "why", "what", "how", "when", "where", "who", "which",
    "explain", "describe", "define", "list", "name",
})

def _validate_cards(cards: list[dict]) -> None:
    for i, card in enumerate(cards):
        term = card.get("term", "")
        defn = card.get("definition", "")
        if not term or not isinstance(term, str):
            raise ValueError(f"Card {i} missing or invalid 'term'")
        if not defn or not isinstance(defn, str):
            raise ValueError(f"Card {i} missing or invalid 'definition'")
        first_word = term.strip().split()[0].lower().rstrip("?")
        if first_word in _BAD_TERM_STARTERS:
            raise ValueError(
                f"Card {i} term '{term}' is a question word, not a concept name"
            )

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

    prompt = f"""Create exactly {_CARD_COUNT} flashcard pairs from the content below.

        Rules:
        - "term" must be the NAME of a concept, technique, or principle — a noun phrase of 1 to 6 words.
          Never use question words (Why, What, How, Explain, Describe, etc.) as the term.
        - "definition" must explain that specific term in 1 to 2 sentences.
        - Every definition must directly correspond to its term.

        Example of correct output:
        [{{"term": "Binary Search", "definition": "A divide-and-conquer algorithm that locates a target by repeatedly halving a sorted list. Time complexity is O(log n)."}}]

        Return ONLY a valid JSON array — no markdown, no explanation, no preamble.
        Each element must follow this exact schema:
        {{
        "term":       "<noun phrase naming the concept — 1 to 6 words>",
        "definition": "<concise explanation of that specific concept>"
        }}

        {source_block}"""

    system_prompt = (
        f"You are a flashcard generator. Respond ONLY with a valid JSON array of exactly "
        f"{_CARD_COUNT} elements. No markdown fences, no explanation, no text before or after the array. "
        f"Each term must be a concept name (noun phrase), never a question word."
    )

    raw = await local_response(prompt, system_prompt=system_prompt)

    # Retry if needed
    for attempt in range(2):
        try:
            clean = (raw or "").strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            # llama3.2:3b often truncates the closing ] — recover before parsing
            if clean.startswith("[") and not clean.rstrip().endswith("]"):
                clean = clean.rstrip().rstrip(",") + "\n]"
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
