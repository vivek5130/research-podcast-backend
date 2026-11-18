"""Response bodies for ``/api/runs``."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.scheduler import SchedulerType
from app.models.scheduler_run import RunStatus, RunTrigger


class RunSummaryResponse(BaseModel):
    """A run as shown in lists (no full output)."""

    id: UUID
    scheduler_id: UUID
    scheduler_name: str
    scheduler_type: SchedulerType
    trigger: RunTrigger
    status: RunStatus
    stage: str | None
    title: str | None = None  # convenience copy of output["title"] when the type sets one
    duration_sec: int | None = None  # convenience copy of output["duration_sec"] (podcast length)
    scheduled_for: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None
    attempts: int = 0  # stage executions started, automatic retries included
    retry_count: int = 0  # manual retries requested by the user
    created_at: datetime


class RunDetailResponse(RunSummaryResponse):
    """A run with its type-specific ``output``.

    The handler decides what is exposed: for podcasts the private ``audio_path`` is
    replaced by a short-lived signed ``audio_url``.
    """

    output: dict
