# CAHVA Session Changelog
**Date:** 2026-05-19  
**Session scope:** Local LLM expansion — data-aware responses, flashcard tool, document source mode

---

## Feature A — Data-Aware Local Responses

### `backend/app/services/db_con.py`

- **`get_session_context_summary(session_id)` *(new)*:**  
  Fetches personalised context for the current session in a single round-trip:
  - Upcoming schedule entries (next 3 dates ≥ today, sorted ASC)
  - Last quiz attempt score and total
  - All uploaded filenames
  - Remaining visible tokens + quiz_bonus from the session row
  - Returns `{"schedule": [...], "last_quiz": {...}|None, "files": [...], "budget": {...}}`

- **`get_files_by_ids(session_id, file_ids)` *(new)*:**  
  Fetches `id_file`, `filename`, `extracted_text` for a list of file IDs scoped to the given session (prevents cross-session data access).

### `backend/app/services/task_router.py`

- **`AgentState`** — added `document_context: Optional[str]` field.
- **Import** — added `from services.db_con import get_session_context_summary`.
- **`local_generation_node`** — two sub-modes:
  - *Standard mode*: calls `get_session_context_summary` at node entry, builds a `_context_block` string (schedule, last quiz, uploaded files, remaining tokens), and prepends it to the synthesis prompt so local responses about "my schedule", "my tokens", etc. are personalised.
  - *Grounded mode* (when `document_context` is set): uses a strict grounded prompt instructing the model to answer only from the provided file excerpts.
- **`route_decision`** — added earliest check: if `state.get("document_context")`, return `"local_generation"` unconditionally before all category/confidence checks.
- **`routing_logic`** — added `document_context: str | None = None` parameter; wired into initial state.

---

## Feature B — Flashcard Generator (Local MCP Tool)

### `backend/app/mcp/tools/flashcard/__init__.py` *(new)*

Re-exports `generate_flashcards`.

### `backend/app/mcp/tools/flashcard/flashcard_gen.py` *(new)*

- **`generate_flashcards(session_id, topic="")`** — local-only tool (llama3.2:3b), zero cost.
- Produces 10 term/definition pairs from the uploaded document or a topic string.
- Cache logic matches `quiz_gen.py`: valid until a new file is uploaded, or indefinitely for topic-based cards.
- Validates each card has non-empty `term` and `definition`; retries once on parse failure.
- Adds positional `index` to each card before saving and returning.
- Output schema: `{ "tool_output_id": N, "cards": [{"index": 0, "term": "...", "definition": "..."}, …] }`

### `backend/app/mcp/tools/__init__.py`

- Added `from .flashcard import generate_flashcards` + `__all__` entry.

### `backend/app/services/tools/__init__.py`

- **Registry** — added `"generate_flashcards"` entry with `"args": ["topic"]`.
- **`_extract_flashcard_args(user_input)` *(new)*:** extracts `topic` string from the student message.
- **`_extract_args` dispatch** — wired `generate_flashcards` to `_extract_flashcard_args`.
- **Re-export** — `generate_flashcards = _mcp_tools_pkg.generate_flashcards`.

### `backend/app/services/task_router.py`

- **Triage prompt** — added `generate_flashcards` to the `requires_tool` examples.
- **`tool_executor_node`** — added flashcard early-return block (mirrors quiz early-return): if `tool_calls == ["generate_flashcards"]` and result contains `"cards"`, return raw JSON directly to the frontend without synthesis.
- **`BADGE_MAP`** — routing decision `"llama3.2:3b (tool path)"` maps to `"Local + tools"` badge.

### `frontend/cahva-react/src/FlashcardDisplay.jsx` *(new)*

- Props: `{ cards }` — array of `{index, term, definition}`.
- One card visible at a time; front = term, back = definition.
- Click/tap the card to flip it (CSS 3D rotation).
- Previous / Next navigation buttons; progress indicator "3 / 10".
- "click to flip" hint shown on the front face until first interaction.

