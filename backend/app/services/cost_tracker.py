from __future__ import annotations

import json
import time
from enum import Enum
from typing import Optional

import asyncpg  # type: ignore

from services.db_con import get_db_pool

class Pool(str, Enum):
    VISIBLE = "visible"
    BONUS   = "bonus"
    SHADOW  = "shadow"

# raises when a budget check fails due to insufficient balance in the relevant pool
class BudgetExhaustedError(Exception):
    def __init__(self, pool: Pool) -> None:
        self.pool = pool
        super().__init__(f"{pool.value} budget exhausted")

MODEL_COSTS: dict[str, dict[str, float]] = {
    # model_name → {input_cost_per_token, output_cost_per_token}  (USD)
    "gpt-4o-mini": {"input": 0.00000015, "output": 0.00000060},
    "gpt-4o":      {"input": 0.00000500, "output": 0.00001500},
    # local model: always zero
    "llama3.2:3b": {"input": 0.0,        "output": 0.0},
}

PERFECT_QUIZ_REWARD: int = 500

def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return the USD cost for a completed LLM call."""
    rates = MODEL_COSTS.get(model, {"input": 0.0, "output": 0.0})
    return rates["input"] * input_tokens + rates["output"] * output_tokens

# return the current budget state for a session, including visible limit, 
# visible used, quiz bonus, shadow reserve, shadow used, and timestamps for depletion and next reset
async def get_budget_state(session_id: int) -> dict:
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

# two-stage budget check for cloud calls, with shadow-reserve check for quiz generation
async def check_and_deduct_cloud(
    session_id: int,
    input_tokens: int,
    output_tokens: int,
    model: str,
) -> Pool:

    cost = _compute_cost(model, input_tokens, output_tokens)
    db_pool = await get_db_pool()
    # Stage 1: try visible pool, then Stage 2: try quiz-bonus overflow pool, with atomic transactions and depletion timestamping
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

            # stage 1: try visible pool
            if visible_remaining >= cost:
                await conn.execute(
                    "UPDATE session SET visible_used = visible_used + $1 WHERE id_session = $2",
                    cost,
                    session_id,
                )
                return Pool.VISIBLE

            # stage 2: try quiz-bonus pool as overflow
            if row["quiz_bonus"] >= cost:
                await conn.execute(
                    "UPDATE session SET quiz_bonus = quiz_bonus - $1 WHERE id_session = $2",
                    cost,
                    session_id,
                )
                return Pool.BONUS

            # Record depletion timestamp on first exhaustion
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


# determine which pool would cover the next cloud call without deducting
async def check_pool_available(session_id: int) -> Pool:
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

# deduct actual token usage from the specified pool after the LLM call returns real token counts
async def deduct_cloud_actual(
    session_id: int,
    input_tokens: int,
    output_tokens: int,
    pool: Pool,
) -> None:

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


# shadow-reserve check for quiz generation
async def check_and_deduct_shadow(
    session_id: int,
    input_tokens: int,
    output_tokens: int,
) -> None:

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

# record route event
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
    
    cost = _compute_cost(model_name, input_tokens, output_tokens)
    db_pool = await get_db_pool()

    async with db_pool.acquire() as conn:
        await conn.execute(
            """
                INSERT INTO route_log
                    (session_id, message_id, id_model, category, confidence,
                    input_token, output_token, cost, pool, created_at)
                VALUES ($1, $2,
                        (SELECT id_model FROM model WHERE model_name = $3),
                        $4, $5, $6, $7, $8, $9, NOW())
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

async def credit_quiz_bonus(
    session_id: int,
    correct_count: int,
    total_questions: int,
    tool_output_id: int,
    submitted_answers: dict,
) -> int:

    bonus_tokens = PERFECT_QUIZ_REWARD
    db_pool = await get_db_pool()

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            # Increment quiz_bonus atomically
            await conn.execute(
                "UPDATE session SET quiz_bonus = quiz_bonus + $1 WHERE id_session = $2",
                float(bonus_tokens),
                session_id,
            )

            # create the quiz attempt
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

            # Log the reward in route_log as a negative-cost quiz_reward entry.
            # id_model is NULL — quiz answer checking has no associated LLM model.
            await conn.execute(
                """
                    INSERT INTO route_log
                        (session_id, message_id, id_model, category, confidence,
                        input_token, output_token, cost, pool, created_at)
                    VALUES ($1, NULL, NULL, 'quiz_reward', 1.0,
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

async def get_budget_display(session_id: int) -> dict:
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

# Daily reset of visible_used and shadow_used with next_reset_at anchoring to prevent clock-skew exploits
async def run_daily_reset(session_id: int) -> bool:

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