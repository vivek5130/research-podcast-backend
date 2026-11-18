"""Generic run executor: runs one stage of a scheduler run, whatever its type.

A run moves through its handler's ordered stages, one queued job per stage:

    pending --(stage 1)--> running --(stage 2)--> ... --(last stage)--> completed
                               \\------------------- any stage fails ---> failed

``scheduler_runs.stage`` records the stage to execute next (NULL = first stage),
which makes every execution **idempotent**:

* the run already finished                 -> skip
* this stage already finished              -> skip (and re-publish the next stage,
                                              in case that publish was lost)
* this is the expected stage                -> run it
"""
import asyncio
import logging
from typing import Literal

from app.core import tracing
from app.core.config import get_settings
from app.core.exceptions import PermanentJobError
from app.handlers.registry import get_handler
from app.models.scheduler_run import TERMINAL_STATUSES
from app.repositories import scheduler_run_repository as runs
from app.services import queue

log = logging.getLogger(__name__)

StageOutcome = Literal["ok", "skipped"]
LOCAL_RETRY_BACKOFF_SECONDS = 10  # local mode: wait 10s, 20s, 30s between retries


async def execute_stage(run_id: str, stage: str) -> StageOutcome:
    """Execute ``stage`` of run ``run_id`` and hand off to the next stage (or finish the run).

    Raises:
        PermanentJobError: retrying cannot help (unknown stage, no sources, ...).
        Exception: anything else is treated as transient and retried by the caller.
    """
    ctx = await runs.get_context(run_id)
    if ctx is None or ctx.run_status in TERMINAL_STATUSES:
        return "skipped"  # deleted, or a duplicate delivery after the run finished

    handler = get_handler(ctx.scheduler_type)
    if stage not in handler.stages:
        raise PermanentJobError(f"Stage '{stage}' does not exist for type '{ctx.scheduler_type}'")

    expected = ctx.stage or handler.first_stage
    if stage != expected:
        if handler.next_stage(stage) == expected:
            await queue.enqueue_stage(run_id, expected, ctx.retry_count)  # deduplicated if already published
        return "skipped"

    await runs.start_stage(run_id, stage)
    with tracing.run_scope(ctx.run_id, ctx.user_id, stage):  # every LLM/tool call below is traced to this run
        output_patch = await handler.run_stage(stage, ctx)
    next_stage = handler.next_stage(stage)
    await runs.complete_stage(run_id, output_patch, next_stage)

    if next_stage:
        await queue.enqueue_stage(run_id, next_stage, ctx.retry_count)
    else:
        await _notify(handler, ctx, {**ctx.output, **output_patch})
    return "ok"


async def _notify(handler, ctx, output: dict) -> None:
    """Run the type's completion side effects.

    The run is already ``completed`` and its output saved, so a notification
    failure is logged rather than raised. Raising would make the queue retry
    the stage and redo (and re-pay for) the work.
    """
    try:
        await handler.on_completed(ctx, output)
        await runs.mark_notified(ctx.run_id)
    except Exception:
        log.exception("notification failed for run %s", ctx.run_id)


async def execute_stage_locally(run_id: str, stage: str) -> None:
    """Local-dev transport (QUEUE_MODE=local): run a stage in-process with QStash-like retries."""
    attempts = get_settings().job_max_retries + 1
    for attempt in range(1, attempts + 1):
        try:
            await execute_stage(run_id, stage)
            return
        except PermanentJobError as exc:
            await runs.mark_failed(run_id, str(exc))
            return
        except Exception as exc:
            log.exception("stage %s failed for run %s (attempt %d/%d)", stage, run_id, attempt, attempts)
            if attempt == attempts:
                await runs.mark_failed(run_id, f"{stage}: {exc}")
            else:
                await asyncio.sleep(LOCAL_RETRY_BACKOFF_SECONDS * attempt)  # QStash also backs off between retries