### `frontend/cahva-react/src/FlashcardDisplay.css` *(new)*

- `.fc-scene`, `.fc-card`, `.fc-front`, `.fc-back` — CSS 3D perspective flip.
- `transform: rotateY(180deg)` on `.fc-card.is-flipped`; `backface-visibility: hidden`.
- Navigation row and progress indicator styles.

### `frontend/cahva-react/src/Message.jsx`

- **Import** `FlashcardDisplay`.
- **`tryParseFlashcards(text)` *(new)*:** detects JSON with `tool_output_id` + `cards[0].term`.
- **Detection order updated:** quiz → completed quiz → flashcard → schedule → plain text.
- **`BADGE_MAP`** — added `'llama3.2:3b (tool path)'` → `"Local + tools"` badge.

---

## Feature C — Document Source Mode

### `backend/app/main.py`

- **Import** — added `get_files_by_ids`.
- **`ChatRequest`** — added `source_file_ids: list[int] = []`.
- **`chat_endpoint`** — if `source_file_ids` is non-empty, calls `get_files_by_ids`, builds `document_context`:
  ```python
  per_file = max(400, 4000 // len(files))
  parts = [f"[{f['filename']}]\n{f['extracted_text'][:per_file]}" for f in files]
  document_context = "\n\n---\n\n".join(parts)
  ```
  Passes `document_context` in the graph initial state. If `source_file_ids` is empty, `document_context` is `None`.

### `frontend/cahva-react/src/FileUpload.jsx`

- **`onSourceChange` prop** — callback fired with the updated array of active `id_file` values whenever a toggle changes.
- **`activeSourceIds` state** — initialised from `localStorage` key `doc_source_${sessionId}` on mount.
- **`toggleSource(id_file)`** — adds or removes the file ID from `activeSourceIds`, persists to localStorage, calls `onSourceChange`.
- **Per-file "Source" toggle button** — shown only when the file has extracted text (`char_count > 0`); styled as a pill: dark green when `source-on`, grey when `source-off`.
- Placeholder text updated to include "Source Mode".

### `frontend/cahva-react/src/FileUpload.css`

- Added `.btn-source`, `.source-on`, `.source-off` — pill toggle button styles.

### `frontend/cahva-react/src/Chat.jsx`

- **`sourceFileIds` state** — initialised from `localStorage` key `doc_source_${sessionId}`; updated via `onSourceChange` from `FileUpload`.
- **`handleSendMessage`** — passes `source_file_ids: sourceFileIds` in every chat fetch body.
- **Header badge** — renders `<span className="source-mode-badge">📄 Source mode</span>` when `sourceFileIds.length > 0`.
- **`FileUpload`** — receives `onSourceChange={setSourceFileIds}`.

### `frontend/cahva-react/src/Chat.css`

- Added `.source-mode-badge` — green pill badge displayed in the chat header during source mode.

---

## Date: 2026-05-18  
**Session scope:** Feature additions, schema changes, and bug fixes

---

## Update — Quiz Improvements (same session)

### `backend/app/mcp/tools/quiz/quiz_gen.py`

- **Default `question_count` changed 5 → 10.**
- **`generate_quiz` signature** — added `force_regen: bool = False` and `used_questions: list | None = None` parameters.
  - `force_regen=True` bypasses the cache and always generates fresh questions (used by the regeneration endpoint).
  - `used_questions` is passed to the LLM prompt instructing it to avoid repeating those questions (lower effective weight on already-covered material).
- **Prompt updated** — each question now requests an `"explanation"` field: a one-sentence reason why the correct answer is right.
- **`_strip_answers`** — docstring clarified that `explanation` is also excluded from the frontend-bound stripped payload.

### `backend/app/mcp/tools/quiz/submit_quiz_ans.py`

- **`explanation` field** — now uses the stored `explanation` from the quiz data instead of the option text. Falls back to option text for quizzes generated before this change.
- **`regen_needed` flag** — response now includes `"regen_needed": true` when the score is not perfect, signalling the frontend to offer a regeneration button.

