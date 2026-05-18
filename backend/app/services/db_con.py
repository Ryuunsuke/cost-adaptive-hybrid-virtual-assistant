"""
db_con.py
---------
asyncpg database layer for the cost-adaptive academic assistant (thesis §3.3.3).

Responsibilities
----------------
* Manage the single application-wide asyncpg connection pool (init / get / close).
* Expose typed CRUD functions for every table in the nine-table schema:
      user, session, role, message, tool_output, file,
      quiz_attempt, route_log, model
* Provide the DDL bootstrap that creates all tables on first run.

What this file does NOT do
--------------------------
* No budget pool arithmetic  → cost_tracker.py
* No routing logic           → task_router.py
* No LLM calls               → services/ollama_router.py

Import convention
-----------------
    from db_con import get_db_pool          # used by cost_tracker.py
    from db_con import (                    # used by FastAPI endpoints
        create_session, get_session,
        create_message, get_session_history,
        save_tool_output, get_cached_tool_output,
        ...
    )
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

import asyncpg  # type: ignore

# ---------------------------------------------------------------------------
# Default budget constants used when creating a new session.
# Shadow reserve is sized to cover at least one full quiz generation call so
# the student can always access the quiz tool (thesis §3.4.3).
# ---------------------------------------------------------------------------
DEFAULT_DAILY_VISIBLE_LIMIT: float = 5000.0   # token-equivalent units
DEFAULT_SHADOW_RESERVE:       float = 500.0    # hidden; quiz-gen only

# ---------------------------------------------------------------------------
# Module-level pool singleton
# ---------------------------------------------------------------------------
_pool: Optional[asyncpg.Pool] = None


# ════════════════════════════════════════════════════════════════════════════
# Pool lifecycle
# ════════════════════════════════════════════════════════════════════════════

async def init_db_pool(dsn: Optional[str] = None) -> asyncpg.Pool:
    """
    Create and store the asyncpg connection pool.

    Call once from the FastAPI lifespan startup handler:

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            await init_db_pool()
            yield
            await close_db_pool()

    Parameters
    ----------
    dsn : PostgreSQL connection string.
        Falls back to the DATABASE_URL environment variable if not supplied.

    Returns
    -------
    asyncpg.Pool
        The newly created pool (also stored as the module singleton).
    """
    global _pool
    connection_string = dsn or os.environ.get("DATABASE_URL")
    if not connection_string:
        raise RuntimeError(
            "No database DSN supplied and DATABASE_URL is not set."
        )
    _pool = await asyncpg.create_pool(
        dsn=connection_string,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )
    return _pool


async def get_db_pool() -> asyncpg.Pool:
    """
    Return the application-wide connection pool.

    Raises
    ------
    RuntimeError
        If called before init_db_pool().
    """
    if _pool is None:
        raise RuntimeError(
            "Database pool is not initialised. "
            "Call await init_db_pool() during application startup."
        )
    return _pool


async def close_db_pool() -> None:
    """
    Gracefully close all pool connections.

    Call from the FastAPI lifespan shutdown handler.
    """
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


# ════════════════════════════════════════════════════════════════════════════
# DDL bootstrap
# ════════════════════════════════════════════════════════════════════════════

_DDL = """
-- Reference: model pricing (mirrors MODEL_COSTS in cost_tracker.py)
CREATE TABLE IF NOT EXISTS model (
    id_model        SERIAL       PRIMARY KEY,
    model_name      VARCHAR(80)  NOT NULL UNIQUE,
    input_cost_per_token  NUMERIC(14,10) NOT NULL DEFAULT 0,
    output_cost_per_token NUMERIC(14,10) NOT NULL DEFAULT 0
);

-- Seed the three models used by the router on first run
INSERT INTO model (model_name, input_cost_per_token, output_cost_per_token)
VALUES
    ('llama3.2:3b',  0,            0           ),
    ('gpt-4o-mini',  0.00000015,   0.00000060  ),
    ('gpt-4o',       0.00000500,   0.00001500  )
