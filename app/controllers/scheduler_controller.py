"""Scheduler business logic: CRUD, schedule recalculation, previews, manual triggers.

Type-specific rules (config validation, default name, stages) are delegated to the
scheduler type's handler, so this module never mentions podcasts. Timing rules live
in ``utils/scheduling.py`` (once / daily / weekly / monthly).
"""
from datetime import datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core.exceptions import ConflictError, InvalidRequestError, NotFoundError
from app.handlers.registry import all_handlers, get_handler
from app.models.scheduler import Scheduler, SchedulerType
from app.models.scheduler_run import RunTrigger
from app.repositories import scheduler_repository, scheduler_run_repository
from app.schemas.scheduler import (
    SchedulePreviewRequest,
    SchedulePreviewResponse,
    SchedulerCreate,
    SchedulerCreatedResponse,
    SchedulerResponse,
    SchedulerTypeResponse,
    SchedulerUpdate,
)
from app.services import queue
from app.utils.scheduling import Frequency, describe, next_run_after

TIMING_FIELDS = {"frequency", "run_time", "timezone", "run_date", "days_of_week", "day_of_month"}


def to_response(s: Scheduler) -> SchedulerResponse:
    """API view of a scheduler, including a human-readable schedule description."""
    return SchedulerResponse(**s.model_dump(), schedule_description=describe(s.recurrence()))


def list_types() -> list[SchedulerTypeResponse]:
    """Supported scheduler types with their stages and config JSON schema."""
    return [
        SchedulerTypeResponse(
            type=h.type, description=h.description, stages=list(h.stages),
            config_schema=h.config_model.model_json_schema(),
        )
        for h in all_handlers()
    ]


def preview(body: SchedulePreviewRequest) -> SchedulePreviewResponse:
    """Upcoming run times for a schedule, computed by the same rule the tick uses."""
    if body.run_now:
        return SchedulePreviewResponse(description="Once, right now", next_runs=[datetime.now(timezone.utc)])
    rec = body.recurrence()
    runs: list[datetime] = []
    after = datetime.now(timezone.utc)
    while len(runs) < body.count and (nxt := next_run_after(rec, after)) is not None:
        runs.append(nxt)
        after = nxt
    return SchedulePreviewResponse(description=describe(rec), next_runs=runs)


async def _get_owned(scheduler_id: UUID, user_id: UUID) -> Scheduler:
    """Return the scheduler if the user owns it.

    Raises:
        NotFoundError: also for other users' schedulers, so ids are not leaked.
    """
    scheduler = await scheduler_repository.get_for_user(scheduler_id, user_id)
    if not scheduler:
        raise NotFoundError("Scheduler not found")
    return scheduler


async def list_schedulers(user_id: UUID, scheduler_type: SchedulerType | None) -> list[SchedulerResponse]:
    """The user's schedulers, optionally of one type."""
    return [to_response(s) for s in await scheduler_repository.list_for_user(user_id, scheduler_type)]


async def get_scheduler(scheduler_id: UUID, user_id: UUID) -> SchedulerResponse:
    """One of the user's schedulers."""
    return to_response(await _get_owned(scheduler_id, user_id))


async def create_scheduler(user_id: UUID, body: SchedulerCreate) -> SchedulerCreatedResponse:
    """Validate config + schedule and create the scheduler.

    ``frequency=once, run_now=true`` records a one-time schedule for "now" (already
    consumed, so inactive) and immediately starts a manual run.
    """
    handler = get_handler(body.type)
    config = handler.validate_config(body.config)
    rec = body.recurrence()
    now = datetime.now(timezone.utc)

    if body.run_now:
        local = now.astimezone(ZoneInfo(body.timezone))
        run_date, run_time, next_run_at, active = local.date(), local.time().replace(second=0, microsecond=0), now, False
    else:
        next_run_at = next_run_after(rec, now)
        if next_run_at is None:
            raise InvalidRequestError("That date and time has already passed. Pick a time in the future.")
        run_date, run_time, active = rec.run_date, rec.run_time, True

    scheduler = await scheduler_repository.create(
        {
            "user_id": user_id,
            "type": body.type.value,
            "name": (body.name or "").strip() or handler.default_name(config),
            "config": config,
            "frequency": body.frequency.value,
            "run_time": run_time,
            "run_date": run_date,
            "days_of_week": list(rec.days_of_week) if rec.days_of_week else None,
            "day_of_month": rec.day_of_month,
            "timezone": body.timezone,
            "next_run_at": next_run_at,
            "is_active": active,
        }
    )
    try:
        run_id = await trigger_run(scheduler.id, user_id) if body.run_now else None
    except queue.QueueError as exc:
        raise queue.QueueError(f"Schedule saved, but the first episode couldn't start. {exc.message}") from exc
    return SchedulerCreatedResponse(**to_response(scheduler).model_dump(), triggered_run_id=run_id)


async def update_scheduler(scheduler_id: UUID, user_id: UUID, body: SchedulerUpdate) -> SchedulerResponse:
    """Apply a partial update. Timing changes (or re-activating) recompute ``next_run_at``."""
    current = await _get_owned(scheduler_id, user_id)
    changes = body.model_dump(exclude_unset=True)
    changes = {k: v for k, v in changes.items() if v is not None or k in {"run_date", "days_of_week", "day_of_month"}}
    if "config" in changes:
        changes["config"] = get_handler(current.type).validate_config(changes["config"])
    updated = current.model_copy(update=changes)

    # Clear fields that don't belong to the (possibly new) frequency.
    if updated.frequency is not Frequency.ONCE:
        updated.run_date = None
    if updated.frequency is not Frequency.WEEKLY:
        updated.days_of_week = None
    if updated.frequency is not Frequency.MONTHLY:
        updated.day_of_month = None

    reactivated = changes.get("is_active") is True and not current.is_active
    if TIMING_FIELDS & changes.keys() or reactivated:
        try:
            rec = updated.recurrence().validate()
        except ValueError as exc:
            raise InvalidRequestError(str(exc)) from exc
        nxt = next_run_after(rec)
        if nxt is None:
            raise InvalidRequestError("That one-time slot has already passed. Pick a new date and time.")
        updated.next_run_at = nxt
        if TIMING_FIELDS & changes.keys() and "is_active" not in changes:
            updated.is_active = True  # rescheduling a finished one-time job brings it back
    return to_response(await scheduler_repository.update(updated))


async def delete_scheduler(scheduler_id: UUID, user_id: UUID) -> None:
    """Delete a scheduler and (via FK cascade) its runs."""
    if not await scheduler_repository.delete(scheduler_id, user_id):
        raise NotFoundError("Scheduler not found")


async def trigger_run(scheduler_id: UUID, user_id: UUID) -> UUID:
    """Start a run immediately ("run now") without changing the schedule.

    Returns:
        The new run's id.

    Raises:
        ConflictError: if this scheduler already has a run in progress.
    """
    scheduler = await _get_owned(scheduler_id, user_id)
    if await scheduler_run_repository.has_active_run(scheduler_id):
        raise ConflictError("A run for this scheduler is already in progress")
    run_id = await scheduler_run_repository.create_pending(
        scheduler_id, user_id, datetime.now(timezone.utc), RunTrigger.MANUAL
    )
    try:
        await queue.enqueue_stage(run_id, get_handler(scheduler.type).first_stage)
    except queue.QueueError as exc:
        # Don't leave a run stuck at "Queued": mark it failed (retryable from the episode page).
        await scheduler_run_repository.mark_failed(run_id, f"enqueue: {exc.message}")
        raise
    return run_id
