"""Gnani.ai (Vachana) text-to-speech.

A multi-minute script (5 min is ~4-5k chars) is too long for one request, so we split it on
sentence boundaries into chunks, synthesize them concurrently (bounded), and join
the MP3s. MP3 is a frame-based format: chunks with identical sample rate/bitrate
can be concatenated byte-for-byte and play as one file, so no ffmpeg is needed.
"""
import asyncio
import re
import time

import httpx

from app.core import tracing
from app.core.config import get_settings

BITRATE_KBPS = 128
SAMPLE_RATE = 44100


class TTSError(RuntimeError):
    """Gnani TTS returned an error or no audio."""


def split_into_chunks(text: str, max_chars: int) -> list[str]:
    """Pack whole sentences into chunks of at most max_chars (hard-splits a monster sentence)."""
    sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text).strip())
    chunks: list[str] = []
    current = ""
    for sentence in filter(None, sentences):
        while len(sentence) > max_chars:  # extremely long sentence: split on a comma/space
            cut = max(sentence.rfind(",", 0, max_chars), sentence.rfind(" ", 0, max_chars))
            cut = cut if cut > 0 else max_chars
            pieces, sentence = sentence[: cut + 1].strip(), sentence[cut + 1 :].strip()
            if current:
                chunks.append(current)
                current = ""
            chunks.append(pieces)
        if current and len(current) + 1 + len(sentence) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


async def _synthesize_chunk(client: httpx.AsyncClient, text: str, sem: asyncio.Semaphore,
                            voice: str, language: str) -> bytes:
    """Synthesize one chunk to MP3, retrying rate limits (429) and 5xx with backoff.

    Gnani allows a limited number of concurrent requests per account (1 on the
    free tier), so ``sem`` caps concurrency and 429s wait progressively longer.
    """
    s = get_settings()
    payload = {
        "text": text,
        "model": s.gnani_model,
        "voice": voice,
        "language": language,
        "speed": 1.0,
        "audio_config": {
            "sample_rate": SAMPLE_RATE,
            "num_channels": 1,
            "sample_width": 2,
            "container": "mp3",
            "bitrate": f"{BITRATE_KBPS}k",
        },
    }
    attempts = 5
    trace_input = {"chars": len(text), "text_preview": text[:200], "model": s.gnani_model, "voice": voice,
                   "language": language, "format": f"mp3/{SAMPLE_RATE}Hz/{BITRATE_KBPS}k"}
    async with sem:
        for attempt in range(attempts):
            started, t0 = tracing.now(), time.perf_counter()
            try:
                resp = await client.post(
                    s.gnani_tts_url,
                    headers={"X-API-Key-ID": s.gnani_api_key, "Content-Type": "application/json"},
                    json=payload,
                    timeout=120,
                )
            except Exception as exc:
                await tracing.record(kind="tool", name="gnani.tts", status="error", error=str(exc)[:1000],
                                     attempt=attempt + 1, started_at=started, input=trace_input, input_chars=len(text),
                                     latency_ms=round((time.perf_counter() - t0) * 1000))
                raise
            ok = resp.status_code == 200 and bool(resp.content)
            await tracing.record(
                kind="tool", name="gnani.tts", status="ok" if ok else "error", attempt=attempt + 1,
                error=None if ok else f"HTTP {resp.status_code}: {resp.text[:300]}",
                started_at=started, latency_ms=round((time.perf_counter() - t0) * 1000),
                input=trace_input, input_chars=len(text),
                output={"bytes": len(resp.content), "duration_sec": round(len(resp.content) * 8 / (BITRATE_KBPS * 1000), 1)}
                if ok else {"status_code": resp.status_code},
            )
            if ok:
                return resp.content
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                await asyncio.sleep(2 * (attempt + 1))  # 2, 4, 6, 8 s
                continue
            raise TTSError(f"Gnani TTS {resp.status_code}: {resp.text[:300]}")
    raise TTSError("Gnani TTS failed after retries")


def _seconds(mp3: bytes) -> float:
    """Exact duration of a constant-bitrate MP3 chunk."""
    return len(mp3) * 8 / (BITRATE_KBPS * 1000)


def sentence_timings(chunks: list[str], chunk_seconds: list[float]) -> list[dict]:
    """Start/end time of every sentence, for highlighting the script while it plays.

    Each chunk's real audio duration is known (CBR MP3), so chunk boundaries are
    exact; inside a chunk, time is shared between sentences by character length.
    """
    timings: list[dict] = []
    offset = 0.0
    for text, seconds in zip(chunks, chunk_seconds):
        sentences = [x for x in re.split(r"(?<=[.!?।])\s+", text) if x.strip()]
        total = sum(len(x) for x in sentences) or 1
        t = offset
        for sentence in sentences:
            span = seconds * len(sentence) / total
            timings.append({"text": sentence, "start": round(t, 2), "end": round(t + span, 2)})
            t += span
        offset += seconds
    return timings


async def synthesize(script: str, voice: str | None = None, language: str | None = None) -> tuple[bytes, int, list[dict]]:
    """Return (mp3_bytes, duration_seconds, sentence_timings). Voice/language default to settings."""
    s = get_settings()
    voice, language = voice or s.gnani_voice, language or s.gnani_language
    chunks = split_into_chunks(script, s.gnani_chunk_chars)
    if not chunks:
        raise TTSError("Empty script")
    sem = asyncio.Semaphore(s.gnani_concurrency)
    async with httpx.AsyncClient() as client:
        parts = await asyncio.gather(*(_synthesize_chunk(client, c, sem, voice, language) for c in chunks))
    audio = b"".join(parts)  # gather preserves order
    duration = round(_seconds(audio))
    return audio, duration, sentence_timings(chunks, [_seconds(x) for x in parts])


async def synthesize_sample(text: str, voice: str, language: str) -> bytes:
    """One short clip (voice previews)."""
    async with httpx.AsyncClient() as client:
        return await _synthesize_chunk(client, text, asyncio.Semaphore(1), voice, language)