ON CONFLICT (model_name) DO NOTHING;

-- User accounts (proof-of-concept identity model – thesis §3.3.3)
-- Multiple users are supported; each is identified by username alone.
-- No password or email is stored. A new row is created automatically the
-- first time a username is submitted (upsert-on-connect pattern).
CREATE TABLE IF NOT EXISTS "user" (
    id_user     SERIAL      PRIMARY KEY,
    username    VARCHAR(80) NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Message role lookup (user | assistant | system)
CREATE TABLE IF NOT EXISTS role (
    id_role     SERIAL      PRIMARY KEY,
    role_name   VARCHAR(20) NOT NULL UNIQUE
);

INSERT INTO role (role_name)
VALUES ('user'), ('assistant'), ('system')
ON CONFLICT (role_name) DO NOTHING;

-- Session: the central record linking all budget pools (thesis §3.3.3)
-- Three-pool budget fields (thesis §3.4.3):
--   daily_visible_limit / visible_used  → shown in the UI status bar
--   shadow_reserve / shadow_used        → hidden; quiz-gen only
--   quiz_bonus                          → earned overflow; persists across resets
-- Reset anchor fields:
--   depleted_at    → recorded on first visible-pool exhaustion in a cycle
--   next_reset_at  → depleted_at + 24 h (or midnight UTC if never depleted)
CREATE TABLE IF NOT EXISTS session (
    id_session          SERIAL       PRIMARY KEY,
    user_id             INT          NOT NULL REFERENCES "user"(id_user) ON DELETE CASCADE,
    started_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Visible budget pool
    daily_visible_limit NUMERIC(12,4) NOT NULL DEFAULT 5000,
    visible_used        NUMERIC(12,4) NOT NULL DEFAULT 0,

    -- Shadow reserve pool (quiz generation only)
    shadow_reserve      NUMERIC(12,4) NOT NULL DEFAULT 500,
    shadow_used         NUMERIC(12,4) NOT NULL DEFAULT 0,

    -- Earned bonus pool (quiz answer rewards, persists across resets)
    quiz_bonus          NUMERIC(12,4) NOT NULL DEFAULT 0,

    -- Reset anchors
    depleted_at         TIMESTAMPTZ,
    next_reset_at       TIMESTAMPTZ
);

-- Messages exchanged within a session
CREATE TABLE IF NOT EXISTS message (
    id_message  SERIAL       PRIMARY KEY,
    session_id  INT          NOT NULL REFERENCES session(id_session) ON DELETE CASCADE,
    role_id     INT          NOT NULL REFERENCES role(id_role),
    content     TEXT         NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Uploaded student documents (one active file per session)
CREATE TABLE IF NOT EXISTS file (
    id_file         SERIAL       PRIMARY KEY,
    session_id      INT          NOT NULL REFERENCES session(id_session) ON DELETE CASCADE,
    filename        VARCHAR(255) NOT NULL,
    extracted_text  TEXT,
    uploaded_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Calendar events for the scheduling screen
CREATE TABLE IF NOT EXISTS calendar_event (
    id_event    SERIAL       PRIMARY KEY,
    user_id     INT          NOT NULL REFERENCES "user"(id_user) ON DELETE CASCADE,
    title       VARCHAR(255) NOT NULL,
    description TEXT,
    start_date  TIMESTAMPTZ  NOT NULL,
    end_date    TIMESTAMPTZ,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- MCP tool result cache (quiz, summary, schedule) – thesis §3.5
CREATE TABLE IF NOT EXISTS tool_output (
    id_tool         SERIAL       PRIMARY KEY,
    session_id      INT          NOT NULL REFERENCES session(id_session) ON DELETE CASCADE,
    tool_name       VARCHAR(80)  NOT NULL,
    output_json     JSONB        NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Per-request cost audit log – thesis §3.3.3, §3.4.3
-- Debit rows: cost > 0, pool = 'visible' | 'bonus' | 'shadow'
-- Credit rows (quiz rewards): cost < 0, pool = NULL, category = 'quiz_reward'
CREATE TABLE IF NOT EXISTS route_log (
    id_log      SERIAL       PRIMARY KEY,
    session_id  INT          NOT NULL REFERENCES session(id_session) ON DELETE CASCADE,
    message_id  INT          REFERENCES message(id_message) ON DELETE SET NULL,
    model_name  VARCHAR(80)  NOT NULL,
    category    VARCHAR(40)  NOT NULL,
    confidence  NUMERIC(4,3) NOT NULL DEFAULT 0,
    input_token INT          NOT NULL DEFAULT 0,
    output_token INT         NOT NULL DEFAULT 0,
    cost        NUMERIC(14,8) NOT NULL DEFAULT 0,
    pool        VARCHAR(10),     -- 'visible' | 'bonus' | 'shadow' | NULL
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Quiz attempt records for Chapter 4 evaluation analytics – thesis §3.5.3
CREATE TABLE IF NOT EXISTS quiz_attempt (
    id_attempt      SERIAL        PRIMARY KEY,
    session_id      INT           NOT NULL REFERENCES session(id_session) ON DELETE CASCADE,
    tool_output_id  INT           NOT NULL REFERENCES tool_output(id_tool) ON DELETE CASCADE,
    submitted_answers JSONB       NOT NULL,   -- {question_index: chosen_option}
    score           INT           NOT NULL,   -- correct answer count
    total_questions INT           NOT NULL,
    budget_reward   NUMERIC(10,4) NOT NULL DEFAULT 0,  -- tokens credited (50 × score)
    submitted_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- Session-scoped study schedule entries (tool-generated or manually created)
CREATE TABLE IF NOT EXISTS schedule_entry (
    id_entry        SERIAL        PRIMARY KEY,
    session_id      INT           NOT NULL REFERENCES session(id_session) ON DELETE CASCADE,
    date            DATE          NOT NULL,
    topics          JSONB         NOT NULL,
    duration_hours  NUMERIC(4,2)  NOT NULL DEFAULT 2.0,
    note            TEXT,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
"""


async def bootstrap_schema() -> None:
    """
    Run the DDL statements that create all nine tables on first run.

    Safe to call on every startup: every statement uses IF NOT EXISTS and
    INSERT … ON CONFLICT DO NOTHING, so repeated calls are idempotent.

    Call from the FastAPI lifespan startup handler, after init_db_pool():

        await init_db_pool()
        await bootstrap_schema()
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(_DDL)


# ════════════════════════════════════════════════════════════════════════════
# user table
# ════════════════════════════════════════════════════════════════════════════
# Proof-of-concept identity model (thesis §3.3.3).
# Multiple users are supported. Each user is identified by username only —
# no password or email is stored.  The upsert-on-connect pattern means the
# frontend calls upsert_user() once when a person enters their username;
# the function returns the existing row for returning users or creates a new
# one for first-time users, with no separate registration step required.

async def upsert_user(username: str) -> dict:
    """
    Return the user row for *username*, creating it if it does not exist.

    This implements the upsert-on-connect pattern described in thesis §3.3.3:
    the frontend prompts for a username once per connection; the backend calls
    this function, which resolves returning users and registers new ones in a
    single round-trip.

    Parameters
    ----------
    username : the string entered by the user in the frontend prompt.

    Returns
    -------
    dict
        The user row: {id_user, username, created_at}.
        is_new is not returned; callers that need to distinguish first-time
        users from returning users should compare created_at against NOW().
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO "user" (username)
            VALUES ($1)
            ON CONFLICT (username) DO UPDATE SET username = EXCLUDED.username
            RETURNING id_user, username, created_at
            """,
            username,
        )
    return dict(row)


async def get_user_by_id(user_id: int) -> Optional[dict]:
    """
    Fetch a user row by primary key.

    Returns None if the user does not exist.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            'SELECT id_user, username, created_at FROM "user" WHERE id_user = $1',
            user_id,
        )
    return dict(row) if row else None


async def get_user_by_username(username: str) -> Optional[dict]:
    """
    Fetch a user row by username.

    Returns None if the username is not found.
    Prefer upsert_user() for the connect flow; use this for read-only lookups.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            'SELECT id_user, username, created_at FROM "user" WHERE username = $1',
            username,
        )
    return dict(row) if row else None


# ════════════════════════════════════════════════════════════════════════════
# session table
# ════════════════════════════════════════════════════════════════════════════

async def create_session(
    user_id: int,
    daily_visible_limit: float = DEFAULT_DAILY_VISIBLE_LIMIT,
    shadow_reserve: float      = DEFAULT_SHADOW_RESERVE,
) -> dict:
    """
    Create a new chat session for *user_id*.

    A midnight-UTC reset anchor is set immediately so that students who never
    exhaust their visible budget still receive a daily replenishment (thesis §3.4.3).

    Parameters
    ----------
    user_id             : FK → user.id_user
    daily_visible_limit : token-equivalent units shown in the UI status bar
    shadow_reserve      : hidden allocation reserved exclusively for quiz generation

    Returns
    -------
    dict
        Full session row including all budget fields.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO session (
                user_id, daily_visible_limit, shadow_reserve,
                visible_used, shadow_used, quiz_bonus,
                next_reset_at
            )
            VALUES ($1, $2, $3, 0, 0, 0,
                    DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')
                    + INTERVAL '1 day')
            RETURNING *
            """,
            user_id,
            daily_visible_limit,
            shadow_reserve,
        )
    return dict(row)


async def get_session(session_id: int) -> Optional[dict]:
    """
    Fetch the full session row by primary key.

    Returns None if the session does not exist.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM session WHERE id_session = $1",
            session_id,
        )
    return dict(row) if row else None


async def get_user_sessions(user_id: int) -> list[dict]:
    """
    Return all sessions belonging to *user_id*, newest first.

    Used by the session history panel in the React UI.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM session WHERE user_id = $1 ORDER BY started_at DESC",
            user_id,
        )
    return [dict(r) for r in rows]


async def delete_session(session_id: int) -> None:
    """
    Permanently delete a session and all its messages (CASCADE).

    Called when the user removes a session from the session list in the UI.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM session WHERE id_session = $1",
            session_id,
        )


async def get_sessions_due_for_reset() -> list[int]:
    """
    Return the id_session of every session whose next_reset_at ≤ NOW().

    Called by the FastAPI background polling task every 60 seconds.
    The task passes each returned id to cost_tracker.run_daily_reset().

    Returns
    -------
    list[int]
        Session IDs that need their visible_used and shadow_used zeroed.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id_session
            FROM   session
            WHERE  next_reset_at IS NOT NULL
              AND  next_reset_at <= NOW()
            """,
        )
    return [r["id_session"] for r in rows]


# ════════════════════════════════════════════════════════════════════════════
# role table
# ════════════════════════════════════════════════════════════════════════════

async def get_role_by_name(role_name: str) -> Optional[dict]:
    """
    Return the role row for *role_name* ('user' | 'assistant' | 'system').

    Returns None if the role is not found (should not happen after bootstrap).
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id_role, role_name FROM role WHERE role_name = $1",
            role_name,
        )
    return dict(row) if row else None


# ════════════════════════════════════════════════════════════════════════════
# message table
# ════════════════════════════════════════════════════════════════════════════

async def create_message(
    session_id: int,
    role_name: str,
    content: str,
) -> dict:
    """
    Persist one message turn and return the new row.

    The role is resolved by name ('user' | 'assistant' | 'system') so callers
    do not need to know the internal role_id.

    Parameters
    ----------
    session_id : FK → session.id_session
    role_name  : 'user' | 'assistant' | 'system'
    content    : raw message text

    Returns
    -------
    dict
        The new message row (id_message, session_id, role_id, content, created_at).

    Raises
    ------
    ValueError
        If role_name is not found in the role table.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        role_row = await conn.fetchrow(
            "SELECT id_role FROM role WHERE role_name = $1", role_name
        )
        if role_row is None:
            raise ValueError(f"Unknown role: '{role_name}'")

        row = await conn.fetchrow(
            """
            INSERT INTO message (session_id, role_id, content)
            VALUES ($1, $2, $3)
            RETURNING id_message, session_id, role_id, content, created_at
            """,
            session_id,
            role_row["id_role"],
            content,
        )
    return dict(row)


async def get_session_history(
    session_id: int,
    limit: int = 6,
) -> list[dict]:
    """
    Return the most recent *limit* message turns for *session_id*.

    Each dict includes role_name (resolved via JOIN) so callers can pass the
    result directly to the session_history field in AgentState.

    Returns
    -------
    list[dict]
        Messages in chronological order, each with keys:
        id_message, role (str), content, created_at.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT m.id_message,
                   r.role_name  AS role,
                   m.content,
                   m.created_at
            FROM   message m
            JOIN   role    r ON r.id_role = m.role_id
            WHERE  m.session_id = $1
            ORDER  BY m.created_at DESC
            LIMIT  $2
            """,
            session_id,
            limit,
        )
    # Reverse so the list is chronological (oldest first)
    return [dict(r) for r in reversed(rows)]


# ════════════════════════════════════════════════════════════════════════════
# file table
# ════════════════════════════════════════════════════════════════════════════

async def save_file(
    session_id: int,
    filename: str,
    extracted_text: Optional[str] = None,
) -> dict:
    """
    Persist an uploaded document for *session_id*.

    Only one file is expected per session (the tools use get_session_file to
    look it up).  Multiple uploads are allowed; the tools always use the most
    recently uploaded file (get_session_file returns the newest row).

    Parameters
    ----------
    session_id     : FK → session.id_session
    filename       : original upload filename
    extracted_text : plain-text content extracted from the document (may be
                     None if extraction is deferred or the file is binary).

    Returns
    -------
    dict
        The new file row (id_file, session_id, filename, extracted_text, uploaded_at).
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO file (session_id, filename, extracted_text)
            VALUES ($1, $2, $3)
            RETURNING id_file, session_id, filename, extracted_text, uploaded_at
            """,
            session_id,
            filename,
            extracted_text,
        )
    return dict(row)


async def get_session_file(session_id: int) -> Optional[dict]:
    """
    Return the most recently uploaded file for *session_id*.

    Used by the quiz-generation and summarise-document tools to obtain
    extracted_text without requiring the caller to pass the file explicitly.

    Returns None if no file has been uploaded for this session.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id_file, session_id, filename, extracted_text, uploaded_at
            FROM   file
            WHERE  session_id = $1
            ORDER  BY uploaded_at DESC
            LIMIT  1
            """,
            session_id,
        )
    return dict(row) if row else None


async def get_session_files(session_id: int) -> list[dict]:
    """Return all files uploaded for *session_id*, newest first."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id_file, session_id, filename, extracted_text, uploaded_at
            FROM   file
            WHERE  session_id = $1
            ORDER  BY uploaded_at DESC
            """,
            session_id,
        )
    return [dict(r) for r in rows]


async def update_file_text(file_id: int, extracted_text: str) -> None:
    """
    Backfill extracted_text on an existing file row.

    Called when text extraction is performed asynchronously after the initial
    upload (e.g. after a PDF parsing worker completes).
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE file SET extracted_text = $1 WHERE id_file = $2",
            extracted_text,
            file_id,
        )


# ════════════════════════════════════════════════════════════════════════════
# tool_output table  (MCP tool result cache)
# ════════════════════════════════════════════════════════════════════════════

async def save_tool_output(
    session_id: int,
    tool_name: str,
    output_data: dict | list,
) -> dict:
    """
    Persist a tool result as JSONB and return the new cache row.

    Subsequent calls to get_cached_tool_output within the same session return
    this row instead of re-invoking the LLM, avoiding redundant token spend
    (thesis §3.5).

    Parameters
    ----------
    session_id  : FK → session.id_session
    tool_name   : 'generate_quiz' | 'summarize_document' | 'create_schedule'
    output_data : the structured result from the MCP tool (dict or list)

    Returns
    -------
    dict
        The new tool_output row (id_tool, session_id, tool_name, output_json, created_at).
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO tool_output (session_id, tool_name, output_json)
            VALUES ($1, $2, $3)
            RETURNING id_tool, session_id, tool_name, output_json, created_at
            """,
            session_id,
            tool_name,
            json.dumps(output_data),
        )
    result = dict(row)
    # Deserialise output_json so callers receive a Python object, not a string
    result["output_json"] = json.loads(result["output_json"])
    return result


async def get_cached_tool_output(
    session_id: int,
    tool_name: str,
) -> Optional[dict]:
    """
    Return the most recent cached result for *tool_name* within *session_id*.

    The quiz answer-submission endpoint uses this to retrieve stored correct
    answers without an LLM call (thesis §3.5.3).

    Returns None if no cached output exists for this tool in this session.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id_tool, session_id, tool_name, output_json, created_at
            FROM   tool_output
            WHERE  session_id = $1
              AND  tool_name  = $2
            ORDER  BY created_at DESC
            LIMIT  1
            """,
            session_id,
            tool_name,
        )
    if row is None:
        return None
    result = dict(row)
    result["output_json"] = json.loads(result["output_json"])
    return result


