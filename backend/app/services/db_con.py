from __future__ import annotations

import json
import os
from datetime import date as _date
from typing import Optional

import asyncpg  # type: ignore

DEFAULT_DAILY_VISIBLE_LIMIT: float = 5000.0   # token-equivalent units
DEFAULT_SHADOW_RESERVE:       float = 500.0    # hidden; quiz-gen only

# Module-level pool singleton
_pool: Optional[asyncpg.Pool] = None


async def init_db_pool(dsn: Optional[str] = None) -> asyncpg.Pool:
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
    if _pool is None:
        raise RuntimeError(
            "Database pool is not initialised. "
            "Call await init_db_pool() during application startup."
        )
    return _pool


async def close_db_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


# ════════════════════════════════════════════════════════════════════════════
# DDL bootstrap
# ════════════════════════════════════════════════════════════════════════════

_DDL = """
CREATE TABLE IF NOT EXISTS model (
    id_model        SERIAL       PRIMARY KEY,
    model_name      VARCHAR(80)  NOT NULL UNIQUE,
    input_cost_per_token  NUMERIC(14,10) NOT NULL DEFAULT 0,
    output_cost_per_token NUMERIC(14,10) NOT NULL DEFAULT 0
);

INSERT INTO model (model_name, input_cost_per_token, output_cost_per_token)
VALUES
    ('llama3.2:3b',  0,            0           ),
    ('gpt-4o-mini',  0.00000015,   0.00000060  ),
    ('gpt-4o',       0.00000500,   0.00001500  )
ON CONFLICT (model_name) DO NOTHING;

CREATE TABLE IF NOT EXISTS "user" (
    id_user     SERIAL      PRIMARY KEY,
    username    VARCHAR(80) NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS role (
    id_role     SERIAL      PRIMARY KEY,
    role_name   VARCHAR(20) NOT NULL UNIQUE
);

INSERT INTO role (role_name)
VALUES ('user'), ('assistant'), ('system')
ON CONFLICT (role_name) DO NOTHING;

CREATE TABLE IF NOT EXISTS session (
    id_session          SERIAL       PRIMARY KEY,
    user_id             INT          NOT NULL REFERENCES "user"(id_user) ON DELETE CASCADE,
    started_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Visible budget pool
    daily_visible_limit NUMERIC(12,4) NOT NULL DEFAULT 5000,
    visible_used        NUMERIC(12,4) NOT NULL DEFAULT 0,

    -- Shadow reserve pool
    shadow_reserve      NUMERIC(12,4) NOT NULL DEFAULT 500,
    shadow_used         NUMERIC(12,4) NOT NULL DEFAULT 0,

    -- Earned bonus pool
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

-- Uploaded student documents
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

-- MCP tool result cache
CREATE TABLE IF NOT EXISTS tool_output (
    id_tool         SERIAL       PRIMARY KEY,
    session_id      INT          NOT NULL REFERENCES session(id_session) ON DELETE CASCADE,
    tool_name       VARCHAR(80)  NOT NULL,
    output_json     JSONB        NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Per-request cost audit log
CREATE TABLE IF NOT EXISTS route_log (
    id_log       SERIAL        PRIMARY KEY,
    session_id   INT           NOT NULL REFERENCES session(id_session) ON DELETE CASCADE,
    message_id   INT           REFERENCES message(id_message) ON DELETE SET NULL,
    id_model     INT           REFERENCES model(id_model),
    category     VARCHAR(40)   NOT NULL,
    confidence   NUMERIC(4,3)  NOT NULL DEFAULT 0,
    input_token  INT           NOT NULL DEFAULT 0,
    output_token INT           NOT NULL DEFAULT 0,
    cost         NUMERIC(14,8) NOT NULL DEFAULT 0,
    pool         VARCHAR(10),
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- Quiz attempt records
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

-- Session-scoped study schedule entries
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

# Runs the above DDL on startup to create tables
async def bootstrap_schema() -> None:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(_DDL)
        # Migrate route_log.model_name VARCHAR to id_model FK
        await conn.execute("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE  table_name  = 'route_log'
                      AND  column_name = 'model_name'
                ) THEN
                    ALTER TABLE route_log
                        ADD COLUMN IF NOT EXISTS id_model INT REFERENCES model(id_model);
                    UPDATE route_log rl
                    SET    id_model = m.id_model
                    FROM   model m
                    WHERE  m.model_name = rl.model_name
                      AND  rl.id_model IS NULL;
                    ALTER TABLE route_log DROP COLUMN model_name;
                END IF;
            END $$;
        """)

