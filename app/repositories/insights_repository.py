"""Aggregates for the dashboard's quality & reliability insights (one user, recent runs)."""
from uuid import UUID

from app.core import database as db


async def run_rows(user_id: UUID, days: int) -> list[dict]:
    """Recent runs with the fields the insights need (evaluation lives in ``output``)."""
    return await db.fetch(
        """
        SELECT id, status, error, attempts, retry_count, started_at, completed_at,
               output->'evaluation' AS evaluation, output->'guardrails' AS guardrails
        FROM scheduler_runs
        WHERE user_id = $1 AND created_at > now() - make_interval(days => $2)
        """,
        user_id, days,
    )


async def run_costs(user_id: UUID, days: int) -> list[dict]:
    """Per-run LLM/tool totals from ``llm_run_traces``: tokens, calls, errors, time spent in calls."""
    return await db.fetch(
        """
        SELECT run_id,
               COALESCE(SUM(total_tokens), 0) AS tokens,
               COUNT(*) FILTER (WHERE kind = 'llm') AS llm_calls,
               COUNT(*) FILTER (WHERE status = 'error') AS errors,
               COALESCE(SUM(latency_ms), 0) AS call_ms
        FROM llm_run_traces
        WHERE user_id = $1 AND created_at > now() - make_interval(days => $2) AND run_id IS NOT NULL
        GROUP BY run_id
        """,
        user_id, days,
    )
