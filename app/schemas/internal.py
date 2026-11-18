"""Response bodies for machine-to-machine ``/internal`` endpoints."""
from pydantic import BaseModel


class TickResponse(BaseModel):
    """Result of one scheduler tick."""

    claimed: int   # runs created for due schedulers
    enqueued: int  # first stages published to the queue
    failed: int    # runs that could not be enqueued (marked failed)


class StageResponse(BaseModel):
    """Result of one stage execution. Any 2xx tells QStash not to retry."""

    status: str  # "ok" | "skipped" | "failed"
    error: str | None = None