### `backend/app/services/cost_tracker.py`

- **`QUIZ_REWARD_PER_CORRECT = 50`** replaced with **`PERFECT_QUIZ_REWARD = 500`** — reward is now a flat 500 tokens for a perfect score, not per-question.
- **`credit_quiz_bonus`** — uses `PERFECT_QUIZ_REWARD` (500) instead of `correct_count * 50`.

### `backend/app/services/tools/__init__.py`

- **`_extract_quiz_args`** — default `question_count` changed from 5 → 10.
- **Re-export** — `generate_quiz` added alongside `submit_quiz_answers` so `main.py` can import it cleanly without runtime path manipulation.

### `backend/app/services/db_con.py`

- **`get_all_quiz_questions(session_id)` *(new)*:**  
  Returns every question dict from every `generate_quiz` tool_output for a session, ordered by `created_at ASC`. Used by the regeneration endpoint to build the "avoid repeating" prompt context.

### `backend/app/main.py`

- **`QuizRegenerateRequest`** Pydantic model added (`session_id`, `topic`, `question_count`).
- **`_generate_quiz`** imported from `services.tools`.
- **New endpoint `POST /api/quiz/regenerate`:** fetches all prior questions via `get_all_quiz_questions`, calls `generate_quiz` with `force_regen=True` and `used_questions`, returns the fresh quiz.

---

## Frontend (Quiz Improvements)

### `frontend/cahva-react/src/QuizDisplay.jsx`

- **`currentQuiz` state** — local state initialised from the `quiz` prop; updated in-place when "Try New Questions" regenerates the quiz, keeping the component self-contained.
- **Explanation display** — after submission, wrong-answer questions show a yellow explanation block below the options (`quiz-explanation`).
- **Score badge** — uses `.quiz-score-partial` (orange) when score is not perfect.
- **Result footer message** — perfect: "Perfect score! +500 bonus tokens earned."; partial: `"X / Y correct — review the explanations above."`.
- **Action row** — "Try Again" resets state to retry the same questions. "Try New Questions" (shown only on non-perfect) calls `POST /api/quiz/regenerate` and loads the fresh quiz.
- **Hook ordering** — all `useState` hooks moved unconditionally above the `if (completed)` early return (React rules compliance).

### `frontend/cahva-react/src/QuizDisplay.css`

- Added `.quiz-score-partial` — orange badge for partial scores.
- Added `.quiz-explanation` — yellow left-bordered explanation block per wrong question.
- Added `.explanation-label` — bold orange label.
- Added `.quiz-action-row` — flex row wrapper for Try Again / Try New Questions buttons.
- Added `.quiz-regen-btn` — solid blue button; disabled state goes grey.

---

## Bug Fixes (Quiz Improvements)

| # | File | Bug | Fix |
|---|------|-----|-----|
| 1 | `QuizDisplay.jsx` | React hooks called after conditional early return | Moved all `useState` hooks above the `completed` guard |
| 2 | `cost_tracker.py` | `QUIZ_REWARD_PER_CORRECT` reference left dangling after rename | Updated `credit_quiz_bonus` to use `PERFECT_QUIZ_REWARD` |

---

## Update — Try Again shuffles questions

### `frontend/cahva-react/src/QuizDisplay.jsx`

- **`displayQuestions` state** — holds the currently displayed order of questions; initialised from `quiz.questions` and updated independently of `currentQuiz`.
- **`shuffle` helper** — Fisher-Yates-style sort using `Math.random`.
- **Try Again** now calls `shuffle(currentQuiz.questions)` before resetting answers and results, so each retry presents questions in a randomised order.
- **Regeneration** resets `displayQuestions` to the new questions in their original order.
- **Question number** now shows the display position (`displayPos + 1`) rather than the original index, so numbers are always 1–10 in order regardless of shuffle.
- **"Try Again" after a perfect score** now calls `handleRegenerate` (same as "Try New Questions") so the student always gets a fresh set of questions with less weight on already-covered material. On a non-perfect score, "Try Again" still shuffles the same questions for practice.

