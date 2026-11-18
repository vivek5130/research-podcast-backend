"""Machine-to-machine routes (not for browsers).

* ``POST /internal/scheduler/tick``            - Supabase pg_cron, every minute (X-Cron-Secret).
* ``POST /internal/runs/{run_id}/stages/{stage}`` - Upstash QStash stage execution (signed).
"""
from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.controllers import stage_controller, tick_controller
from app.core.dependencies import verify_cron_secret, verify_qstash_request
from app.schemas.internal import StageResponse, TickResponse

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/scheduler/tick", response_model=TickResponse, dependencies=[Depends(verify_cron_secret)])
async def scheduler_tick():
    """Claim due schedulers, create their runs, and enqueue each run's first stage."""
    return await tick_controller.tick()


@router.post(
    "/runs/{run_id}/stages/{stage}", response_model=StageResponse, dependencies=[Depends(verify_qstash_request)]
)
async def execute_stage(run_id: str, stage: str, upstash_retried: int = Header(default=0)):
    """Execute one stage of a run. A 500 makes QStash retry; any 2xx stops retries."""
    try:
        return await stage_controller.run_stage(run_id, stage, upstash_retried)
    except stage_controller.RetryableStageError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc
