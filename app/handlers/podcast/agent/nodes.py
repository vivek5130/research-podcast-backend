"""The research agent's graph nodes.

Each node is an async function: it reads ``ResearchState`` and returns only the
keys it changes. LangGraph merges that partial update into the state.
"""
import asyncio

import httpx

from app.core.config import get_settings
from app.core.tracing import traced_node
from app.handlers.podcast.agent import evaluation, guardrails, prompts
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


def _guard(state: ResearchState) -> dict:
    return dict(state.get("guardrails") or {})


@traced_node("guard_input")
async def guard_input(state: ResearchState) -> ResearchState:
    """Input guardrails, before anything is searched or sent to the LLM.

    * Content policy (``BlockedTermsMiddleware``): a blocked topic fails the run with a
      reason naming the category and terms (``ContentPolicyError``, never retried).
    * PII (``PIIMiddleware``): emails, phone numbers, IDs, keys... are redacted from the
      topic; only their *types* are recorded for the episode page.
    """
    guardrails.check_topic(state["topic"])
    redaction = guardrails.redact_input(state["topic"])
    guard = _guard(state)
    if redaction.types:
        guard["topic_pii_removed"] = redaction.types
    return {"topic": redaction.text, "guardrails": guard}


@traced_node("plan_queries")
async def plan_queries(state: ResearchState) -> ResearchState:
    """Ask the LLM for 3-5 varied search queries; always keep the user's own wording too.

    Every query passes the same guardrails as the topic: queries that break the content
    policy are dropped, and PII is redacted before the query reaches the search API.
    """
    plan = await llm.generate_structured(
        prompts.PLANNER_PROMPT.format(topic=state["topic"], today=state["today"]),
        QueryPlan,
        system=prompts.PLANNER_SYSTEM,
        temperature=0.3,
    )
    if not plan.safe:  # input moderation, decided in the same planner call (no extra LLM cost)
        raise guardrails.ContentPolicyError.moderated(plan.unsafe_category or "unsafe content")
    queries = [q.strip() for q in plan.queries if q.strip()]
    if state["topic"] not in queries:
        queries.insert(0, state["topic"])
    policy, guard, safe = guardrails.content_policy(), _guard(state), []
    for query in queries[:MAX_QUERIES]:
        if policy.find(query):
            guard["queries_blocked"] = guard.get("queries_blocked", 0) + 1
            continue
        redaction = guardrails.redact_input(query)
        if redaction.types:
            guard["query_pii_redactions"] = guard.get("query_pii_redactions", 0) + 1
        safe.append(redaction.text)
    return {"queries": safe or [state["topic"]], "guardrails": guard}


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
    # Tool-result guardrail: strip contact details from article text before the writer sees it.
    guard, cleaned = _guard(state), []
    for source in sources:
        text, injected = guardrails.strip_injections(source.get("content") or "")
        if injected:  # sentences trying to instruct the model ("ignore previous instructions...")
            guard["injection_sentences_removed"] = guard.get("injection_sentences_removed", 0) + injected
        redaction = guardrails.redact_source(text)
        if redaction.types:
            guard["source_pii_redactions"] = guard.get("source_pii_redactions", 0) + 1
        cleaned.append({**source, "content": redaction.text})
    return {"sources": cleaned, "guardrails": guard}


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


@traced_node("evaluate")
async def evaluate(state: ResearchState) -> ResearchState:
    """Score the draft (faithfulness, relevance, freshness, ...); see ``agent/evaluation.py``.

    Runs before the output guardrails so the groundedness filter can use the judge's
    list of unsupported claims.
    """
    metrics = await evaluation.evaluate(
        state["topic"], state["script"], state["sources"], state["word_count"], _plan(state).target_words,
        with_judge=get_settings().evaluation_enabled,
    )
    return {"evaluation": metrics}


@traced_node("guard_output")
async def guard_output(state: ResearchState) -> ResearchState:
    """Output guardrails before the script is voiced.

    1. Groundedness: sentences carrying claims the judge found in no source are removed.
    2. Safety: sentences that break the content policy are removed.
    3. PII (``PIIMiddleware`` ``after_model``): no contact details get voiced.
    """
    guard = _guard(state)
    metrics = dict(state.get("evaluation") or {})
    script, report = guardrails.filter_script(state["script"], metrics.get("unsupported_claims") or [])
    guard.update({k: v for k, v in report.items() if v})
    redaction = guardrails.redact_output(script)
    if redaction.types:
        guard["script_pii_removed"] = redaction.types
    words = word_count(redaction.text)
    metrics["word_count"] = words
    if metrics.get("target_words"):
        metrics["length_accuracy"] = round(max(0.0, 1 - abs(words - metrics["target_words"]) / metrics["target_words"]), 3)
    metrics["safety"] = {
        "pii_redactions": len(guard.get("topic_pii_removed", [])) + len(guard.get("script_pii_removed", []))
        + guard.get("query_pii_redactions", 0) + guard.get("source_pii_redactions", 0),
        "queries_blocked": guard.get("queries_blocked", 0),
        "injections_removed": guard.get("injection_sentences_removed", 0),
        "ungrounded_removed": guard.get("ungrounded_sentences_removed", 0),
        "unsafe_removed": guard.get("unsafe_sentences_removed", 0),
    }
    return {"script": redaction.text, "word_count": words, "guardrails": guard, "evaluation": metrics}