---

## Update — Bonus tokens accumulate per perfect submission

### `backend/app/mcp/tools/quiz/submit_quiz_ans.py`

- **Removed `already_perfect` guard** — previously, the +500 bonus was only awarded on the *first* perfect score for a given `tool_output_id`. The session can accumulate multiple bonuses, so the guard is removed: every perfect submission (whether on the same questions via "Try Again" or on a new set via "Try New Questions") now increments `quiz_bonus` by +500.
- **Removed unused `get_quiz_attempts_for_session` import** (no longer needed now the guard is gone).

---

## Update — Bonus label shows once, then disappears

### `frontend/cahva-react/src/Stats.jsx`

- **`showBonus` state** — replaces the unconditional `quiz_bonus > 0` guard.
- **`localStorage` tracking** — key `bonus_seen_<sessionId>` stores the bonus amount at the last time the user opened Stats.
  - When `current_bonus > stored_seen_bonus`: show the label once and immediately update the stored value to `current_bonus` so the next Stats open hides it.
  - When `current_bonus <= stored_seen_bonus`: label is hidden; stored value is clamped down to `current_bonus` so that future bonuses earned above the current level will trigger the label again.
- **Cross-session behaviour** — each session has an independent key, so a session whose bonus was already viewed never re-shows the label unless new tokens are earned; a session where the bonus has been fully spent resets its key to 0 so new earnings are detected correctly.

---

## Backend

### `backend/app/services/db_con.py`

- **DDL — new table `schedule_entry`**  
  Added session-scoped study schedule table:
  ```
  id_entry, session_id (FK), date (DATE), topics (JSONB),
  duration_hours (NUMERIC 4,2), note (TEXT), created_at (TIMESTAMPTZ)
  ```

- **DDL — `route_log` schema change**  
  Replaced `model_name VARCHAR(80) NOT NULL` with `id_model INT REFERENCES model(id_model)`.  
  Column is nullable — quiz-reward rows have no associated model.

- **`bootstrap_schema` — one-time migration**  
  Added a `DO $$ … $$` PL/pgSQL block that runs on startup:
  - Detects the old `model_name` column via `information_schema.columns`
  - Adds `id_model` column, backfills from `model` table via join on `model_name`
  - Drops `model_name` column
  - Fully idempotent — safe on repeated restarts

- **`get_route_log_for_session`**  
  Changed bare `SELECT … FROM route_log` to `LEFT JOIN model` so `model_name`
  is still surfaced in results alongside `id_model`.

- **`get_session_cost_summary`**  
  Added `LEFT JOIN model` to support `local_requests` / `cloud_requests` filters
  after removing the `model_name` column. Added `id_model IS NOT NULL` guard to
  prevent NULL quiz-reward rows from being miscounted as cloud requests.

- **Import fix**  
  Added `from datetime import date as _date` (was unused `datetime, timezone`).

- **`_parse_schedule_row` helper** *(new)*  
  Converts asyncpg row → Python dict; handles JSONB topics deserialization,
  `DATE` → ISO string, `TIMESTAMPTZ` → ISO string, and `NUMERIC` duration_hours → `float`.

- **`save_schedule_entries(session_id, entries)` *(new)***  
  Bulk-inserts tool-generated schedule day entries; converts `"day"` string to
  `datetime.date` via `_date.fromisoformat()` (asyncpg requires a date object, not a string).

- **`get_schedule_entries(session_id)` *(new)***  
  Returns all schedule entries for a session sorted by date ascending.

- **`create_schedule_entry(...)` *(new)***  
  Inserts a single manually-created schedule entry; converts date string to `datetime.date`.

- **`update_schedule_entry(...)` *(new)***  
  Updates an existing entry by `id_entry + session_id`; converts date string to `datetime.date`.