async def get_tool_output_by_id(tool_output_id: int) -> Optional[dict]:
    """
    Fetch a specific tool_output row by primary key.

    Used by credit_quiz_bonus in cost_tracker.py to verify the tool_output_id
    FK before inserting a quiz_attempt row.

    Returns None if the row does not exist.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id_tool, session_id, tool_name, output_json, created_at
            FROM   tool_output
            WHERE  id_tool = $1
            """,
            tool_output_id,
        )
    if row is None:
        return None
    result = dict(row)
    result["output_json"] = json.loads(result["output_json"])
    return result


# ════════════════════════════════════════════════════════════════════════════
# quiz_attempt table
# ════════════════════════════════════════════════════════════════════════════

async def get_quiz_attempts_for_session(session_id: int) -> list[dict]:
    """
    Return all quiz attempts for *session_id*, newest first.

    Used in the Chapter 4 evaluation to compare sessions by quiz engagement
    level and correlate attempt count with effective cost per session.

    Returns
    -------
    list[dict]
        Each dict has: id_attempt, session_id, tool_output_id, submitted_answers,
        score, total_questions, budget_reward, submitted_at.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id_attempt, session_id, tool_output_id,
                   submitted_answers, score, total_questions,
                   budget_reward, submitted_at
            FROM   quiz_attempt
            WHERE  session_id = $1
            ORDER  BY submitted_at DESC
            """,
            session_id,
        )
    results = []
    for r in rows:
        row_dict = dict(r)
        # submitted_answers is stored as JSONB; deserialise for callers
        if isinstance(row_dict.get("submitted_answers"), str):
            row_dict["submitted_answers"] = json.loads(row_dict["submitted_answers"])
        results.append(row_dict)
    return results


