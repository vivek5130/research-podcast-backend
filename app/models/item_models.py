"""Request/response shapes for /ingest and /items (Pydantic validates input automatically)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator

SourceType = Literal["note", "url"]


class IngestRequest(BaseModel):
    """POST /ingest body. Send `content` for a note, or `url` for a web page."""

    source_type: SourceType
    content: str | None = Field(default=None, min_length=1, max_length=20_000)
    url: HttpUrl | None = None
    title: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def check_matching_field(self):
        """note -> needs content, url -> needs url."""
        if self.source_type == "note" and not self.content:
            raise ValueError("content is required when source_type is 'note'")
        if self.source_type == "url" and not self.url:
            raise ValueError("url is required when source_type is 'url'")
        return self


class ItemResponse(BaseModel):
    """One saved item as returned to the frontend."""

    id: int
    source_type: SourceType
    source_url: str | None = None
    title: str | None = None
    content: str
    created_at: datetime


class ItemListResponse(BaseModel):
    """GET /items response."""

    items: list[ItemResponse]
    count: int