- **`delete_schedule_entry(entry_id, session_id)` *(new)***  
  Deletes a schedule entry scoped to its session.

- **`get_user_schedule_entries(user_id)` *(new)***  
  Returns all schedule entries across every session owned by the user
  via `JOIN session ON session.user_id = $1`.

---

### `backend/app/services/cost_tracker.py`

- **`log_route_event` — INSERT updated**  
  Column `model_name` replaced with `id_model`; value resolved via inline subquery:
  ```sql
  (SELECT id_model FROM model WHERE model_name = $3)
  ```
  If the model name is not in the table, `id_model` is NULL (no crash).

- **`credit_quiz_bonus` — route_log INSERT updated**  
  Removed hardcoded `'quiz_answer_check'` model name string.
  Now inserts `id_model = NULL` explicitly, since quiz answer checking
  does not route through an LLM.

---

### `backend/app/mcp/tools/create_schedule.py`

- **Import** `save_schedule_entries` from `services.db_con`.
- After `save_tool_output`, also calls `await save_schedule_entries(session_id, schedule_data)`
  so tool-generated entries are immediately visible in the SchedulePanel and Calendar.

---

### `backend/app/mcp/tools/quiz/quiz_gen.py`

- **New parameters:**
  - `topic: str = ""` — subject string; used as sole source when no document is uploaded,
    or as a focus hint when a document is present.
  - `question_count: int = 5` — number of questions to generate (capped at 10).

- **Topic-only mode:** If no document is uploaded but `topic` is non-empty, generates
  a quiz from the topic alone without requiring a file upload.

- **Cache validation updated:** Cache is now considered valid when there is no file
  (topic-based quiz) OR when the cache is newer than the uploaded file.

- **Prompt updated** to use `question_count` variable instead of hardcoded `10`.

- **Completed card message** updated: removed "This quiz is now locked" language.

---

### `backend/app/services/tools/__init__.py`

- **Registry updated:** `generate_quiz` args changed from `[]` to `["topic", "question_count"]`.

- **`_extract_quiz_args(user_input)` *(new)*:**  
  Uses the local model to extract `topic` (string) and `question_count` (int, default 5,
  clamped to 1–10) from the student's message.

- **`_extract_args` updated:** `generate_quiz` case now calls `_extract_quiz_args`
  instead of returning `{}`.

---

### `backend/app/main.py`

- **Bug fix — Pydantic v2 validation:**  
  `CalendarEventCreate.end_date` and `CalendarEventUpdate.end_date` changed from
  `str = None` to `str | None = None`.  
  Root cause: Pydantic v2 does not allow `None` for a `str`-typed field even when
  the default is `None`; the frontend sends `"end_date": null`, causing a 422 error
  on every calendar save.

- **New Pydantic models:**
  - `ScheduleEntryCreate` — `session_id, date, topics: list[str], duration_hours, note`
  - `ScheduleEntryUpdate` — same fields

- **New imports from `db_con`:**  
  `get_schedule_entries`, `create_schedule_entry`, `update_schedule_entry`,
  `delete_schedule_entry`, `get_user_schedule_entries`

- **New endpoints:**
  | Method | Path | Description |
  |--------|------|-------------|
  | GET | `/api/schedule` | List all schedule entries for a session |
  | POST | `/api/schedule` | Create a new schedule entry |
  | PUT | `/api/schedule/{entry_id}` | Update an existing entry |
  | DELETE | `/api/schedule/{entry_id}` | Delete an entry |
  | GET | `/api/schedule/user` | All entries across all sessions for a user (used by Calendar) |

---

## Frontend

### `frontend/cahva-react/src/QuizDisplay.jsx`

- **Completed card:** Updated message — removed "This quiz is now locked — generate
  a new session to try again." Replaced with a neutral note about new quizzes
  drawing from different topics.

