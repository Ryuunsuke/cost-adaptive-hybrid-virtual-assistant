"""
cost_tracker.py
---------------
Three-pool budget management and route_log persistence (thesis §3.4.3).

Pools
-----
VISIBLE  daily_visible_limit / visible_used   — shown in the status bar UI
BONUS    quiz_bonus                           — earned overflow; persists across resets
SHADOW   shadow_reserve / shadow_used         — hidden; quiz generation only

Design rules
------------
* Regular cloud chat draws from VISIBLE first, then falls through to BONUS.
* Quiz generation draws exclusively from SHADOW; the visible balance is untouched.
* All pool mutations run inside a single PostgreSQL transaction (asyncpg) so
  concurrent requests cannot double-spend any pool (thesis §3.4.3).
* route_log receives one row per routed request: debit rows carry a positive
  cost, quiz-reward rows carry a negative cost (the bonus credited).
* This module is imported by task_router.py; no cost logic lives there.
"""

from __future__ import annotations

import json
import time
from enum import Enum
from typing import Optional

import asyncpg  # type: ignore

# ---------------------------------------------------------------------------
# DB connection factory (thesis §3.3.3)
# ---------------------------------------------------------------------------
from services.db_con import get_db_pool


# ---------------------------------------------------------------------------
# Pool identifier
# ---------------------------------------------------------------------------
class Pool(str, Enum):
    VISIBLE = "visible"
    BONUS   = "bonus"
    SHADOW  = "shadow"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class BudgetExhaustedError(Exception):
    """
    Raised when no eligible pool has sufficient balance to cover the request.

    Attributes
    ----------
    pool : Pool
        The last pool that was checked before raising.
    """

    def __init__(self, pool: Pool) -> None:
        self.pool = pool
        super().__init__(f"{pool.value} budget exhausted")


# ---------------------------------------------------------------------------
# Per-token cost constants (USD, matching thesis §3.2.4 model table)
# These mirror the cost_per_token rows in the model DB table so that the
# router can compute costs without an extra DB round-trip.
# ---------------------------------------------------------------------------
MODEL_COSTS: dict[str, dict[str, float]] = {
    # model_name → {input_cost_per_token, output_cost_per_token}  (USD)
    "gpt-4o-mini": {"input": 0.00000015, "output": 0.00000060},
    "gpt-4o":      {"input": 0.00000500, "output": 0.00001500},
    # local model: always zero
    "llama3.2:3b": {"input": 0.0,        "output": 0.0},
}