async def upsert_user(username: str) -> dict:
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

async def create_session(
    user_id: int,
    daily_visible_limit: float = DEFAULT_DAILY_VISIBLE_LIMIT,
    shadow_reserve: float      = DEFAULT_SHADOW_RESERVE,
) -> dict:

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
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM session WHERE id_session = $1",
            session_id,
        )
    return dict(row) if row else None

async def get_user_sessions(user_id: int) -> list[dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM session WHERE user_id = $1 ORDER BY started_at DESC",
            user_id,
        )
    return [dict(r) for r in rows]


async def delete_session(session_id: int) -> None:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM session WHERE id_session = $1",
            session_id,
        )


async def get_sessions_due_for_reset() -> list[int]:
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

async def get_role_by_name(role_name: str) -> Optional[dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id_role, role_name FROM role WHERE role_name = $1",
            role_name,
        )
    return dict(row) if row else None

async def create_message(
    session_id: int,
    role_name: str,
    content: str,
) -> dict:
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

async def save_file(
    session_id: int,
    filename: str,
    extracted_text: Optional[str] = None,
) -> dict:
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
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE file SET extracted_text = $1 WHERE id_file = $2",
            extracted_text,
            file_id,
        )

async def get_files_by_ids(session_id: int, file_ids: list[int]) -> list[dict]:
    if not file_ids:
        return []
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
                SELECT id_file, filename, extracted_text
                FROM   file
                WHERE  session_id = $1 AND id_file = ANY($2)
                ORDER  BY uploaded_at ASC
            """,
            session_id,
            file_ids,
        )
    return [dict(r) for r in rows]

async def get_session_context_summary(session_id: int) -> dict:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        schedule_rows = await conn.fetch(
            """
                SELECT date, topics
                FROM   schedule_entry
                WHERE  session_id = $1 AND date >= CURRENT_DATE
                ORDER  BY date ASC
                LIMIT  3
            """,
            session_id,
        )
        quiz_row = await conn.fetchrow(
            """
                SELECT score, total_questions   
                FROM   quiz_attempt
                WHERE  session_id = $1
                ORDER  BY submitted_at DESC
                LIMIT  1
            """,
            session_id,
        )
        file_rows = await conn.fetch(
            "SELECT filename FROM file WHERE session_id = $1 ORDER BY uploaded_at DESC",
            session_id,
        )
        budget_row = await conn.fetchrow(
            "SELECT daily_visible_limit, visible_used, quiz_bonus FROM session WHERE id_session = $1",
            session_id,
        )

    schedule = []
    for r in schedule_rows:
        topics = r["topics"]
        if isinstance(topics, str):
            topics = json.loads(topics)
        date_val = r["date"]
        schedule.append({
            "date":   date_val.isoformat() if hasattr(date_val, "isoformat") else str(date_val),
            "topics": topics or [],
        })

    last_quiz = None
    if quiz_row:
        last_quiz = {"score": quiz_row["score"], "total": quiz_row["total_questions"]}

    files = [{"filename": r["filename"]} for r in file_rows]

    budget = {"visible_remaining": 0.0, "quiz_bonus": 0.0}
    if budget_row:
        budget = {
            "visible_remaining": max(
                0.0,
                float(budget_row["daily_visible_limit"]) - float(budget_row["visible_used"]),
            ),
            "quiz_bonus": float(budget_row["quiz_bonus"]),
        }

    return {
        "schedule":  schedule,
        "last_quiz": last_quiz,
        "files":     files,
        "budget":    budget,
    }

async def save_tool_output(
    session_id: int,
    tool_name: str,
    output_data: dict | list,
) -> dict:
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

async def get_all_quiz_questions(session_id: int) -> list[dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
                SELECT output_json
                FROM   tool_output
                WHERE  session_id = $1 AND tool_name = 'generate_quiz'
                ORDER  BY created_at ASC
            """,
            session_id,
        )
    questions: list[dict] = []
    for row in rows:
        data = row["output_json"]
        if isinstance(data, str):
            data = json.loads(data)
        if isinstance(data, list):
            questions.extend(data)
    return questions

