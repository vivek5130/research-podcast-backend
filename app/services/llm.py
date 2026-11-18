"""Gemini wrapper returning validated structured output, resilient to overload.

Gemini's free tier regularly answers ``503 UNAVAILABLE`` ("model is experiencing
high demand"), ``429`` during spikes, or simply hangs. It also retires models
(``404 NOT_FOUND``: "no longer available to new users"). Each call therefore walks
a chain of models: GEMINI_MODEL, then each GEMINI_FALLBACK_MODEL (comma-separated):

* each attempt is capped at GEMINI_TIMEOUT_SECONDS, so a hung model fails over fast;
* overload/timeout errors are retried with a short backoff, then the next model is tried;
* a daily quota (429 PerDay) or a retired/unknown model (404) skips straight to the next model;
* anything else (bad request, auth) raises immediately.
"""
import asyncio
import logging
import time
from typing import TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from app.core import tracing
from app.core.config import get_settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
_client: genai.Client | None = None

RETRYABLE_CODES = {429, 500, 502, 503, 504}
ATTEMPTS_PER_MODEL = 2
BACKOFF_SECONDS = 3  # wait between attempts on the same model
ROUNDS = 2  # passes over the whole model chain
ROUND_BACKOFF_SECONDS = 10  # wait before the second pass


def _get_client() -> genai.Client:
    """Lazily create one Gemini client per process."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=get_settings().gemini_api_key)
    return _client


def model_chain() -> list[str]:
    """Primary model followed by the fallbacks, without duplicates."""
    s = get_settings()
    chain: list[str] = []
    for name in [s.gemini_model, *s.gemini_fallback_model.split(",")]:
        name = name.strip()
        if name and name not in chain:
            chain.append(name)
    return chain


def _code(exc: Exception) -> int | None:
    return getattr(exc, "code", None) if isinstance(exc, errors.APIError) else None


def _skip_model(exc: Exception) -> bool:
    """Errors that won't fix themselves within this run: move to the next model now."""
    code = _code(exc)
    return code == 404 or (code == 429 and "PerDay" in str(exc))


def _retryable(exc: Exception) -> bool:
    return isinstance(exc, asyncio.TimeoutError) or _code(exc) in RETRYABLE_CODES


async def _call(model: str, prompt: str, schema: type[T], system: str | None, temperature: float):
    """One Gemini request. Returns ``(parsed, raw_response)``."""
    resp = await asyncio.wait_for(
        _get_client().aio.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=temperature,
                response_mime_type="application/json",
                response_schema=schema,
            ),
        ),
        timeout=get_settings().gemini_timeout_seconds,
    )
    parsed = resp.parsed if isinstance(resp.parsed, schema) else schema.model_validate_json(resp.text or "{}")
    return parsed, resp


def _usage(resp) -> dict:
    """Token counts reported by Gemini (``usage_metadata``)."""
    u = getattr(resp, "usage_metadata", None)
    if u is None:
        return {}
    return {
        "input_tokens": u.prompt_token_count,
        "output_tokens": u.candidates_token_count,
        "thinking_tokens": getattr(u, "thoughts_token_count", None),
        "total_tokens": u.total_token_count,
    }


async def _traced_call(model: str, requested: str, attempt_no: int, prompt: str, schema: type[T],
                       system: str | None, temperature: float) -> T:
    """``_call`` plus one ``llm_run_traces`` row (success or failure) with prompt, response, tokens, latency."""
    started, t0 = tracing.now(), time.perf_counter()
    input_chars = len(prompt) + len(system or "")
    common = dict(
        kind="llm", name="gemini.generate_content", requested_model=requested, model=model, attempt=attempt_no,
        started_at=started, input_chars=input_chars,
        input={"system": tracing.clip(system), "prompt": tracing.clip(prompt), "schema": schema.__name__,
               "temperature": temperature},
    )
    if input_chars > get_settings().llm_max_input_chars:
        log.warning("LLM input over budget: %d chars (limit %d)", input_chars, get_settings().llm_max_input_chars)
    try:
        parsed, resp = await _call(model, prompt, schema, system, temperature)
    except Exception as exc:
        reason = "timed out" if isinstance(exc, asyncio.TimeoutError) else str(exc)
        await tracing.record(**common, status="error", error=reason[:1000],
                             latency_ms=round((time.perf_counter() - t0) * 1000))
        raise
    await tracing.record(**common, status="ok", latency_ms=round((time.perf_counter() - t0) * 1000),
                         output={"response": tracing.clip(resp.text)}, **_usage(resp))
    return parsed


async def generate_structured(prompt: str, schema: type[T], system: str | None = None, temperature: float = 0.4) -> T:
    """Call Gemini in JSON mode and return the response validated as ``schema``.

    Passing a Pydantic model as ``response_schema`` constrains the model's output,
    so we get typed data instead of parsing free text. Every attempt is traced.
    """
    last_exc: Exception | None = None
    dead: set[str] = set()  # retired / out-of-quota models: don't try them again this call
    chain = model_chain()
    attempt_no = 0
    for round_no in range(ROUNDS):
        if round_no:
            await asyncio.sleep(ROUND_BACKOFF_SECONDS)  # overload spikes usually clear within seconds
        for model in (m for m in chain if m not in dead):
            for attempt in range(ATTEMPTS_PER_MODEL):
                attempt_no += 1
                try:
                    return await _traced_call(model, chain[0], attempt_no, prompt, schema, system, temperature)
                except Exception as exc:
                    if not (_skip_model(exc) or _retryable(exc)):
                        raise
                    last_exc = exc
                    reason = "timed out" if isinstance(exc, asyncio.TimeoutError) else str(exc)[:120]
                    log.warning("Gemini %s attempt %d (round %d) failed: %s", model, attempt + 1, round_no + 1, reason)
                    if _skip_model(exc):
                        dead.add(model)
                        break
                    if attempt < ATTEMPTS_PER_MODEL - 1:
                        await asyncio.sleep(BACKOFF_SECONDS)
    assert last_exc is not None
    if isinstance(last_exc, asyncio.TimeoutError):
        raise RuntimeError("All Gemini models timed out") from last_exc
    raise last_exc
