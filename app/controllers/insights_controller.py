"""Dashboard insights: quality, reliability, cost/latency and guardrail activity across a user's recent episodes.

Quality metrics are the per-episode ``output.evaluation`` values (see
``handlers/podcast/agent/evaluation.py``) averaged over completed episodes; cost and
latency come from ``llm_run_traces``; reliability from run statuses and errors.
"""
from statistics import mean
from uuid import UUID

from app.repositories import insights_repository

QUALITY_KEYS = ("faithfulness", "relevance", "freshness", "source_diversity", "source_utilization",
                "length_accuracy", "duration_accuracy")
SAFETY_KEYS = ("pii_redactions", "queries_blocked", "injections_removed", "ungrounded_removed", "unsafe_removed")


def failure_reason(error: str | None) -> str:
    """Group raw run errors into a few readable buckets."""
    e = (error or "").lower()
    if e.startswith("blocked by content policy"):
        return "Blocked by content policy"
    if "no recent sources" in e:
        return "No recent sources found"
    if e.startswith("enqueue") or "qstash" in e:
        return "Job queue (QStash)"
    if e.startswith("synthesize") or "gnani" in e or "voice generation" in e:
        return "Voice generation (Gnani)"
    if "gemini" in e or "quota" in e or "resource_exhausted" in e or "429" in e:
        return "LLM (Gemini) quota or error"
    if e.startswith("research"):
        return "Research / script"
    return "Other"


def _avg(values: list[float]) -> float | None:
    values = [v for v in values if isinstance(v, (int, float))]
    return round(mean(values), 3) if values else None


async def get_insights(user_id: UUID, days: int = 30) -> dict:
    """Everything the dashboard's Insights section shows, for runs in the last ``days`` days."""
    runs = await insights_repository.run_rows(user_id, days)
    costs = {r["run_id"]: r for r in await insights_repository.run_costs(user_id, days)}
    completed = [r for r in runs if r["status"] == "completed"]
    failed = [r for r in runs if r["status"] == "failed"]
    finished = len(completed) + len(failed)

    evals = [r["evaluation"] or {} for r in completed]
    quality = {k: _avg([e.get(k) for e in evals]) for k in QUALITY_KEYS}
    quality["relevance"] = _avg([e.get("relevance") for e in evals])  # 1-5 scale
    quality["episodes_scored"] = sum(1 for e in evals if e.get("faithfulness") is not None)
    quality["avg_sources"] = _avg([e.get("sources") for e in evals])

    reasons: dict[str, int] = {}
    for r in failed:
        reasons[failure_reason(r["error"])] = reasons.get(failure_reason(r["error"]), 0) + 1

    done_costs = [costs[r["id"]] for r in completed if r["id"] in costs]
    durations = [(r["completed_at"] - r["started_at"]).total_seconds()
                 for r in completed if r["completed_at"] and r["started_at"]]

    safety = {k: 0 for k in SAFETY_KEYS}
    policy_blocks = reasons.get("Blocked by content policy", 0)
    for r in runs:
        for k, v in ((r["evaluation"] or {}).get("safety") or {}).items():
            if k in safety and isinstance(v, int):
                safety[k] += v
    safety["policy_blocks"] = policy_blocks

    return {
        "days": days,
        "runs": len(runs),
        "quality": quality,
        "reliability": {
            "completed": len(completed),
            "failed": len(failed),
            "in_progress": len(runs) - finished,
            "success_rate": round(len(completed) / finished, 3) if finished else None,
            "manual_retries": sum(r["retry_count"] or 0 for r in runs),
            "automatic_retries": sum(max((r["attempts"] or 0) - 2, 0) for r in completed)
            + sum(max((r["attempts"] or 0) - 1, 0) for r in failed),
            "failure_reasons": sorted(({"reason": k, "count": v} for k, v in reasons.items()),
                                      key=lambda x: -x["count"]),
        },
        "cost": {
            "avg_tokens": _avg([c["tokens"] for c in done_costs]),
            "avg_llm_calls": _avg([c["llm_calls"] for c in done_costs]),
            "avg_seconds": _avg(durations),
            "total_tokens": sum(c["tokens"] for c in costs.values()),
        },
        "safety": safety,
    }
