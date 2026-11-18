"""Queue-facing entry point: execute one run stage and turn the outcome into a retry decision.

QStash retries any non-2xx response, so the result here *is* the retry policy:

* success or duplicate             -> 200 ``ok`` / ``skipped``
* ``PermanentJobError``            -> mark run failed, 200 (retrying won't help)
* other error, retries remain      -> raise ``RetryableStageError`` (route returns 500, QStash retries)
* other error, last retry used     -> mark run failed, 200 (stop retrying)
"""
import logging

from app.core.config import get_settings
from app.core.exceptions import PermanentJobError
from app.repositories import scheduler_run_repository
from app.schemas.internal import StageResponse
from app.workers import run_executor

log = logging.getLogger(__name__)


class RetryableStageError(Exception):
    """The stage failed but the queue should retry it."""


async def run_stage(run_id: str, stage: str, retried: int) -> StageResponse:
    """Execute ``stage`` of ``run_id``. ``retried`` = how many times QStash already retried it."""
    try:
        outcome = await run_executor.execute_stage(run_id, stage)
    except PermanentJobError as exc:
        await scheduler_run_repository.mark_failed(run_id, str(exc))
        return StageResponse(status="failed", error=str(exc))
    except Exception as exc:
        log.exception("stage %s failed for run %s (retry %d)", stage, run_id, retried)
        if retried >= get_settings().job_max_retries:
            await scheduler_run_repository.mark_failed(run_id, f"{stage}: {exc}")
            return StageResponse(status="failed", error=str(exc))
        raise RetryableStageError(f"{stage} failed; will retry") from exc
    return StageResponse(status=outcome)
