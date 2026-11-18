"""The research agent's graph nodes.

Each node is an async function: it reads ``ResearchState`` and returns only the
keys it changes. LangGraph merges that partial update into the state.
"""
import asyncio

import httpx

from app.core.tracing import traced_node
from app.handlers.podcast.agent import prompts
from app.handlers.podcast.voices import LANGUAGES
from app.handlers.podcast.agent.state import NoSourcesError, QueryPlan, ResearchState, script_model
from app.handlers.podcast.agent.text_utils import (
    WRITER_LENGTH_BOOST,
    LengthPlan,
    dedupe_and_rank,
    format_sources,
    length_plan,
    word_count,
)
from app.services import llm, tavily

MAX_QUERIES = 5
MAX_REVISIONS = 1  # each rewrite edits the previous draft; 1 is usually enough and saves LLM quota
MIN_RESULTS_BEFORE_WIDENING = 3
DEFAULT_DURATION_SECONDS = 300


def _plan(state: ResearchState) -> LengthPlan:
    """Length-derived sizes (words, paragraphs, sources, context) for this run."""
    return length_plan(state.get("duration_seconds") or DEFAULT_DURATION_SECONDS)


@traced_node("plan_queries")
async def plan_queries(state: ResearchState) -> ResearchState:
    """Ask the LLM for 3-5 varied search queries; always keep the user's own wording too."""
    plan = await llm.generate_structured(
        prompts.PLANNER_PROMPT.format(topic=state["topic"], today=state["today"]),
        QueryPlan,
        system=prompts.PLANNER_SYSTEM,
        temperature=0.3,
    )
    queries = [q.strip() for q in plan.queries if q.strip()]
    if state["topic"] not in queries:
        queries.insert(0, state["topic"])
    return {"queries": queries[:MAX_QUERIES]}


@traced_node("search")
async def search(state: ResearchState) -> ResearchState:
    """Run every query in parallel against Tavily's news index for the last 24 hours.

    On a quiet news day (fewer than 3 hits) the window is widened to 3 days
    rather than failing the run.
    """
    async with httpx.AsyncClient() as client:

        async def search_all(days: int) -> list[dict]:
            batches = await asyncio.gather(
                *(tavily.search_news(client, q, days=days) for q in state["queries"]),
                return_exceptions=True,  # one failed query shouldn't sink the others
            )
            return [hit for batch in batches if isinstance(batch, list) for hit in batch]

        results = await search_all(days=1)
        if len(results) < MIN_RESULTS_BEFORE_WIDENING:
            results += await search_all(days=3)
    return {"raw_results": results}


async def curate(state: ResearchState) -> ResearchState:
    """Deduplicate and rank the hits, keeping as many sources as the episode length needs.

    Raises:
        NoSourcesError: if nothing usable was found (fails the run without retries).
    """
    sources = dedupe_and_rank(state["raw_results"], limit=_plan(state).max_sources)
    if not sources:
        raise NoSourcesError("No recent sources found for this topic")
    return {"sources": sources}


