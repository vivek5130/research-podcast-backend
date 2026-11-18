"""Response bodies for ``GET /api/runs/{id}/traces`` (LLM + tool telemetry)."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class TraceResponse(BaseModel):
    """One LLM attempt or tool call."""

    id: UUID
    stage: str | None
    node: str | None
    kind: str  # llm | tool
    name: str  # gemini.generate_content | tavily.search | gnani.tts
    requested_model: str | None
    model: str | None
    attempt: int
    status: str  # ok | error
    error: str | None
    input: dict
    output: dict
    input_tokens: int | None
    output_tokens: int | None
    thinking_tokens: int | None
    total_tokens: int | None
    input_chars: int | None
    latency_ms: int | None
    metadata: dict
    started_at: datetime


class TraceSummary(BaseModel):
    """Totals for a run."""

    llm_calls: int
    llm_failed_attempts: int
    tool_calls: int
    tool_errors: int
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    total_tokens: int
    max_input_chars: int  # largest single prompt, to check the context budget
    total_latency_ms: int
    models_used: list[str]  # models that actually answered


class RunTracesResponse(BaseModel):
    summary: TraceSummary
    traces: list[TraceResponse]