# Tokens credited to quiz_bonus per correct quiz answer (thesis §3.5.3)
QUIZ_REWARD_PER_CORRECT: int = 50


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return the USD cost for a completed LLM call."""
    rates = MODEL_COSTS.get(model, {"input": 0.0, "output": 0.0})
    return rates["input"] * input_tokens + rates["output"] * output_tokens


# ---------------------------------------------------------------------------
# Budget state query
# ---------------------------------------------------------------------------
async def get_budget_state(session_id: int) -> dict:
    """
    Return all budget-relevant fields for *session_id*.

    Keys
    ----
    daily_visible_limit, visible_used, shadow_reserve, shadow_used,
    quiz_bonus, depleted_at, next_reset_at
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT daily_visible_limit,
                   visible_used,
                   shadow_reserve,
                   shadow_used,
                   quiz_bonus,
                   depleted_at,
                   next_reset_at
            FROM   session
            WHERE  id_session = $1
            """,
            session_id,
        )
    if row is None:
        raise ValueError(f"Session {session_id} not found")
    return dict(row)


# ---------------------------------------------------------------------------
# Pool check + deduct – regular cloud chat
# ---------------------------------------------------------------------------
async def check_and_deduct_cloud(
    session_id: int,
    input_tokens: int,
    output_tokens: int,
    model: str,
) -> Pool:
    """
    Two-stage budget check for regular cloud-chat requests (thesis §3.4.3).

    Stage 1 – Visible pool
        If (visible_used + cost) ≤ daily_visible_limit: charge visible pool.

    Stage 2 – Quiz bonus pool
        If visible is exhausted but quiz_bonus ≥ cost: charge bonus pool.

    Raises
    ------
    BudgetExhaustedError(Pool.BONUS)
        When neither pool can cover the request.

    Returns
    -------
    Pool
        The pool that was charged (VISIBLE or BONUS).
    """
    cost = _compute_cost(model, input_tokens, output_tokens)
    db_pool = await get_db_pool()

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT daily_visible_limit, visible_used, quiz_bonus, depleted_at
                FROM   session
                WHERE  id_session = $1
                FOR UPDATE
                """,
                session_id,
            )
            if row is None:
                raise ValueError(f"Session {session_id} not found")

            visible_remaining = row["daily_visible_limit"] - row["visible_used"]

            # ── Stage 1: try visible pool ─────────────────────────────────
            if visible_remaining >= cost:
                await conn.execute(
                    "UPDATE session SET visible_used = visible_used + $1 WHERE id_session = $2",
                    cost,
                    session_id,
                )
                return Pool.VISIBLE

            # ── Stage 2: try quiz-bonus overflow pool ─────────────────────
            if row["quiz_bonus"] >= cost:
                await conn.execute(
                    "UPDATE session SET quiz_bonus = quiz_bonus - $1 WHERE id_session = $2",
                    cost,
                    session_id,
                )
                return Pool.BONUS

            # Record depletion timestamp on first exhaustion (for daily reset anchor)
            if row["depleted_at"] is None:
                await conn.execute(
                    """
                    UPDATE session
                    SET    depleted_at  = NOW(),
                           next_reset_at = NOW() + INTERVAL '24 hours'
                    WHERE  id_session = $1
                    """,
                    session_id,
                )

    raise BudgetExhaustedError(Pool.BONUS)


# ---------------------------------------------------------------------------
# Two-step cloud budget helpers (use these in task_router.py)
# ---------------------------------------------------------------------------
async def check_pool_available(session_id: int) -> Pool:
    """
    Read-only check: determine which pool would cover the next cloud call.
    Does NOT deduct anything — call deduct_cloud_actual() after the LLM returns.

    Raises BudgetExhaustedError(Pool.BONUS) when both pools are at zero.
    """
    db_pool = await get_db_pool()
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT daily_visible_limit, visible_used, quiz_bonus, depleted_at
            FROM   session
            WHERE  id_session = $1
            """,
            session_id,
        )
        if row is None:
            raise ValueError(f"Session {session_id} not found")

        visible_remaining = float(row["daily_visible_limit"]) - float(row["visible_used"])

        if visible_remaining > 0:
            return Pool.VISIBLE

        if float(row["quiz_bonus"]) > 0:
            return Pool.BONUS

        # Both exhausted — stamp depletion timestamp on first occurrence
        if row["depleted_at"] is None:
            await conn.execute(
                """
                UPDATE session
                SET    depleted_at   = NOW(),
                       next_reset_at = NOW() + INTERVAL '24 hours'
                WHERE  id_session = $1
                  AND  depleted_at IS NULL
                """,
                session_id,
            )

    raise BudgetExhaustedError(Pool.BONUS)


async def deduct_cloud_actual(
    session_id: int,
    input_tokens: int,
    output_tokens: int,
    pool: Pool,
) -> None:
    """
    Deduct actual token usage (input + output token count) from *pool*.
    The budget is denominated in tokens, not USD — visible_limit = 5000 tokens.
    Call this AFTER cloud_response() returns real token counts.
    """
    tokens = input_tokens + output_tokens
    if tokens <= 0:
        return

    db_pool = await get_db_pool()
    async with db_pool.acquire() as conn:
        if pool == Pool.VISIBLE:
            await conn.execute(
                "UPDATE session SET visible_used = visible_used + $1 WHERE id_session = $2",
                float(tokens),
                session_id,
            )
        elif pool == Pool.BONUS:
            # Cap at 0 to prevent negative bonus balance
            await conn.execute(
                "UPDATE session SET quiz_bonus = GREATEST(0, quiz_bonus - $1) WHERE id_session = $2",
                float(tokens),
                session_id,
            )


# ---------------------------------------------------------------------------
# Pool check + deduct – quiz generation (shadow reserve only)
# ---------------------------------------------------------------------------
async def check_and_deduct_shadow(
    session_id: int,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """
    Shadow-reserve check for quiz generation calls (thesis §3.4.3, §3.5.3).

    The shadow pool is invisible to the student. It is reserved exclusively for
    quiz generation so that students can always attempt quizzes and earn bonus
    tokens regardless of their visible balance.

    Raises
    ------
    BudgetExhaustedError(Pool.SHADOW)
        When shadow_reserve - shadow_used < cost.
    """
    # Quiz generation always uses the GPT-4o mini rate
    cost = _compute_cost("gpt-4o-mini", input_tokens, output_tokens)
    db_pool = await get_db_pool()

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT shadow_reserve, shadow_used
                FROM   session
                WHERE  id_session = $1
                FOR UPDATE
                """,
                session_id,
            )
            if row is None:
                raise ValueError(f"Session {session_id} not found")

            shadow_remaining = row["shadow_reserve"] - row["shadow_used"]
            if shadow_remaining < cost:
                raise BudgetExhaustedError(Pool.SHADOW)

            await conn.execute(
                "UPDATE session SET shadow_used = shadow_used + $1 WHERE id_session = $2",
                cost,
                session_id,
            )


