"""Pure-logic tests (no network, no DB)."""
import base64
import hashlib
import os
import time as _time
from datetime import datetime, time, timezone

import jwt
import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client")
os.environ.setdefault("JWT_SECRET", "test-secret")

from app.handlers.podcast.agent.nodes import route_after_check  # noqa: E402
from app.handlers.podcast.agent.text_utils import dedupe_and_rank, word_count  # noqa: E402
from app.core.security import decode_session_token, issue_session_token, parse_duration  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.services.queue import verify_qstash_signature  # noqa: E402
from app.services.tts import split_into_chunks  # noqa: E402


def test_chunks_respect_limit_and_keep_text():
    text = " ".join(f"This is sentence number {i}." for i in range(300))
    chunks = split_into_chunks(text, 500)
    assert all(len(c) <= 500 for c in chunks)
    assert " ".join(chunks) == text


def test_chunks_split_giant_sentence():
    chunks = split_into_chunks("word " * 400, 300)
    assert all(len(c) <= 300 for c in chunks)


def test_dedupe_urls_and_similar_titles():
    results = [
        {"title": "Acme raises $10M Series A", "url": "https://www.techcrunch.com/acme/", "score": 0.9},
        {"title": "Acme raises $10M Series A", "url": "https://techcrunch.com/acme", "score": 0.5},
        {"title": "Acme raises $10M in Series A", "url": "https://other.com/x", "score": 0.8},
        {"title": "Beta lands seed round", "url": "https://other.com/beta", "score": 0.7},
    ]
    out = dedupe_and_rank(results)
    assert [r["url"] for r in out] == ["https://www.techcrunch.com/acme/", "https://other.com/beta"]


def test_revision_routing():
    assert route_after_check({"feedback": ""}) == "done"
    assert route_after_check({"feedback": "edit this"}) == "revise"


def test_length_loop_keeps_best_draft():
    """Drafts of 500, 690, 600 words with 2 rewrites allowed (5-min target = 760): the 690-word draft wins."""
    import asyncio

    from app.handlers.podcast.agent import graph, nodes

    counts = iter([500, 690, 600])

    async def fake_plan(s):
        return {"queries": ["q"]}

    async def fake_search(s):
        return {"raw_results": [{"title": "t", "url": "https://a.com/x", "content": "c", "score": 1}]}

    async def fake_write(s):
        n = next(counts)
        return {"title": f"T{n}", "script": "w " * n, "word_count": n}

    originals = (nodes.plan_queries, nodes.search, nodes.write_script, nodes.MAX_REVISIONS)
    nodes.plan_queries, nodes.search, nodes.write_script, nodes.MAX_REVISIONS = fake_plan, fake_search, fake_write, 2
    try:
        state = asyncio.run(graph.build_graph().ainvoke({"topic": "t", "today": "d", "revisions": 0}))
    finally:
        nodes.plan_queries, nodes.search, nodes.write_script, nodes.MAX_REVISIONS = originals
    assert state["word_count"] == 690 and state["title"] == "T690"
    assert "PREVIOUS DRAFT" not in state["feedback"]


def test_word_count():
    assert word_count("Hello there, it's twelve-million dollars.") == 5


def test_jwt_roundtrip():
    import uuid

    uid = uuid.uuid4()
    token, _ = issue_session_token(uid, "a@b.com")
    assert jwt.decode(token, "test-secret", algorithms=["HS256"])["sub"] == str(uid)
    assert decode_session_token(token) == uid
    assert parse_duration("7d").days == 7
    with pytest.raises(ValueError):
        parse_duration("soon")


def test_qstash_signature():
    s = get_settings()
    s.qstash_current_signing_key = "sig_current"
    body = b'{"run_id":"x","stage":"research"}'
    url = "https://api.example.com/internal/runs/x/stages/research"
    digest = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).decode().rstrip("=")
    now = int(_time.time())
    good = jwt.encode({"iss": "Upstash", "sub": url, "body": digest, "iat": now, "nbf": now, "exp": now + 300},
                      "sig_current", algorithm="HS256")
    assert verify_qstash_signature(good, body, url)
    assert not verify_qstash_signature(good, b"tampered", url)
    assert not verify_qstash_signature(good, body, url + "/other")
    assert not verify_qstash_signature(None, body, url)


