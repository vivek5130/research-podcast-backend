"""Per-episode quality metrics, stored in ``scheduler_runs.output.evaluation``.

The metrics follow common practice for retrieval-grounded generation:

* **Faithfulness** (RAGAS definition): claims in the script that are supported by the
  retrieved sources / all claims. The main hallucination signal.
* **Topic relevance** (G-Eval style rubric, 1-5): does the episode cover what was asked?
* **Source utilization** (a context-precision proxy): share of retrieved sources the
  supported claims actually use.
* **Freshness**: share of sources published within 3 days (the brief asks for current news).
* **Source diversity**: distinct outlets / sources, so one site doesn't dominate.
* **Length accuracy**: words vs the target (and, after TTS, seconds vs the requested length).
* **Safety**: what the guardrails redacted or blocked in this run.

Faithfulness and relevance come from one LLM-as-judge call (temperature 0); the rest is
computed. Research shows LLM judges can be lenient on faithfulness, so the judge must cite
a source number for each supported claim, and unsupported claims are listed for review.
The judge never fails an episode: if it errors, those two metrics are simply null.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from app.handlers.podcast.agent import prompts
from app.handlers.podcast.agent.text_utils import format_sources
from app.services import llm

log = logging.getLogger(__name__)

JUDGE_CONTEXT_CHARS = 14_000  # sources shown to the judge (kept small: one extra lite-model call per episode)
MAX_CLAIMS = 25
FRESH_WITHIN = timedelta(days=3)


class Claim(BaseModel):
    claim: str = Field(description="One factual statement from the script, in a short sentence")
    supported: bool = Field(description="True only if the cited source states this fact")
    source: int | None = Field(default=None, description="Number of the supporting source, or null")


class JudgeVerdict(BaseModel):
    claims: list[Claim] = Field(max_length=MAX_CLAIMS)
    relevance: int = Field(ge=1, le=5, description="How well the episode covers the requested topic (1-5)")
    relevance_reason: str = Field(description="One sentence explaining the relevance score")


def _published(value: str | None) -> datetime | None:
    if not value:
        return None
    for parse in (lambda v: datetime.fromisoformat(v.replace("Z", "+00:00")), parsedate_to_datetime):
        try:
            dt = parse(value)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def _ratio(part: float, whole: float) -> float | None:
    return round(part / whole, 3) if whole else None


def static_metrics(sources: list[dict], word_count: int, target_words: int) -> dict:
    """Metrics that need no LLM: freshness, diversity, length accuracy."""
    now = datetime.now(timezone.utc)
    dates = [_published(s.get("published_date")) for s in sources]
    dated = [d for d in dates if d]
    domains = {urlsplit(s["url"]).netloc.removeprefix("www.") for s in sources}
    return {
        "sources": len(sources),
        "freshness": _ratio(sum(now - d <= FRESH_WITHIN for d in dated), len(dated)),
        "source_diversity": _ratio(len(domains), len(sources)),
        "word_count": word_count,
        "target_words": target_words,
        "length_accuracy": round(max(0.0, 1 - abs(word_count - target_words) / target_words), 3) if target_words else None,
    }


async def judge(topic: str, script: str, sources: list[dict]) -> dict:
    """LLM-as-judge: faithfulness, unsupported claims, source utilization, topic relevance."""
    verdict = await llm.generate_structured(
        prompts.JUDGE_PROMPT.format(
            topic=topic, max_claims=MAX_CLAIMS, script=script,
            sources=format_sources(sources, JUDGE_CONTEXT_CHARS),
        ),
        JudgeVerdict,
        system=prompts.JUDGE_SYSTEM,
        temperature=0.0,
    )
    claims = verdict.claims
    supported = [c for c in claims if c.supported and c.source and 1 <= c.source <= len(sources)]
    return {
        "claims_checked": len(claims),
        "claims_supported": len(supported),
        "faithfulness": _ratio(len(supported), len(claims)),
        "unsupported_claims": [c.claim for c in claims if c not in supported][:5],
        "source_utilization": _ratio(len({c.source for c in supported}), len(sources)),
        "relevance": verdict.relevance,
        "relevance_reason": verdict.relevance_reason[:300],
    }


async def evaluate(topic: str, script: str, sources: list[dict], word_count: int, target_words: int,
                   with_judge: bool = True) -> dict:
    """All episode metrics; the judge part is best-effort."""
    metrics = static_metrics(sources, word_count, target_words)
    if with_judge:
        try:
            metrics.update(await judge(topic, script, sources))
        except Exception as exc:  # quota or model errors must not fail the episode
            log.warning("evaluation judge failed: %s", exc)
            metrics.update({"faithfulness": None, "relevance": None, "judge_error": type(exc).__name__})
    return metrics


def duration_accuracy(actual_seconds: int, target_seconds: int) -> float | None:
    """1.0 = exactly the requested length; 0.75 = 25% off."""
    return round(max(0.0, 1 - abs(actual_seconds - target_seconds) / target_seconds), 3) if target_seconds else None