# ---------------------------------------------------------------------------
# route_log persistence
# ---------------------------------------------------------------------------
async def log_route_event(
    session_id: int,
    message_id: Optional[int],
    model_name: str,
    category: str,
    confidence: float,
    input_tokens: int,
    output_tokens: int,
    pool: Optional[Pool] = None,
) -> None:
    """
    Write one row to route_log (thesis §3.3.3 Database Schema).

    For local (llama3.2:3b) calls: cost = 0.0, pool = None.
    For cloud calls: cost is derived from MODEL_COSTS.
    For quiz-reward credits: use log_quiz_reward() instead.

    Parameters
    ----------
    session_id  : FK → session.id_session
    message_id  : FK → message.id_message (None if not yet persisted)
    model_name  : e.g. "gpt-4o-mini", "llama3.2:3b"
    category    : "administrative" | "informational" | "analytical"
    confidence  : classifier confidence score
    input_tokens, output_tokens : token counts from the LLM response
    pool        : which budget pool was charged (None for local/free calls)
    """
    cost = _compute_cost(model_name, input_tokens, output_tokens)
    db_pool = await get_db_pool()

    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO route_log
                (session_id, message_id, model_name, category, confidence,
                 input_token, output_token, cost, pool, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
            """,
            session_id,
            message_id,
            model_name,
            category,
            confidence,
            input_tokens,
            output_tokens,
            cost,
            pool.value if pool else None,
        )


# ---------------------------------------------------------------------------
# Quiz-reward credit
# ---------------------------------------------------------------------------
async def credit_quiz_bonus(
    session_id: int,
    correct_count: int,
    total_questions: int,
    tool_output_id: int,
    submitted_answers: dict,
) -> int:
    """
    Credit quiz_bonus for correct answers and persist the attempt (thesis §3.5.3).

    The quiz_bonus field persists across daily resets and is consumed only after
    the visible balance is exhausted, acting as earned overflow credit.

    Parameters
    ----------
    session_id      : FK → session.id_session
    correct_count   : number of questions answered correctly
    total_questions : total questions in the quiz
    tool_output_id  : FK → tool_output.id_tool (the cached quiz record)
    submitted_answers : {question_index: chosen_option} mapping

    Returns
    -------
    int
        Total tokens credited to quiz_bonus.
    """
    bonus_tokens = correct_count * QUIZ_REWARD_PER_CORRECT
    db_pool = await get_db_pool()

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            # Increment quiz_bonus atomically
            await conn.execute(
                "UPDATE session SET quiz_bonus = quiz_bonus + $1 WHERE id_session = $2",
                float(bonus_tokens),
                session_id,
            )

            # Persist the quiz attempt for Chapter 4 evaluation analytics
            await conn.execute(
                """
                INSERT INTO quiz_attempt
                    (session_id, tool_output_id, submitted_answers,
                     score, total_questions, budget_reward, submitted_at)
                VALUES ($1, $2, $3, $4, $5, $6, NOW())
                """,
                session_id,
                tool_output_id,
                json.dumps(submitted_answers),   # asyncpg expects str for JSONB
                correct_count,
                total_questions,
                bonus_tokens,
            )

            # Log the reward in route_log as a negative-cost quiz_reward entry
            await conn.execute(
                """
                INSERT INTO route_log
                    (session_id, message_id, model_name, category, confidence,
                     input_token, output_token, cost, pool, created_at)
                VALUES ($1, NULL, 'quiz_answer_check', 'quiz_reward', 1.0,
                        0, 0, $2, NULL, NOW())
                """,
                session_id,
                -float(bonus_tokens),   # negative = budget credit
            )

    return bonus_tokens


async def save_quiz_attempt(
    session_id: int,
    correct_count: int,
    total_questions: int,
    tool_output_id: int,
    submitted_answers: dict,
) -> None:
    """
    Persist a quiz attempt with zero budget reward.

    Called for retries after the first perfect completion, or for partial-score
    submissions that do not earn a reward.
    """
    db_pool = await get_db_pool()
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO quiz_attempt
                (session_id, tool_output_id, submitted_answers,
                 score, total_questions, budget_reward, submitted_at)
            VALUES ($1, $2, $3, $4, $5, 0, NOW())
            """,
            session_id,
            tool_output_id,
            json.dumps(submitted_answers),
            correct_count,
            total_questions,
        )


