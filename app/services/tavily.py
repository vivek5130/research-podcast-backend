"""Tavily web search: returns current news articles with cleaned content."""
import re
import time

import httpx

from app.core import tracing
from app.core.config import get_settings

TAVILY_URL = "https://api.tavily.com/search"


def _clean(text: str) -> str:
    """Collapse whitespace and strip markdown link/image noise from raw article text."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)          # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)       # links -> their text
    return re.sub(r"\s+", " ", text).strip()


async def search_news(client: httpx.AsyncClient, query: str, days: int = 1, max_results: int = 8) -> list[dict]:
    """Search Tavily's news index for articles published in the last ``days`` days.

    Returns dicts with title, url, content, score, published_date, query. ``content``
    is the article's full cleaned text when Tavily has it (``include_raw_content``),
    falling back to the short relevance snippet. The extra detail lets the writer
    reach five minutes from real facts instead of padding.
    """
    args = {"query": query, "days": days, "max_results": max_results, "topic": "news", "search_depth": "advanced"}
    started, t0 = tracing.now(), time.perf_counter()
    try:
        resp = await client.post(
            TAVILY_URL,
            headers={"Authorization": f"Bearer {get_settings().tavily_api_key}"},
            json={**args, "include_answer": False, "include_raw_content": True},
            timeout=45,
        )
        resp.raise_for_status()
    except Exception as exc:
        await tracing.record(kind="tool", name="tavily.search", status="error", error=str(exc)[:1000],
                             started_at=started, latency_ms=round((time.perf_counter() - t0) * 1000), input=args)
        raise
    results = []
    for r in resp.json().get("results", []):
        if not r.get("url"):
            continue
        snippet = r.get("content") or ""
        raw = _clean(r.get("raw_content") or "")
        results.append(
            {
                "title": r.get("title") or "",
                "url": r["url"],
                # Lead with the relevance snippet, then the article body for detail.
                "content": f"{snippet} {raw}".strip() if raw else snippet,
                "score": float(r.get("score") or 0),
                "published_date": r.get("published_date"),
                "query": query,
            }
        )
    await tracing.record(
        kind="tool", name="tavily.search", status="ok", started_at=started,
        latency_ms=round((time.perf_counter() - t0) * 1000), input=args,
        output={
            "results": len(results),
            "content_chars": sum(len(r["content"]) for r in results),
            "items": [{"title": r["title"], "url": r["url"], "score": round(r["score"], 3)} for r in results],
        },
    )
    return results
