"""Data access for the ``llm_run_traces`` table (telemetry of LLM and tool calls)."""
from uuid import UUID

from app.core import database as db

_COLS = """id, run_id, stage, node, kind, name, requested_model, model, attempt, status, error,
           input, output, input_tokens, output_tokens, thinking_tokens, total_tokens, input_chars,
           latency_ms, metadata, started_at"""


async def insert(trace: dict) -> None:
    """Store one trace row."""
    await db.execute(
        """
        INSERT INTO llm_run_traces (run_id, user_id, stage, node, kind, name, requested_model, model, attempt,
                                    status, error, input, output, input_tokens, output_tokens, thinking_tokens,
                                    total_tokens, input_chars, latency_ms, metadata, started_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21)
        """,
        trace.get("run_id"), trace.get("user_id"), trace.get("stage"), trace.get("node"), trace["kind"],
        trace["name"], trace.get("requested_model"), trace.get("model"), trace.get("attempt", 1),
        trace["status"], trace.get("error"), trace.get("input", {}), trace.get("output", {}),
        trace.get("input_tokens"), trace.get("output_tokens"), trace.get("thinking_tokens"),
        trace.get("total_tokens"), trace.get("input_chars"), trace.get("latency_ms"),
        trace.get("metadata", {}), trace["started_at"],
    )


async def list_for_run(run_id: UUID, user_id: UUID) -> list[dict]:
    """All traces of a run, oldest first, only if the user owns the run."""
    return await db.fetch(
        f"""
        SELECT {_COLS} FROM llm_run_traces
        WHERE run_id = $1 AND user_id = $2
        ORDER BY started_at, created_at
        """,
        run_id, user_id,
    )