# ════════════════════════════════════════════════════════════════════════════
# route_log table  (read-only queries – writes live in cost_tracker.py)
# ════════════════════════════════════════════════════════════════════════════

async def get_route_log_for_session(session_id: int) -> list[dict]:
    """
    Return all route_log rows for *session_id*, oldest first.

    Provides the per-request cost breakdown used in the Chapter 4 evaluation
    metrics: total spend, cost split between local and cloud, pool distribution,
    and effective cost after quiz-reward credits.

    Returns
    -------
    list[dict]
        Each dict has: id_log, session_id, message_id, model_name, category,
        confidence, input_token, output_token, cost, pool, created_at.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id_log, session_id, message_id, model_name, category,
                   confidence, input_token, output_token, cost, pool, created_at
            FROM   route_log
            WHERE  session_id = $1
            ORDER  BY created_at ASC
            """,
            session_id,
        )
    return [dict(r) for r in rows]


async def get_session_cost_summary(session_id: int) -> dict:
    """
    Aggregate cost metrics for *session_id* in a single DB round-trip.

    Used by the Chapter 4 evaluation to compute:
      - total_spend         : sum of all positive cost entries
      - total_reward        : absolute sum of all negative cost entries (quiz credits)
      - net_cost            : total_spend - total_reward  (effective cost)
      - local_requests      : count of llama3.2:3b route_log rows (cost = 0)
      - cloud_requests      : count of GPT-4o / GPT-4o mini rows
      - visible_pool_spend  : spend drawn from the visible pool
      - bonus_pool_spend    : spend drawn from the quiz-bonus pool
      - shadow_pool_spend   : spend drawn from the shadow reserve

    Returns
    -------
    dict with the keys listed above.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COALESCE(SUM(cost) FILTER (WHERE cost > 0), 0)   AS total_spend,
                COALESCE(ABS(SUM(cost) FILTER (WHERE cost < 0)), 0) AS total_reward,
                COALESCE(SUM(cost), 0)                            AS net_cost,
                COUNT(*) FILTER (WHERE model_name = 'llama3.2:3b') AS local_requests,
                COUNT(*) FILTER (WHERE model_name <> 'llama3.2:3b'
                                   AND category  <> 'quiz_reward')  AS cloud_requests,
                COALESCE(SUM(cost) FILTER (WHERE pool = 'visible'), 0)  AS visible_pool_spend,
                COALESCE(SUM(cost) FILTER (WHERE pool = 'bonus'),   0)  AS bonus_pool_spend,
                COALESCE(SUM(cost) FILTER (WHERE pool = 'shadow'),  0)  AS shadow_pool_spend
            FROM route_log
            WHERE session_id = $1
            """,
            session_id,
        )
    return dict(row)