# ---------- handler registry / podcast handler ----------

from app.core.exceptions import InvalidRequestError  # noqa: E402
from app.handlers.registry import get_handler  # noqa: E402
from app.models.scheduler import SchedulerType  # noqa: E402


def test_podcast_handler_contract():
    h = get_handler(SchedulerType.PODCAST)
    assert h.stages == ("research", "synthesize")
    assert h.first_stage == "research"
    assert h.next_stage("research") == "synthesize"
    assert h.next_stage("synthesize") is None


def test_podcast_config_validation():
    h = get_handler("podcast")
    assert h.validate_config({"topic": "  AI news today  "}) == {
        "topic": "AI news today", "duration_seconds": 300, "voice": "Shivani", "language": "en-IN"}
    with pytest.raises(InvalidRequestError):
        h.validate_config({"topic": "x"})
    with pytest.raises(InvalidRequestError):
        h.validate_config({})
    assert h.default_name({"topic": "a" * 100}).endswith("...")


def test_unknown_type_rejected():
    with pytest.raises(InvalidRequestError):
        get_handler("newsletter")


def test_podcast_present_output_hides_path():
    import asyncio

    from app.handlers.podcast import handler as podcast_module

    async def fake_signed_url(path):
        return f"https://signed/{path}"

    original = podcast_module.storage.signed_url
    podcast_module.storage.signed_url = fake_signed_url
    try:
        out = asyncio.run(get_handler("podcast").present_output({"title": "T", "audio_path": "podcasts/u/r.mp3"}))
    finally:
        podcast_module.storage.signed_url = original
    assert out == {"title": "T", "audio_url": "https://signed/podcasts/u/r.mp3"}


def test_low_quality_sources_filtered():
    from app.handlers.podcast.agent.text_utils import is_citable

    assert not is_citable("https://www.instagram.com/p/abc")
    assert not is_citable("https://m.facebook.com/post/1")
    assert not is_citable("https://www.alleywatch.com")
    assert is_citable("https://techcrunch.com/2026/11/18/acme-raises")
    assert is_citable("https://www.sesamers.com?p=27608")


# ---------- context budget ----------

def test_source_context_budget_caps_total():
    from app.handlers.podcast.agent.text_utils import format_sources, source_char_budget

    sources = [{"title": f"T{i}", "url": f"https://a.com/{i}", "content": "Word. " * 2000, "published_date": None} for i in range(12)]
    assert source_char_budget(12) * 12 <= get_settings().source_context_chars
    text = format_sources(sources)
    assert len(text) < get_settings().source_context_chars + 12 * 60  # budget + per-source headers
    assert source_char_budget(2) == 2000  # few sources: per-source cap applies


def test_trace_collector_records_without_db():
    import asyncio
    from datetime import datetime, timezone

    from app.core import tracing

    async def go():
        with tracing.collect() as bucket, tracing.node_scope("plan_queries"):
            await tracing.record(kind="llm", name="gemini.generate_content", status="ok",
                                 started_at=datetime.now(timezone.utc), latency_ms=5, input_tokens=10)
        return bucket

    bucket = asyncio.run(go())
    assert bucket[0]["node"] == "plan_queries" and bucket[0]["input_tokens"] == 10 and bucket[0]["run_id"] is None


def test_undated_evergreen_pages_dropped_when_enough_news():
    news = [{"title": f"Startup {i} raises ${i}M", "url": f"https://news.com/{i}", "score": 0.5, "published_date": "2026-11-18"} for i in range(6)]
    guide = {"title": "Fundraising for startups: five tips", "url": "https://bank.com/tips", "score": 0.99, "published_date": None}
    assert guide not in dedupe_and_rank(news + [guide])
    assert guide in dedupe_and_rank(news[:2] + [guide])  # too few dated articles: keep it


# ---------- configurable episode length ----------

@pytest.mark.parametrize("seconds,words,paras,sources", [
    (15, 38, 1, 2), (30, 76, 1, 2), (60, 152, 2, 2), (120, 304, 4, 5), (300, 760, 10, 12), (600, 1520, 20, 16),
])
def test_length_plan_scales_with_duration(seconds, words, paras, sources):
    from app.handlers.podcast.agent.text_utils import length_plan

    p = length_plan(seconds)
    assert p.target_words == words and p.min_paragraphs == paras and p.max_sources == sources
    assert p.min_words < words < p.max_words and p.max_words - p.min_words >= 16  # never tighter than ±8 words
    assert 4_000 <= p.context_chars <= get_settings().source_context_chars_max
    assert ("No recap" in p.structure) == (seconds <= 120)


