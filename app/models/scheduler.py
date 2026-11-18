"""Scheduler domain model (mirrors the ``schedulers`` table)."""
from datetime import date, datetime, time
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel

from app.utils.scheduling import Frequency, Recurrence


class SchedulerType(StrEnum):
    """What a scheduler produces. Each type has a handler in ``app/handlers``."""

    PODCAST = "podcast"


class Scheduler(BaseModel):
    """A user's task: run ``type`` with ``config`` on a schedule in ``timezone``.

    The schedule is ``frequency`` (once / daily / weekly / monthly) at ``run_time``,
    plus ``run_date`` (once), ``days_of_week`` (weekly) or ``day_of_month`` (monthly).
    ``next_run_at`` is the UTC instant of the next run; the tick compares it with
    ``now()`` and advances it after each run. One-time schedulers deactivate after firing.
    """

    id: UUID
    user_id: UUID
    type: SchedulerType
    name: str
    config: dict
    frequency: Frequency = Frequency.DAILY
    run_time: time
    run_date: date | None = None
    days_of_week: list[int] | None = None
    day_of_month: int | None = None
    timezone: str
    next_run_at: datetime
    is_active: bool
    created_at: datetime
    updated_at: datetime

    def recurrence(self) -> Recurrence:
        """The scheduling rule, for ``utils.scheduling.next_run_after``."""
        return Recurrence(
            frequency=Frequency(self.frequency),
            run_time=self.run_time,
            timezone=self.timezone,
            run_date=self.run_date,
            days_of_week=tuple(self.days_of_week) if self.days_of_week else None,
            day_of_month=self.day_of_month,
        )
