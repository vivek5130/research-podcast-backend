"""Turns text into embedding vectors using Google Gemini.

Falls back to a local hashed bag-of-words vector when no Gemini key is configured,
so the app still runs without a key (keyword overlap, not semantics).
"""

import hashlib
import math
import re

import httpx

from app.config.settings import settings
from app.utils.errors import ExternalServiceError
from app.utils.logger import log_info

LOCAL_DIM = 256
_TOKEN = re.compile(r"[a-z0-9]+")


def _has_gemini_key() -> bool:
    key = settings.GEMINI_API_KEY.strip()
    return bool(key) and key not in ("", "your-key-here")


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Return one vector per input text, same order."""
    if not texts:
        return []
    if not _has_gemini_key():
        log_info(
            "embedding.embed_texts.local",
            "Using local hash embeddings (no Gemini key)",
            count=len(texts),
        )
        return [_local_embed(text) for text in texts]

    model_name = settings.EMBEDDING_MODEL if settings.EMBEDDING_MODEL.startswith("models/") else f"models/{settings.EMBEDDING_MODEL}"
    vectors: list[list[float]] = []
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            for text in texts:
                response = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/{model_name}:embedContent",
                    params={"key": settings.GEMINI_API_KEY},
                    json={
                        "model": model_name,
                        "content": {"parts": [{"text": text}]},
                    },
                )
                response.raise_for_status()
                payload = response.json()
                vector = payload.get("embedding", {}).get("values", [])
                if not vector:
                    raise ExternalServiceError("Gemini embedding response was empty")
                vectors.append(vector)
    except httpx.HTTPError as exc:
        detail = _http_detail(exc)
        raise ExternalServiceError("Gemini embedding request failed", details=detail) from exc

    log_info(
        "embedding.embed_texts.success",
        "Embeddings created via Gemini",
        count=len(vectors),
        model=settings.EMBEDDING_MODEL,
    )
    return vectors


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Similarity between two vectors: 1 = same direction, 0 = unrelated."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for left, right in zip(a, b):
        dot += left * right
        norm_a += left * left
        norm_b += right * right
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _local_embed(text: str) -> list[float]:
    """Feature hashing: each token adds +1 or -1 into a fixed-size vector."""
    vector = [0.0] * LOCAL_DIM
    tokens = _TOKEN.findall(text.lower())
    if not tokens:
        return vector
    for token in tokens:
        digest = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        index = digest % LOCAL_DIM
        sign = 1.0 if (digest // LOCAL_DIM) % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _http_detail(exc: httpx.HTTPError) -> dict:
    response = getattr(exc, "response", None)
    if response is None:
        return {"reason": str(exc)}
    body = response.text[:500]
    return {"status": response.status_code, "body": body}