# ════════════════════════════════════════════════════════════════════════════
# model table  (reference data – read only after bootstrap)
# ════════════════════════════════════════════════════════════════════════════

async def get_model(model_name: str) -> Optional[dict]:
    """
    Return the model reference row for *model_name*.

    Used when the router needs to look up pricing dynamically rather than
    relying on the in-process MODEL_COSTS dict in cost_tracker.py.

    Returns None if *model_name* is not in the model table.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id_model, model_name,
                   input_cost_per_token, output_cost_per_token
            FROM   model
            WHERE  model_name = $1
            """,
            model_name,
        )
    return dict(row) if row else None


async def list_models() -> list[dict]:
    """
    Return all rows from the model table.

    Useful for admin endpoints that expose pricing information.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id_model, model_name, input_cost_per_token, output_cost_per_token FROM model ORDER BY id_model"
        )
    return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════════════════════════
# calendar_event table
# ════════════════════════════════════════════════════════════════════════════

async def create_calendar_event(
    user_id: int,
    title: str,
    description: Optional[str],
    start_date: str,
    end_date: Optional[str] = None,
) -> dict:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO calendar_event (user_id, title, description, start_date, end_date)
            VALUES ($1, $2, $3, $4::TIMESTAMPTZ, $5::TIMESTAMPTZ)
            RETURNING id_event, user_id, title, description, start_date, end_date, created_at
            """,
            user_id, title, description, start_date, end_date,
        )
    return dict(row)