- **"Try Again" button *(new)*:**  
  Shown after any quiz submission result. Resets `answers`, `results`, and `error`
  state so the student can attempt the same questions again without refreshing.
  Wrapped with `.quiz-results-footer` for layout.

### `frontend/cahva-react/src/QuizDisplay.css`

- Added `.quiz-results-footer` flex column layout.
- Added `.quiz-retry-btn` — outlined blue button style with hover state.

---

### `frontend/cahva-react/src/SchedulePanel.jsx` *(new file)*

Session-scoped schedule management panel rendered in the Chat "Schedule" tab.

- Fetches entries from `GET /api/schedule?session_id=X` on mount.
- Displays entries sorted by date; each row shows date, topic tags, duration, and notes.
- **Add Entry** form: date picker, duration input, comma-separated topics, notes textarea.
  Disabled submit if date or topics are empty.
- **Edit** (inline): clicking ✏ on an entry populates the form with existing values.
- **Delete**: clicking ✕ calls `DELETE /api/schedule/{id}?session_id=X` and refreshes.

### `frontend/cahva-react/src/SchedulePanel.css` *(new file)*

Full stylesheet for SchedulePanel: panel layout, form grid, entry cards,
topic tags, edit/delete action buttons.

---

### `frontend/cahva-react/src/Chat.jsx`

- Imported `SchedulePanel`.
- Added **"Schedule" tab** between "Files" and "Stats" tabs.
- Renders `<SchedulePanel sessionId={sessionId} />` when the Schedule tab is active.

---

### `frontend/cahva-react/src/Message.jsx`

- **`tryParseSchedule(text)` *(new)*:**  
  Detects JSON array payloads where the first element has a `day` string key
  (output format of the `create_schedule` tool).

- **`ScheduleInline` component *(new)*:**  
  Read-only compact view of a tool-returned schedule embedded directly in the
  assistant message bubble. Shows count of days, a row per entry (date / topics / hours),
  and a tip to open the Schedule tab for editing.

- **`Message` component updated:**  
  Detection priority: quiz → completed quiz → schedule → plain text.

### `frontend/cahva-react/src/Message.css`

- Added `.schedule-inline` card wrapper (white, border, shadow).
- Added `.si-header`, `.si-list`, `.si-entry`, `.si-date`, `.si-topics`,
  `.si-hours`, `.si-tip` — compact schedule preview styles.

---

### `frontend/cahva-react/src/Calendar.jsx`

- Added `studyEntries` state, populated by `fetchStudyEntries` on mount.
- `fetchStudyEntries` calls `GET /api/schedule/user?user_id=X` to load all
  schedule entries across the user's sessions.
- **`studyOnDay(date)` helper *(new)*:**  
  Filters `studyEntries` for a given calendar cell by comparing
  `se.date + 'T00:00:00'` with the cell date.
- **Calendar grid updated:** each cell now renders study chips after event chips.
  Chip label shows first topic name + `+N` if more than one topic.
  `title` tooltip shows all topics, duration, and notes.

### `frontend/cahva-react/src/Calendar.css`

- Added `.cal-study-chip`:  
  Green background (`#e8f5e9`), green text (`#2e7d32`), left border accent (`#66bb6a`),
  `📖` prefix via `::before`, non-interactive cursor.  
  Visually distinct from the blue `.cal-event-chip`.

---

## Bug Fixes

| # | File | Bug | Fix |
|---|------|-----|-----|
| 1 | `main.py` | Pydantic v2 rejects `null` for `end_date: str = None`, causing 422 on all calendar saves without an end date | Changed to `str \| None = None` |
| 2 | `db_con.py` | asyncpg raises `DataError` when a plain string is passed for a `DATE` column — `'str' object has no attribute 'toordinal'` | Convert date strings to `datetime.date` via `_date.fromisoformat()` before all `schedule_entry` writes |
| 3 | `db_con.py` | PostgreSQL `NUMERIC` columns return Python `Decimal`; not always handled by serialization | Added explicit `float()` cast in `_parse_schedule_row` |
