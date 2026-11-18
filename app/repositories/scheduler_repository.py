"""Data access for the ``schedulers`` table, including the tick's claim logic.

Every user-facing function takes ``user_id`` and filters by it, so one user can
never read or modify another user's schedulers.
"""
from datetime import datetime, timezone
from uuid import UUID

from app.core import database as db
from app.models.scheduler import Scheduler, SchedulerType
from app.utils.scheduling import next_run_after

_COLS = (
    "id, user_id, type, name, config, frequency, run_time, run_date, days_of_week, day_of_month, "
    "timezone, next_run_at, is_active, created_at, updated_at"
)


async def list_for_user(user_id: UUID, scheduler_type: SchedulerType | None = None) -> list[Scheduler]:
    """All schedulers owned by the user (optionally of one type), newest first."""
    rows = await db.fetch(
        f"""
        SELECT {_COLS} FROM schedulers
        WHERE user_id = $1 AND ($2::text IS NULL OR type = $2)
        ORDER BY created_at DESC
        """,
        user_id, scheduler_type.value if scheduler_type else None,
    )
    return [Scheduler(**r) for r in rows]


async def get_for_user(scheduler_id: UUID, user_id: UUID) -> Scheduler | None:
    """One scheduler, only if the user owns it."""
    row = await db.fetchrow(f"SELECT {_COLS} FROM schedulers WHERE id = $1 AND user_id = $2", scheduler_id, user_id)
    return Scheduler(**row) if row else None


async def create(scheduler: dict) -> Scheduler:
    """Insert a scheduler (dict with the Scheduler fields except id/timestamps) and return it."""
    row = await db.fetchrow(
        f"""
        INSERT INTO schedulers (user_id, type, name, config, frequency, run_time, run_date,
                                days_of_week, day_of_month, timezone, next_run_at, is_active)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        RETURNING {_COLS}
        """,
        scheduler["user_id"], scheduler["type"], scheduler["name"], scheduler["config"], scheduler["frequency"],
        scheduler["run_time"], scheduler.get("run_date"), scheduler.get("days_of_week"), scheduler.get("day_of_month"),
        scheduler["timezone"], scheduler["next_run_at"], scheduler.get("is_active", True),
    )
    return Scheduler(**row)


async def update(scheduler: Scheduler) -> Scheduler:
    """Persist the editable fields of ``scheduler`` (scoped to its owner)."""
    row = await db.fetchrow(
        f"""
        UPDATE schedulers
        SET name = $3, config = $4, frequency = $5, run_time = $6, run_date = $7, days_of_week = $8,
            day_of_month = $9, timezone = $10, is_active = $11, next_run_at = $12, updated_at = now()
        WHERE id = $1 AND user_id = $2
        RETURNING {_COLS}
        """,
        scheduler.id, scheduler.user_id, scheduler.name, scheduler.config, scheduler.frequency.value,
        scheduler.run_time, scheduler.run_date, scheduler.days_of_week, scheduler.day_of_month,
        scheduler.timezone, scheduler.is_active, scheduler.next_run_at,
    )
    return Scheduler(**row)


async def delete(scheduler_id: UUID, user_id: UUID) -> bool:
    """Delete a scheduler (its runs cascade). Returns False if nothing was deleted."""
    status = await db.execute("DELETE FROM schedulers WHERE id = $1 AND user_id = $2", scheduler_id, user_id)
    return status.endswith(" 1")


async def claim_due_and_create_runs(limit: int = 50) -> list[tuple[UUID, SchedulerType]]:
    """Claim due schedulers, advance each to its next occurrence, and create their runs.

    Runs in ONE transaction:
    1. ``SELECT ... FOR UPDATE SKIP LOCKED`` locks the due rows, so an overlapping tick
       skips rows already claimed instead of waiting or double-claiming.
    2. For each row, ``next_run_after`` (the same rule the API uses) computes the next
       local occurrence. One-time schedulers have none, so they are deactivated.
    3. One ``pending`` run is inserted per (scheduler, slot); the unique key makes a
       repeated tick a no-op.

    Returns ``(run_id, type)`` for every new run so the caller can enqueue its first stage.
    """
    now = datetime.now(timezone.utc)
    created: list[tuple[UUID, SchedulerType]] = []
    async with db.transaction() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {_COLS} FROM schedulers
            WHERE is_active AND next_run_at <= now()
            ORDER BY next_run_at
            LIMIT $1
            FOR UPDATE SKIP LOCKED
            """,
            limit,
        )
        for row in rows:
            scheduler = Scheduler(**dict(row))
            following = next_run_after(scheduler.recurrence(), after=now)
            await conn.execute(
                """
                UPDATE schedulers
                SET next_run_at = COALESCE($2, next_run_at), is_active = $3, updated_at = now()
                WHERE id = $1
                """,
                scheduler.id, following, following is not None,
            )
            run_id = await conn.fetchval(
                """
                INSERT INTO scheduler_runs (scheduler_id, user_id, scheduled_for, trigger, status)
                VALUES ($1, $2, $3, 'scheduled', 'pending')
                ON CONFLICT (scheduler_id, scheduled_for) DO NOTHING
                RETURNING id
                """,
                scheduler.id, scheduler.user_id, scheduler.next_run_at,
            )
            if run_id:
                created.append((run_id, scheduler.type))
    return created
