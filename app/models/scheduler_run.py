"""Scheduler run domain model (mirrors the ``scheduler_runs`` table) and its lifecycle."""
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel

from app.models.scheduler import SchedulerType


class RunStatus(StrEnum):
    """Generic lifecycle shared by every scheduler type.

    pending -> running -> completed
                     \\-> failed      (``error`` explains why)

    *Which* step a running run is on is recorded separately in ``stage``
    (podcast stages: ``research`` -> ``synthesize``).
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


ACTIVE_STATUSES = {RunStatus.PENDING, RunStatus.RUNNING}
TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED}


class RunTrigger(StrEnum):
    """Why a run was created."""

    SCHEDULED = "scheduled"  # by the every-minute tick
    MANUAL = "manual"        # by the user ("run now")


class SchedulerRun(BaseModel):
    """One execution of a scheduler."""

    id: UUID
    scheduler_id: UUID
    user_id: UUID
    trigger: RunTrigger
    scheduled_for: datetime
    status: RunStatus
    stage: str | None = None
    output: dict = {}
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    notified_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class RunContext(BaseModel):
    """Everything a stage needs, joined from run + scheduler + user."""

    run_id: UUID
    run_status: RunStatus
    stage: str | None
    output: dict
    scheduler_id: UUID
    scheduler_type: SchedulerType
    scheduler_name: str
    config: dict
    user_id: UUID
    user_email: str
    user_name: str | None = None
    retry_count: int = 0  # manual retries so far; keys queue deduplication per attempt
