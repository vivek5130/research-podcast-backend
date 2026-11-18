"""Request/response shapes for /query (RAG question answering)."""

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """POST /query body."""

    question: str = Field(min_length=1, max_length=1_000)
    top_k: int = Field(default=5, ge=1, le=20)  # how many chunks to retrieve


class SourceSnippet(BaseModel):
    """A retrieved chunk used as evidence for the answer (shown as a citation)."""

    item_id: int
    chunk_id: int
    title: str | None = None
    text: str
    score: float  # similarity score, higher = more relevant


class QueryResponse(BaseModel):
    """LLM answer + the sources it was based on."""

    answer: str
    sources: list[SourceSnippet]