async def _write_part(state: ResearchState, plan: LengthPlan, sources: list[dict], part: int, parts: int,
                      feedback: str) -> tuple[str, str]:
    """One writer call for ``part`` of ``parts``; returns (title, script)."""
    share = 1 / parts
    # Ask for more than the target to offset the writer's measured undershoot (see WRITER_LENGTH_BOOST);
    # check_length still judges the result against the real target.
    words = round(plan.target_words * share * WRITER_LENGTH_BOOST)
    tolerance = max(round(words * 0.08), 8)
    paragraphs = max(1, round(plan.min_paragraphs * share * WRITER_LENGTH_BOOST))
    if parts == 1:
        segment, structure = "", plan.structure
    elif part == 1:
        segment = prompts.SEGMENT_FIRST.format(part=part, parts=parts)
        structure = "A one-line hook intro, then these stories grouped by theme. No recap, no sign-off."
    elif part == parts:
        segment = prompts.SEGMENT_LAST.format(part=part, parts=parts)
        structure = "Continue directly with these stories, then a short recap of the whole episode and a sign-off."
    else:
        segment = prompts.SEGMENT_MIDDLE.format(part=part, parts=parts)
        structure = "Continue directly with these stories. No greeting, no recap, no sign-off."
    output = await llm.generate_structured(
        prompts.WRITER_PROMPT.format(
            topic=state["topic"],
            today=state["today"],
            language_name=LANGUAGES.get(state.get("language") or "en-IN", "English (India)"),
            duration_label=plan.label if parts == 1 else f"{plan.label} in total; this part",
            target_words=words,
            min_words=words - tolerance,
            max_words=words + tolerance,
            structure=structure,
            paragraphs=paragraphs,  # LLMs follow paragraph counts better than word counts
            sources=format_sources(sources, round(plan.context_chars * share) if parts > 1 else plan.context_chars),
            feedback=segment + feedback,
        ),
        script_model(paragraphs, paragraphs + max(1, paragraphs // 5)),
        system=prompts.WRITER_SYSTEM,
        temperature=0.6,
    )
    return output.title.strip()[:200], output.script.strip()


@traced_node("write_script")
async def write_script(state: ResearchState) -> ResearchState:
    """Write the spoken script from the numbered sources (plus revision feedback, if any).

    Episodes longer than ~5 minutes are written in parts (``LengthPlan.parts``):
    the sources are interleaved across parts so each has strong stories, part 1
    opens the show, the last part recaps and signs off, and the parts are joined.
    One call reliably writes ~5 minutes; asking a single call for 10 minutes
    returns about half that.
    """
    plan = _plan(state)
    parts = plan.parts
    feedback = state.get("feedback", "")
    if parts > 1 and feedback:
        # A multi-part draft is too long to quote back; give each part its share of the correction instead.
        feedback = prompts.PART_FEEDBACK.format(words=state.get("word_count", 0), target=plan.target_words,
                                                share=round(plan.target_words / parts))
    titles, scripts = [], []
    for part in range(1, parts + 1):
        title, script = await _write_part(state, plan, state["sources"][part - 1::parts], part, parts, feedback)
        titles.append(title)
        scripts.append(script)
    script = "\n\n".join(scripts)
    return {"title": titles[0], "script": script, "word_count": word_count(script)}


async def check_length(state: ResearchState) -> ResearchState:
    """Accept the script if it fits the requested length; otherwise request an edit of this draft.

    Tracks the draft closest to the target across attempts, so when revisions run
    out the episode uses the *best* draft, not merely the last one.
    """
    plan = _plan(state)
    target, min_words, max_words = plan.target_words, plan.min_words, plan.max_words
    words = state["word_count"]
    current = {"title": state["title"], "script": state["script"], "word_count": words}
    best = state.get("best")
    if best is None or abs(words - target) < abs(best["word_count"] - target):
        best = current

    if min_words <= words <= max_words:
        return {"feedback": "", "best": current}

    revisions = state.get("revisions", 0) + 1
    if revisions > MAX_REVISIONS:  # out of attempts: finish with the closest draft
        return {"feedback": "", "revisions": revisions, "best": best, **best}

    direction = (
        f"add about {target - words} words by covering more stories and adding figures, investors and context"
        if words < min_words
        else f"cut about {words - target} words by trimming minor items and repetition"
    )
    return {
        "revisions": revisions,
        "best": best,
        "feedback": prompts.REVISION_FEEDBACK.format(
            words=words, min_words=min_words, max_words=max_words, direction=direction, previous=state["script"]
        ),
    }


def route_after_check(state: ResearchState) -> str:
    """Conditional edge: 'revise' while check_length left feedback, else 'done'."""
    return "revise" if state.get("feedback") else "done"
