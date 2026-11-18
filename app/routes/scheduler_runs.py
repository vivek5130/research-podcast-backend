"""HTTP routes for ``/api/runs`` (scheduler runs). Every route requires a signed-in user."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.controllers import scheduler_run_controller
from app.core.dependencies import get_current_user_id
from app.models.scheduler import SchedulerType
from app.schemas.scheduler_run import RunDetailResponse, RunSummaryResponse
from app.schemas.trace import RunTracesResponse

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("", response_model=list[RunSummaryResponse])
async def list_runs(
    scheduler_id: UUID | None = None,
    type: SchedulerType | None = None,
    limit: int = Query(50, ge=1, le=200),
    user_id: UUID = Depends(get_current_user_id),
):
    """List my runs across all schedulers, newest first (filter by ``scheduler_id`` or ``type``)."""
    return await scheduler_run_controller.list_runs(user_id, scheduler_id, type, limit)


@router.get("/{run_id}", response_model=RunDetailResponse)
async def get_run(run_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    """One run with its output (for podcasts: title, script, sources, signed ``audio_url``)."""
    return await scheduler_run_controller.get_run(run_id, user_id)


@router.post("/{run_id}/retry", response_model=RunDetailResponse, status_code=status.HTTP_202_ACCEPTED)
async def retry_run(run_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    """Retry a failed run from the stage that failed (earlier stages' output is reused)."""
    return await scheduler_run_controller.retry_run(run_id, user_id)


@router.get("/{run_id}/traces", response_model=RunTracesResponse)
async def get_run_traces(run_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    """Telemetry for one run: every Gemini attempt (prompt, response, tokens, latency) and tool call
    (Tavily searches, Gnani TTS chunks), with totals."""
    return await scheduler_run_controller.get_traces(run_id, user_id)
