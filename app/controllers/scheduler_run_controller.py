"""Scheduler run business logic: listing runs, showing one run's output, and its telemetry traces."""
from uuid import UUID

from app.core.exceptions import ConflictError, NotFoundError
from app.handlers.registry import get_handler
from app.models.scheduler import SchedulerType
from app.repositories import scheduler_run_repository, trace_repository
from app.services import queue
from app.schemas.scheduler_run import RunDetailResponse, RunSummaryResponse
from app.schemas.trace import RunTracesResponse, TraceResponse, TraceSummary


async def list_runs(
    user_id: UUID, scheduler_id: UUID | None, scheduler_type: SchedulerType | None, limit: int,
) -> list[RunSummaryResponse]:
    """The user's runs, newest first, optionally filtered by scheduler or type."""
    rows = await scheduler_run_repository.list_for_user(user_id, scheduler_id, scheduler_type, limit)
    return [RunSummaryResponse(**r) for r in rows]


async def get_run(run_id: UUID, user_id: UUID) -> RunDetailResponse:
    """One run with its output, shaped by the type's handler (e.g. signed audio URL)."""
    row = await scheduler_run_repository.get_detail_for_user(run_id, user_id)
    if not row:
        raise NotFoundError("Run not found")
    row["output"] = await get_handler(row["scheduler_type"]).present_output(row["output"] or {})
    return RunDetailResponse(**row)


async def retry_run(run_id: UUID, user_id: UUID) -> RunDetailResponse:
    """Retry a failed run from the stage that failed.

    Output from stages that already succeeded is kept, so e.g. a failed audio
    stage is retried without re-running (and re-paying for) the research stage.

    Raises:
        NotFoundError: not the user's run.
        ConflictError: the run isn't failed (it's pending, running or completed).
    """
    row = await scheduler_run_repository.get_detail_for_user(run_id, user_id)
    if not row:
        raise NotFoundError("Run not found")
    if row["status"] != "failed":
        raise ConflictError(f"Only failed runs can be retried (this one is {row['status']})")
    stage = row["stage"] or get_handler(row["scheduler_type"]).first_stage
    if not await scheduler_run_repository.reset_for_retry(run_id, stage):
        raise ConflictError("This run is already being retried")
    # New generation = retry_count after the reset, so QStash doesn't dedupe it against the failed attempt.
    await queue.enqueue_stage(run_id, stage, generation=row["retry_count"] + 1)
    return await get_run(run_id, user_id)


async def get_traces(run_id: UUID, user_id: UUID) -> RunTracesResponse:
    """Every LLM attempt and tool call of a run, plus token/latency totals."""
    rows = await trace_repository.list_for_run(run_id, user_id)  # already scoped to the owner
    if not rows and not await scheduler_run_repository.get_detail_for_user(run_id, user_id):
        raise NotFoundError("Run not found")  # only pay for the ownership check when there are no traces
    traces = [TraceResponse(**r) for r in rows]
    llm = [t for t in traces if t.kind == "llm"]
    tools = [t for t in traces if t.kind == "tool"]
    ok_llm = [t for t in llm if t.status == "ok"]
    summary = TraceSummary(
        llm_calls=len(ok_llm),
        llm_failed_attempts=len(llm) - len(ok_llm),
        tool_calls=len(tools),
        tool_errors=sum(t.status == "error" for t in tools),
        input_tokens=sum(t.input_tokens or 0 for t in llm),
        output_tokens=sum(t.output_tokens or 0 for t in llm),
        thinking_tokens=sum(t.thinking_tokens or 0 for t in llm),
        total_tokens=sum(t.total_tokens or 0 for t in llm),
        max_input_chars=max((t.input_chars or 0 for t in llm), default=0),
        total_latency_ms=sum(t.latency_ms or 0 for t in traces),
        models_used=sorted({t.model for t in ok_llm if t.model}),
    )
    return RunTracesResponse(summary=summary, traces=traces)
