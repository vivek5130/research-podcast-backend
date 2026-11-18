"""HTTP routes for ``/api/schedulers``. Every route requires a signed-in user."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.controllers import scheduler_controller, scheduler_run_controller
from app.core.dependencies import get_current_user_id
from app.models.scheduler import SchedulerType
from app.schemas.scheduler import (
    SchedulePreviewRequest,
    SchedulePreviewResponse,
    SchedulerCreate,
    SchedulerCreatedResponse,
    SchedulerResponse,
    SchedulerTypeResponse,
    SchedulerUpdate,
)
from app.schemas.scheduler_run import RunDetailResponse, RunSummaryResponse

router = APIRouter(prefix="/api/schedulers", tags=["schedulers"])


@router.get("/types", response_model=list[SchedulerTypeResponse])
async def list_scheduler_types(_: UUID = Depends(get_current_user_id)):
    """Supported scheduler types, their stages, and the JSON schema of their ``config``."""
    return scheduler_controller.list_types()


@router.get("", response_model=list[SchedulerResponse])
async def list_schedulers(type: SchedulerType | None = None, user_id: UUID = Depends(get_current_user_id)):
    """List my schedulers (optionally ``?type=podcast``)."""
    return await scheduler_controller.list_schedulers(user_id, type)


@router.post("/preview", response_model=SchedulePreviewResponse)
async def preview_schedule(body: SchedulePreviewRequest, _: UUID = Depends(get_current_user_id)):
    """Upcoming run times for a schedule (once/daily/weekly/monthly) without saving it."""
    return scheduler_controller.preview(body)


@router.post("", response_model=SchedulerCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_scheduler(body: SchedulerCreate, user_id: UUID = Depends(get_current_user_id)):
    """Create a scheduler. With ``frequency=once, run_now=true`` it also starts a run immediately."""
    return await scheduler_controller.create_scheduler(user_id, body)


@router.get("/{scheduler_id}", response_model=SchedulerResponse)
async def get_scheduler(scheduler_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    """Get one of my schedulers."""
    return await scheduler_controller.get_scheduler(scheduler_id, user_id)


@router.patch("/{scheduler_id}", response_model=SchedulerResponse)
async def update_scheduler(scheduler_id: UUID, body: SchedulerUpdate, user_id: UUID = Depends(get_current_user_id)):
    """Change name/config/time/timezone, or pause/resume with ``is_active``."""
    return await scheduler_controller.update_scheduler(scheduler_id, user_id, body)


@router.delete("/{scheduler_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scheduler(scheduler_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    """Delete a scheduler and all of its runs."""
    await scheduler_controller.delete_scheduler(scheduler_id, user_id)


@router.post("/{scheduler_id}/trigger", response_model=RunDetailResponse, status_code=status.HTTP_202_ACCEPTED)
async def trigger_scheduler(scheduler_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    """Start a run now ("run now"); the daily schedule is unchanged."""
    run_id = await scheduler_controller.trigger_run(scheduler_id, user_id)
    return await scheduler_run_controller.get_run(run_id, user_id)


@router.get("/{scheduler_id}/runs", response_model=list[RunSummaryResponse])
async def list_scheduler_runs(
    scheduler_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    user_id: UUID = Depends(get_current_user_id),
):
    """Runs of one of my schedulers, newest first."""
    await scheduler_controller.get_scheduler(scheduler_id, user_id)  # 404 if not mine
    return await scheduler_run_controller.list_runs(user_id, scheduler_id, None, limit)
