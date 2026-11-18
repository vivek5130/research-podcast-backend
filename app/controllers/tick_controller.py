"""Scheduler tick: find due schedulers and hand their runs to the queue.

Called every minute by Supabase pg_cron (see ``supabase/migrations/002_cron.sql``).
"""
import logging

from app.handlers.registry import get_handler
from app.repositories import scheduler_repository, scheduler_run_repository
from app.schemas.internal import TickResponse
from app.services import queue

log = logging.getLogger(__name__)


async def tick() -> TickResponse:
    """Claim every due scheduler (atomically), create its run, and enqueue the first stage.

    Safe to call concurrently or repeatedly: the claim query skips locked rows and
    ``scheduler_runs`` allows only one run per (scheduler, scheduled slot).
    """
    claimed = await scheduler_repository.claim_due_and_create_runs()
    enqueued = failed = 0
    for run_id, scheduler_type in claimed:
        try:
            await queue.enqueue_stage(run_id, get_handler(scheduler_type).first_stage)
            enqueued += 1
        except Exception as exc:  # leave a visible trace instead of a silently stuck run
            failed += 1
            log.exception("enqueue failed for run %s", run_id)
            await scheduler_run_repository.mark_failed(run_id, f"enqueue: {exc}")
    return TickResponse(claimed=len(claimed), enqueued=enqueued, failed=failed)