def test_script_schema_enforces_paragraph_count():
    from pydantic import ValidationError

    from app.handlers.podcast.agent.state import script_model

    m = script_model(2, 3)
    assert m.model_json_schema()["properties"]["paragraphs"]["minItems"] == 2
    assert m(title="t", paragraphs=["a", "b"]).script == "a\n\nb"
    with pytest.raises(ValidationError):
        m(title="t", paragraphs=["only one"])
    assert script_model(2, 3) is m  # cached per length


def test_podcast_duration_config():
    h = get_handler("podcast")
    assert h.validate_config({"topic": "AI news"})["duration_seconds"] == 300  # default
    assert h.validate_config({"topic": "AI news", "duration_seconds": 30})["duration_seconds"] == 30
    with pytest.raises(InvalidRequestError):
        h.validate_config({"topic": "AI news", "duration_seconds": 45})


def test_email_length_label():
    from app.services.email import _length

    assert _length(30) == "30 sec" and _length(301) == "about 5 min"


def test_long_episodes_are_written_in_parts():
    from app.handlers.podcast.agent.text_utils import length_plan

    assert [length_plan(s).parts for s in (15, 30, 60, 120, 300, 600)] == [1, 1, 1, 1, 1, 2]


def test_voice_and_language_config():
    h = get_handler("podcast")
    cfg = h.validate_config({"topic": "AI news", "voice": "rakesh", "language": "hi-IN"})
    assert cfg["voice"] == "Rakesh" and cfg["language"] == "hi-IN"  # case-normalised for Gnani
    for bad in ({"topic": "AI news", "voice": "Nalini"}, {"topic": "AI news", "language": "fr-FR"}):
        with pytest.raises(InvalidRequestError):
            h.validate_config(bad)


def test_sentence_timings_follow_chunk_durations():
    from app.services.tts import sentence_timings

    t = sentence_timings(["One. Two two.", "Three."], [3.0, 2.0])
    assert [x["text"] for x in t] == ["One.", "Two two.", "Three."]
    assert t[0]["start"] == 0 and t[1]["end"] == 3.0 and t[2]["start"] == 3.0 and t[2]["end"] == 5.0


def test_qstash_dedup_id_changes_per_retry_generation(monkeypatch):
    """A manual retry republishes the same stage; it must not be deduplicated against the failed attempt."""
    import asyncio

    import httpx

    from app.services import queue

    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers["Upstash-Deduplication-Id"])
        return httpx.Response(200, json={"messageId": "m"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(queue.httpx, "AsyncClient", lambda *a, **k: real_client(transport=httpx.MockTransport(handler)))
    s = get_settings()
    monkeypatch.setattr(s, "queue_mode", "qstash")
    monkeypatch.setattr(s, "qstash_token", "t")

    asyncio.run(queue.enqueue_stage("run-1", "research"))
    asyncio.run(queue.enqueue_stage("run-1", "research", generation=1))
    assert sent == ["run-1-research-g0", "run-1-research-g1"]


# ---- password auth ----
def test_password_hash_roundtrip_and_salting():
    from app.core.security import hash_password, verify_password

    h1, h2 = hash_password("correct horse"), hash_password("correct horse")
    assert h1 != h2 and h1.startswith("scrypt$")  # random salt per hash
    assert verify_password("correct horse", h1)
    assert not verify_password("wrong", h1)
    assert not verify_password("correct horse", None)  # unknown user / Google-only account
    assert not verify_password("x", "garbage")


def test_password_signup_schema_limits():
    import pytest
    from pydantic import ValidationError

    from app.schemas.auth import PasswordSignUpRequest

    PasswordSignUpRequest(email="a@b.co", password="12345678")
    with pytest.raises(ValidationError):
        PasswordSignUpRequest(email="a@b.co", password="short")
    with pytest.raises(ValidationError):
        PasswordSignUpRequest(email="not-an-email", password="12345678")