# ---------------------------------------------------------------------------
# UI budget display
# ---------------------------------------------------------------------------
async def get_budget_display(session_id: int) -> dict:
    """
    Return the values shown in the React status bar (thesis §3.3.1).

    The effective balance is:  daily_visible_limit − visible_used + quiz_bonus

    Shadow reserve is deliberately excluded — the student never sees it.

    Returns
    -------
    dict with keys:
        effective_balance  : float  (what the UI shows as "remaining")
        daily_limit        : float  (daily_visible_limit)
        quiz_bonus         : float  (accumulated bonus tokens)
        visible_used       : float  (consumed from the base allowance today)
    """
    state = await get_budget_state(session_id)
    effective = (
        state["daily_visible_limit"]
        - state["visible_used"]
        + state["quiz_bonus"]
    )
    return {
        "effective_balance": round(effective, 4),
        "daily_limit":       round(state["daily_visible_limit"], 4),
        "quiz_bonus":        round(state["quiz_bonus"], 4),
        "visible_used":      round(state["visible_used"], 4),
    }


# ---------------------------------------------------------------------------
# Daily reset (called by the FastAPI background task – thesis §3.4.3)
# ---------------------------------------------------------------------------
async def run_daily_reset(session_id: int) -> bool:
    """
    Reset visible_used and shadow_used to zero for *session_id* if
    next_reset_at ≤ NOW().  quiz_bonus is intentionally left unchanged.

    Sets next_reset_at ← depleted_at + 24 h so the anchor persists for the
    next cycle. If depleted_at is NULL the reset was midnight-UTC-triggered
    and next_reset_at is set 24 h from now.

    Returns
    -------
    bool
        True if the reset was applied, False if next_reset_at is in the future.
    """
    db_pool = await get_db_pool()
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT next_reset_at, depleted_at
                FROM   session
                WHERE  id_session = $1
                FOR UPDATE
                """,
                session_id,
            )
            if row is None or row["next_reset_at"] is None:
                return False

            # Check using DB clock to avoid client-clock skew
            is_due = await conn.fetchval(
                "SELECT NOW() >= $1", row["next_reset_at"]
            )
            if not is_due:
                return False

            # Compute next anchor: depleted_at + 24 h, or NOW() + 24 h if unset
            await conn.execute(
                """
                UPDATE session
                SET    visible_used   = 0,
                       shadow_used    = 0,
                       depleted_at    = NULL,
                       next_reset_at  = CASE
                                          WHEN depleted_at IS NOT NULL
                                          THEN depleted_at + INTERVAL '24 hours'
                                          ELSE NOW()     + INTERVAL '24 hours'
                                        END
                WHERE  id_session = $1
                """,
                session_id,
            )
    return True