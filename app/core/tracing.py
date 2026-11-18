"""Telemetry for LLM and tool calls, stored in ``llm_run_traces``.

How it works:

* The run executor opens ``run_scope(run_id, user_id, stage)`` around each stage.
* Agent nodes run inside ``node_scope("plan_queries")`` (or use ``@traced_node``).
* The Gemini, Tavily and Gnani wrappers call ``record(...)`` after every attempt.

The context lives in ``contextvars``, so it flows automatically into asyncio tasks
(e.g. parallel Tavily searches) and the instrumented services never need run ids
passed through their signatures. Recording is best-effort: a telemetry failure is
logged and never breaks an episode. Outside a run (e.g. ``scripts/run_agent.py``)
traces are kept in memory via ``collect()`` instead of the database.
"""
import functools
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

log = logging.getLogger(__name__)

MAX_FIELD_CHARS = 20_000  # truncate stored prompts/responses to keep rows small


@dataclass(frozen=True)
class RunContext:
    run_id: UUID | str | None
    user_id: UUID | str | None
    stage: str | None


_run: ContextVar[RunContext | None] = ContextVar("trace_run", default=None)
_node: ContextVar[str | None] = ContextVar("trace_node", default=None)
_collector: ContextVar[list[dict] | None] = ContextVar("trace_collector", default=None)


def clip(text: str | None, limit: int = MAX_FIELD_CHARS) -> str | None:
    """Truncate long text, noting how much was dropped."""
    if text is None or len(text) <= limit:
        return text
    return f"{text[:limit]}… [truncated {len(text) - limit} chars]"


def now() -> datetime:
    return datetime.now(timezone.utc)


@contextmanager
def run_scope(run_id: UUID | str, user_id: UUID | str, stage: str) -> Iterator[None]:
    """Attribute every trace recorded inside to this run and stage."""
    token = _run.set(RunContext(run_id, user_id, stage))
    try:
        yield
    finally:
        _run.reset(token)


@contextmanager
def node_scope(name: str) -> Iterator[None]:
    """Attribute every trace recorded inside to this agent node / step."""
    token = _node.set(name)
    try:
        yield
    finally:
        _node.reset(token)


def traced_node(name: str):
    """Decorator form of ``node_scope`` for async functions (LangGraph nodes)."""

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            with node_scope(name):
                return await fn(*args, **kwargs)

        return wrapper

    return decorator


@contextmanager
def collect() -> Iterator[list[dict]]:
    """Collect traces in memory (used by the CLI, where there is no run to attach them to)."""
    bucket: list[dict] = []
    token = _collector.set(bucket)
    try:
        yield bucket
    finally:
        _collector.reset(token)


async def record(
    *,
    kind: str,
    name: str,
    status: str,
    started_at: datetime,
    latency_ms: int,
    input: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
    **fields: Any,
) -> None:
    """Store one trace (LLM attempt or tool call). Never raises."""
    ctx = _run.get()
    trace = {
        "run_id": ctx.run_id if ctx else None,
        "user_id": ctx.user_id if ctx else None,
        "stage": ctx.stage if ctx else None,
        "node": _node.get(),
        "kind": kind,
        "name": name,
        "status": status,
        "started_at": started_at,
        "latency_ms": latency_ms,
        "input": input or {},
        "output": output or {},
        **fields,
    }
    bucket = _collector.get()
    if bucket is not None:
        bucket.append(trace)
    if ctx is None or ctx.run_id is None:
        return
    try:
        from app.repositories import trace_repository  # local import keeps services free of DB imports at load

        await trace_repository.insert(trace)
    except Exception:
        log.exception("failed to store trace %s/%s", kind, name)