async def get_calendar_events(user_id: int) -> list[dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id_event, user_id, title, description, start_date, end_date, created_at
            FROM   calendar_event
            WHERE  user_id = $1
            ORDER  BY start_date ASC
            """,
            user_id,
        )
    return [dict(r) for r in rows]


async def update_calendar_event(
    event_id: int,
    user_id: int,
    title: str,
    description: Optional[str],
    start_date: str,
    end_date: Optional[str],
) -> Optional[dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE calendar_event
            SET    title = $3, description = $4,
                   start_date = $5::TIMESTAMPTZ, end_date = $6::TIMESTAMPTZ
            WHERE  id_event = $1 AND user_id = $2
            RETURNING id_event, user_id, title, description, start_date, end_date, created_at
            """,
            event_id, user_id, title, description, start_date, end_date,
        )
    return dict(row) if row else None


async def delete_calendar_event(event_id: int, user_id: int) -> bool:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM calendar_event WHERE id_event = $1 AND user_id = $2",
            event_id, user_id,
        )
    return result == "DELETE 1"


# ════════════════════════════════════════════════════════════════════════════
# schedule_entry table  (session-scoped study schedule)
# ════════════════════════════════════════════════════════════════════════════

def _parse_schedule_row(row) -> dict:
    d = dict(row)
    topics_val = d.get("topics")
    if isinstance(topics_val, str):
        d["topics"] = json.loads(topics_val)
    elif topics_val is None:
        d["topics"] = []
    date_val = d.get("date")
    if hasattr(date_val, "isoformat"):
        d["date"] = date_val.isoformat()
    created_val = d.get("created_at")
    if hasattr(created_val, "isoformat"):
        d["created_at"] = created_val.isoformat()
    return d


