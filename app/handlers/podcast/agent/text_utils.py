"""Pure text helpers used by the agent (no I/O, unit-tested)."""
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import urlsplit

from app.core.config import get_settings

MAX_SOURCES = 16  # upper bound; the actual count scales with episode length (LengthPlan.max_sources)
MAX_CHARS_PER_SOURCE = 2000  # snippet + start of the article body
SIMILAR_TITLE_RATIO = 0.85


def word_count(text: str) -> int:
    """Count spoken words (handles contractions and hyphenated words)."""
    return len(re.findall(r"\b[\w'’-]+\b", text))


@dataclass(frozen=True)
class LengthPlan:
    """Everything the agent sizes from the requested episode length.

    Gnani reads ~152 words per minute (WORDS_PER_MINUTE), so 5 min ≈ 760 words.
    """

    duration_seconds: int
    target_words: int
    min_words: int
    max_words: int
    min_paragraphs: int  # enforced via the Script schema (minItems)
    max_paragraphs: int
    max_sources: int  # fewer, sharper sources for short episodes
    context_chars: int  # total source text sent to the writer
    label: str  # "5 minutes", "30 seconds"
    structure: str  # how the script should be shaped at this length

    @property
    def parts(self) -> int:
        """Writer calls needed: one call reliably handles ~WORDS_PER_PART words (about 5 minutes)."""
        return max(1, -(-self.target_words // WORDS_PER_PART))  # ceil division


WORDS_PER_PART = 800  # one writer call per ~5 minutes; longer episodes are written in parts and joined
# Measured: Lite models write ~25% fewer words than asked. The prompt asks for this much
# more; the length *check* still uses the real target, so overshoot is caught too.
WRITER_LENGTH_BOOST = 1.3


def duration_label(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} seconds"
    minutes = seconds // 60
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def length_plan(duration_seconds: int) -> LengthPlan:
    """Size words, paragraphs, sources and context budget for an episode of ``duration_seconds``."""
    s = get_settings()
    target = max(25, round(duration_seconds * s.words_per_minute / 60))
    tolerance = max(round(target * 0.08), 8)  # ±8%, but never tighter than ±8 words
    paragraphs = max(1, round(target / 76))  # ~76 words per spoken paragraph
    if duration_seconds <= 60:
        structure = (
            "One tight spoken paragraph: a one-line greeting, the most important one to three stories with "
            "their key numbers, and a two-word sign-off. No recap."
        )
    elif duration_seconds <= 120:
        structure = "A one-line intro, the top stories with their key facts, and a one-line sign-off. No recap."
    else:
        structure = "A one-line hook intro, the stories grouped by theme, a short recap, and a sign-off."
    return LengthPlan(
        duration_seconds=duration_seconds,
        target_words=target,
        min_words=target - tolerance,
        max_words=target + tolerance,
        min_paragraphs=paragraphs,
        max_paragraphs=paragraphs + max(1, paragraphs // 5),
        max_sources=min(MAX_SOURCES, max(2, round(target / 65))),
        context_chars=min(s.source_context_chars_max, max(4_000, target * 32)),
        label=duration_label(duration_seconds),
        structure=structure,
    )


def normalize_url(url: str) -> str:
    """Canonical form for duplicate detection: lowercase host without www, no trailing slash."""
    parts = urlsplit(url.lower())
    return f"{parts.netloc.removeprefix('www.')}{parts.path.rstrip('/')}"


# Social/video platforms rarely carry a citable news report; their snippets are captions.
LOW_QUALITY_DOMAINS = {
    "instagram.com", "facebook.com", "x.com", "twitter.com", "tiktok.com",
    "youtube.com", "pinterest.com", "reddit.com", "threads.net",
}


def is_citable(url: str) -> bool:
    """False for social-media posts and bare homepages (no article path)."""
    parts = urlsplit(url.lower())
    host = parts.netloc.removeprefix("www.")
    if any(host == d or host.endswith("." + d) for d in LOW_QUALITY_DOMAINS):
        return False
    return parts.path.strip("/") != "" or bool(parts.query)


MIN_DATED_SOURCES = 6


def dedupe_and_rank(results: list[dict], limit: int = MAX_SOURCES) -> list[dict]:
    """Keep the best-scored unique, citable, *current* stories.

    Drops social-media posts and homepages, exact duplicate URLs, and
    near-identical headlines (the same story syndicated across outlets).
    When there are enough dated articles, undated pages (evergreen guides such
    as "five fundraising tips") are dropped too, keeping the context to news.
    Then caps the list at ``limit``.
    """
    dated = [r for r in results if r.get("published_date")]
    pool = dated if len({normalize_url(r["url"]) for r in dated}) >= MIN_DATED_SOURCES else results
    kept: list[dict] = []
    seen_urls: set[str] = set()
    for result in sorted(pool, key=lambda r: r["score"], reverse=True):
        if not is_citable(result["url"]):
            continue
        url_key = normalize_url(result["url"])
        if url_key in seen_urls:
            continue
        title = result["title"].lower()
        if any(SequenceMatcher(None, title, k["title"].lower()).ratio() > SIMILAR_TITLE_RATIO for k in kept):
            continue
        seen_urls.add(url_key)
        kept.append(result)
        if len(kept) >= limit:
            break
    return kept


def source_char_budget(n_sources: int, total_chars: int | None = None) -> int:
    """Characters of article text each source may contribute to the writer prompt.

    The total (``total_chars``, sized by LengthPlan; defaults to SOURCE_CONTEXT_CHARS)
    is shared evenly, so more sources means shorter excerpts and the prompt size
    stays roughly constant for a given episode length.
    """
    if n_sources <= 0:
        return 0
    total = total_chars if total_chars is not None else get_settings().source_context_chars
    return min(MAX_CHARS_PER_SOURCE, total // n_sources)


def excerpt(text: str, limit: int) -> str:
    """Cut at a sentence boundary near ``limit`` so excerpts don't end mid-word."""
    if len(text) <= limit:
        return text
    cut = text.rfind(". ", 0, limit)
    return text[: cut + 1] if cut > limit * 0.6 else text[:limit].rsplit(" ", 1)[0] + "…"


def format_sources(sources: list[dict], total_chars: int | None = None) -> str:
    """Render sources as a numbered block for the writer prompt, within the context budget."""
    per_source = source_char_budget(len(sources), total_chars)
    blocks = []
    for i, source in enumerate(sources, 1):
        outlet = urlsplit(source["url"]).netloc.removeprefix("www.")
        date = f" ({source['published_date']})" if source.get("published_date") else ""
        blocks.append(f"[{i}] {source['title']} - {outlet}{date}\n{excerpt(source['content'], per_source)}")
    return "\n\n".join(blocks)
