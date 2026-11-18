"""Data access for the ``scheduler_runs`` table.

Two groups of functions:
* user-facing reads, always filtered by ``user_id``.
* executor writes, which move a run through its ``RunStatus`` lifecycle and stages.
"""
from datetime import datetime
from uuid import UUID

from app.core import database as db
from app.models.scheduler import SchedulerType
from app.models.scheduler_run import ACTIVE_STATUSES, RunContext, RunTrigger

_SUMMARY_COLS = """r.id, r.scheduler_id, s.name AS scheduler_name, s.type AS scheduler_type,
                   r.trigger, r.status, r.stage, r.output->>'title' AS title,
                   (r.output->>'duration_sec')::int AS duration_sec, r.scheduled_for,
                   r.started_at, r.completed_at, r.error, r.attempts, r.retry_count, r.created_at"""


# ---------- user-facing reads ----------

async def list_for_user(
    user_id: UUID, scheduler_id: UUID | None = None, scheduler_type: SchedulerType | None = None, limit: int = 50,
) -> list[dict]:
    """Run summaries owned by the user, newest first, optionally filtered."""
    return await db.fetch(
        f"""
        SELECT {_SUMMARY_COLS}
        FROM scheduler_runs r JOIN schedulers s ON s.id = r.scheduler_id
        WHERE r.user_id = $1
          AND ($2::uuid IS NULL OR r.scheduler_id = $2)
          AND ($3::text IS NULL OR s.type = $3)
        ORDER BY r.scheduled_for DESC
        LIMIT $4
        """,
        user_id, scheduler_id, scheduler_type.value if scheduler_type else None, limit,
    )


async def get_detail_for_user(run_id: UUID, user_id: UUID) -> dict | None:
    """Full run (summary fields + output) only if the user owns it."""
    return await db.fetchrow(
        f"""
        SELECT {_SUMMARY_COLS}, r.output
        FROM scheduler_runs r JOIN schedulers s ON s.id = r.scheduler_id
        WHERE r.id = $1 AND r.user_id = $2
        """,
        run_id, user_id,
    )


async def has_active_run(scheduler_id: UUID, within_minutes: int = 15) -> bool:
    """True if this scheduler has a pending/running run created recently."""
    row = await db.fetchrow(
        """
        SELECT 1 FROM scheduler_runs
        WHERE scheduler_id = $1 AND status = ANY($2::text[])
          AND created_at > now() - make_interval(mins => $3)
        """,
        scheduler_id, [s.value for s in ACTIVE_STATUSES], within_minutes,
    )
    return row is not None


async def create_pending(scheduler_id: UUID, user_id: UUID, scheduled_for: datetime, trigger: RunTrigger) -> UUID:
    """Insert a new ``pending`` run and return its id."""
    row = await db.fetchrow(
        """
        INSERT INTO scheduler_runs (scheduler_id, user_id, scheduled_for, trigger, status)
        VALUES ($1, $2, $3, $4, 'pending') RETURNING id
        """,
        scheduler_id, user_id, scheduled_for, trigger.value,
    )
    return row["id"]


# ---------- executor writes ----------

async def get_context(run_id: UUID | str) -> RunContext | None:
    """Load what a stage needs: run state, scheduler type/config, and the owner's contact."""
    row = await db.fetchrow(
        """
        SELECT r.id AS run_id, r.status AS run_status, r.stage, r.output, r.retry_count,
               s.id AS scheduler_id, s.type AS scheduler_type, s.name AS scheduler_name, s.config,
               u.id AS user_id, u.email AS user_email, u.name AS user_name
        FROM scheduler_runs r
        JOIN schedulers s ON s.id = r.scheduler_id
        JOIN users u ON u.id = r.user_id
        WHERE r.id = $1
        """,
        _uuid(run_id),
    )
    return RunContext(**row) if row else None


async def start_stage(run_id: UUID | str, stage: str) -> None:
    """Mark the run ``running`` on ``stage`` (sets started_at on the first stage)."""
    await db.execute(
        """
        UPDATE scheduler_runs
        SET status = 'running', stage = $2, started_at = COALESCE(started_at, now()),
            attempts = attempts + 1, updated_at = now()
        WHERE id = $1
        """,
        _uuid(run_id), stage,
    )


async def complete_stage(run_id: UUID | str, output_patch: dict, next_stage: str | None) -> None:
    """Merge the stage's output and either move to ``next_stage`` or mark the run ``completed``."""
    if next_stage:
        await db.execute(
            "UPDATE scheduler_runs SET output = output || $2, stage = $3, updated_at = now() WHERE id = $1",
            _uuid(run_id), output_patch, next_stage,
        )
    else:
        await db.execute(
            """
            UPDATE scheduler_runs
            SET output = output || $2, status = 'completed', error = NULL,
                completed_at = now(), updated_at = now()
            WHERE id = $1
            """,
            _uuid(run_id), output_patch,
        )


async def reset_for_retry(run_id: UUID | str, stage: str) -> bool:
    """Turn a ``failed`` run back into ``pending`` at ``stage``, keeping output from earlier stages.

    Returns False if the run isn't failed (e.g. a concurrent retry already reset it).
    """
    status = await db.execute(
        """
        UPDATE scheduler_runs
        SET status = 'pending', stage = $2, error = NULL, completed_at = NULL,
            retry_count = retry_count + 1, updated_at = now()
        WHERE id = $1 AND status = 'failed'
        """,
        _uuid(run_id), stage,
    )
    return status.endswith(" 1")


async def mark_notified(run_id: UUID | str) -> None:
    """Record that the user was notified about the completed run."""
    await db.execute("UPDATE scheduler_runs SET notified_at = now() WHERE id = $1", _uuid(run_id))


async def mark_failed(run_id: UUID | str, error: str) -> None:
    """Mark the run ``failed`` and keep a (truncated) reason for the UI."""
    await db.execute(
        "UPDATE scheduler_runs SET status = 'failed', error = $2, updated_at = now() WHERE id = $1",
        _uuid(run_id), error[:1000],
    )


def _uuid(value: UUID | str) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))
