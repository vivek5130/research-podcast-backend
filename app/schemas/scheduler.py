"""Request/response bodies for ``/api/schedulers``.

Timing is validated here (frequency + its fields, timezone). ``config`` is validated
by the scheduler type's handler in the controller (podcast: ``{"topic": "..."}``).

Schedule fields by frequency::

    once     run_date + run_time        (or run_now: true to start immediately)
    daily    run_time
    weekly   days_of_week [0=Mon..6=Sun] + run_time
    monthly  day_of_month [1..31]        + run_time   (short months -> last day)
"""
from datetime import date, datetime, time
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.scheduler import SchedulerType
from app.utils.scheduling import Frequency, Recurrence, validate_timezone


def _minute(value: time | None) -> time | None:
    """The tick runs once a minute, so seconds are meaningless."""
    return value.replace(second=0, microsecond=0) if value else value


def _dedupe_days(value: list[int] | None) -> list[int] | None:
    return sorted(set(value)) if value else value


class ScheduleFields(BaseModel):
    """The timing part shared by create and preview requests."""

    frequency: Frequency = Frequency.DAILY
    run_time: time | None = None
    timezone: str = "Asia/Kolkata"
    run_date: date | None = None
    days_of_week: list[int] | None = Field(default=None, description="0 = Monday ... 6 = Sunday")
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    run_now: bool = Field(default=False, description="once only: start immediately instead of at run_date/run_time")

    _run_time = field_validator("run_time")(classmethod(lambda cls, v: _minute(v)))
    _days = field_validator("days_of_week")(classmethod(lambda cls, v: _dedupe_days(v)))

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        return validate_timezone(value)

    @model_validator(mode="after")
    def _check_schedule(self):
        if self.run_now:
            if self.frequency is not Frequency.ONCE:
                raise ValueError("run_now is only allowed for a one-time schedule")
            return self
        if self.run_time is None:
            raise ValueError("run_time is required")
        self.recurrence().validate()
        return self

    def recurrence(self) -> Recurrence:
        return Recurrence(
            frequency=self.frequency,
            run_time=self.run_time or time(0, 0),
            timezone=self.timezone,
            run_date=self.run_date if self.frequency is Frequency.ONCE else None,
            days_of_week=tuple(self.days_of_week) if self.frequency is Frequency.WEEKLY and self.days_of_week else None,
            day_of_month=self.day_of_month if self.frequency is Frequency.MONTHLY else None,
        )


class SchedulerCreate(ScheduleFields):
    """Create a scheduler.

    Examples::

        {"type": "podcast", "frequency": "daily", "run_time": "08:00", "timezone": "Asia/Kolkata",
         "config": {"topic": "Startup funding announced in the last 24 hours"}}
        {"frequency": "weekly", "days_of_week": [0, 2, 4], "run_time": "07:30", ...}
        {"frequency": "once", "run_now": true, ...}
    """

    type: SchedulerType = SchedulerType.PODCAST
    name: str | None = Field(default=None, max_length=200, description="Defaults to a name derived from config")
    config: dict = Field(default_factory=dict)


class SchedulerUpdate(BaseModel):
    """Partial update; omitted fields are left unchanged. ``type`` cannot change.

    Changing any timing field re-validates the whole schedule and recomputes next_run_at.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    config: dict | None = None
    frequency: Frequency | None = None
    run_time: time | None = None
    timezone: str | None = None
    run_date: date | None = None
    days_of_week: list[int] | None = None
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    is_active: bool | None = None

    _run_time = field_validator("run_time")(classmethod(lambda cls, v: _minute(v)))
    _days = field_validator("days_of_week")(classmethod(lambda cls, v: _dedupe_days(v)))

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str | None) -> str | None:
        return validate_timezone(value) if value else value


class SchedulerResponse(BaseModel):
    """A scheduler as shown to its owner."""

    id: UUID
    type: SchedulerType
    name: str
    config: dict
    frequency: Frequency
    run_time: time
    run_date: date | None
    days_of_week: list[int] | None
    day_of_month: int | None
    timezone: str
    schedule_description: str
    next_run_at: datetime
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SchedulerCreatedResponse(SchedulerResponse):
    """Create response; ``triggered_run_id`` is set when ``run_now`` started a run immediately."""

    triggered_run_id: UUID | None = None


class SchedulePreviewRequest(ScheduleFields):
    """Compute upcoming run times without saving anything (used by the form's live preview)."""

    count: int = Field(default=3, ge=1, le=10)


class SchedulePreviewResponse(BaseModel):
    description: str
    next_runs: list[datetime]  # UTC instants, earliest first (empty if a one-time slot is in the past)


class SchedulerTypeResponse(BaseModel):
    """A scheduler type the API supports, with the JSON schema of its ``config``."""

    type: SchedulerType
    description: str
    stages: list[str]
    config_schema: dict