async def get_quiz_attempts_for_session(session_id: int) -> list[dict]:
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

async def get_route_log_for_session(session_id: int) -> list[dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
                SELECT rl.id_log, rl.session_id, rl.message_id,
                    rl.id_model, m.model_name,
                    rl.category, rl.confidence,
                    rl.input_token, rl.output_token, rl.cost, rl.pool, rl.created_at
                FROM   route_log rl
                LEFT JOIN model m ON m.id_model = rl.id_model
                WHERE  rl.session_id = $1
                ORDER  BY rl.created_at ASC
            """,
            session_id,
        )
    return [dict(r) for r in rows]

async def get_session_cost_summary(session_id: int) -> dict:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
                SELECT
                    COALESCE(SUM(rl.cost) FILTER (WHERE rl.cost > 0), 0)       AS total_spend,
                    COALESCE(ABS(SUM(rl.cost) FILTER (WHERE rl.cost < 0)), 0)   AS total_reward,
                    COALESCE(SUM(rl.cost), 0)                                   AS net_cost,
                    COUNT(*) FILTER (WHERE m.model_name = 'llama3.2:3b')        AS local_requests,
                    COUNT(*) FILTER (WHERE rl.id_model IS NOT NULL
                                    AND  m.model_name <> 'llama3.2:3b'
                                    AND  rl.category  <> 'quiz_reward')      AS cloud_requests,
                    COALESCE(SUM(rl.cost) FILTER (WHERE rl.pool = 'visible'), 0) AS visible_pool_spend,
                    COALESCE(SUM(rl.cost) FILTER (WHERE rl.pool = 'bonus'),   0) AS bonus_pool_spend,
                    COALESCE(SUM(rl.cost) FILTER (WHERE rl.pool = 'shadow'),  0) AS shadow_pool_spend
                FROM   route_log rl
                LEFT JOIN model m ON m.id_model = rl.id_model
                WHERE  rl.session_id = $1
            """,
            session_id,
        )
    return dict(row)

async def get_model(model_name: str) -> Optional[dict]:
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
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id_model, model_name, input_cost_per_token, output_cost_per_token FROM model ORDER BY id_model"
        )
    return [dict(r) for r in rows]

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
    if d.get("duration_hours") is not None:
        d["duration_hours"] = float(d["duration_hours"])
    return d

async def save_schedule_entries(session_id: int, entries: list[dict]) -> None:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        for e in entries:
            day_str = e.get("day") or ""
            try:
                day_obj = _date.fromisoformat(day_str)
            except ValueError:
                continue  # skip malformed dates
            await conn.execute(
                """
                    INSERT INTO schedule_entry (session_id, date, topics, duration_hours, note)
                    VALUES ($1, $2, $3, $4, $5)
                """,
                session_id,
                day_obj,
                json.dumps(e.get("topics", [])),
                float(e.get("hours", 2.0)),
                e.get("notes") or None,
            )

async def get_schedule_entries(session_id: int) -> list[dict]:
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
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id_entry, session_id, date, topics, duration_hours, note, created_at
            """,
            session_id, _date.fromisoformat(date), json.dumps(topics), float(duration_hours), note,
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
                SET    date = $3, topics = $4, duration_hours = $5, note = $6
                WHERE  id_entry = $1 AND session_id = $2
                RETURNING id_entry, session_id, date, topics, duration_hours, note, created_at
            """,
            entry_id, session_id, _date.fromisoformat(date), json.dumps(topics), float(duration_hours), note,
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

async def get_user_schedule_entries(user_id: int) -> list[dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
                SELECT se.id_entry, se.session_id, se.date,
                    se.topics, se.duration_hours, se.note, se.created_at
                FROM   schedule_entry se
                JOIN   session s ON s.id_session = se.session_id
                WHERE  s.user_id = $1
                ORDER  BY se.date ASC
            """,
            user_id,
        )
    return [_parse_schedule_row(r) for r in rows]