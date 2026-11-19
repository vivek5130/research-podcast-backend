"""Shared state and structured LLM outputs for the research agent."""
from functools import lru_cache
from typing import TypedDict

from pydantic import BaseModel, Field, create_model

from app.core.exceptions import PermanentJobError


class NoSourcesError(PermanentJobError):
    """The search found nothing recent for the topic; retrying will not help."""


class ResearchState(TypedDict, total=False):
    """Data passed between graph nodes. Each node returns only the keys it changes."""

    topic: str               # the user's request
    duration_seconds: int    # requested episode length (sizes words, paragraphs, sources, context)
    language: str            # Gnani language code; the script is written in this language
    today: str               # human-readable date, given to the LLM for recency
    queries: list[str]       # plan_queries
    raw_results: list[dict]  # search (all hits, possibly duplicated)
    sources: list[dict]      # curate (deduplicated, ranked, capped)
    title: str               # write_script
    script: str              # write_script
    word_count: int          # write_script
    revisions: int           # check_length: how many rewrites were requested
    feedback: str            # check_length: instructions for the next rewrite ("" = accepted)
    best: dict               # check_length: draft closest to the target so far {title, script, word_count}
    guardrails: dict         # guard_* nodes: what was redacted/blocked (PII types and counts, never values)
    evaluation: dict         # evaluate: per-episode quality metrics


class QueryPlan(BaseModel):
    """LLM output of the planning step, including an input-moderation verdict (no extra LLM call)."""

    safe: bool = Field(default=True, description="False if the request seeks harmful, hateful, abusive, sexual or illegal content")
    unsafe_category: str | None = Field(default=None, description="Short category when safe is false, e.g. 'hate speech'")
    queries: list[str] = Field(default_factory=list, max_length=5)


class ScriptBase(BaseModel):
    """LLM output of the writing step: a title and the script as a list of spoken paragraphs.

    The paragraph count is enforced through the structured-output schema (minItems /
    maxItems). That keeps even small "lite" models close to the requested length;
    a plain word-count instruction alone is often ignored.
    """

    title: str
    paragraphs: list[str]

    @property
    def script(self) -> str:
        return "\n\n".join(p.strip() for p in self.paragraphs if p.strip())


@lru_cache
def script_model(min_paragraphs: int, max_paragraphs: int) -> type[ScriptBase]:
    """A ScriptBase subclass whose JSON schema requires ``min..max`` paragraphs (one per episode length)."""
    return create_model(
        f"Script{min_paragraphs}to{max_paragraphs}",
        __base__=ScriptBase,
        title=(str, Field(description="Short on-screen title, under 70 characters, written style with digits")),
        paragraphs=(
            list[str],
            Field(
                min_length=min_paragraphs,
                max_length=max_paragraphs,
                description="Spoken paragraphs in order, about 75 words each (4-5 sentences)",
            ),
        ),
    )