async def save_schedule_entries(session_id: int, entries: list[dict]) -> None:
    """Bulk-insert schedule day entries produced by the create_schedule tool."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        for e in entries:
            await conn.execute(
                """
                INSERT INTO schedule_entry (session_id, date, topics, duration_hours, note)
                VALUES ($1, $2::DATE, $3, $4, $5)
                """,
                session_id,
                e.get("day"),
                json.dumps(e.get("topics", [])),
                float(e.get("hours", 2.0)),
                e.get("notes") or None,
            )


async def get_schedule_entries(session_id: int) -> list[dict]:
    """Return all schedule entries for *session_id*, sorted by date."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id_entry, session_id, date, topics, duration_hours, note, created_at
            FROM   schedule_entry
            WHERE  session_id = $1
            ORDER  BY date ASC, created_at ASC
            """,
            session_id,
        )
    return [_parse_schedule_row(r) for r in rows]


async def create_schedule_entry(
    session_id: int,
    date: str,
    topics: list,
    duration_hours: float,
    note: Optional[str],
) -> dict:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO schedule_entry (session_id, date, topics, duration_hours, note)
            VALUES ($1, $2::DATE, $3, $4, $5)
            RETURNING id_entry, session_id, date, topics, duration_hours, note, created_at
            """,
            session_id, date, json.dumps(topics), float(duration_hours), note,
        )
    return _parse_schedule_row(row)


async def update_schedule_entry(
    entry_id: int,
    session_id: int,
    date: str,
    topics: list,
    duration_hours: float,
    note: Optional[str],
) -> Optional[dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE schedule_entry
            SET    date = $3::DATE, topics = $4, duration_hours = $5, note = $6
            WHERE  id_entry = $1 AND session_id = $2
            RETURNING id_entry, session_id, date, topics, duration_hours, note, created_at
            """,
            entry_id, session_id, date, json.dumps(topics), float(duration_hours), note,
        )
    return _parse_schedule_row(row) if row else None


async def delete_schedule_entry(entry_id: int, session_id: int) -> bool:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM schedule_entry WHERE id_entry = $1 AND session_id = $2",
            entry_id, session_id,
        )
    return result == "DELETE 1"