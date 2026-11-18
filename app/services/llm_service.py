"""Calls the Gemini LLM to produce an answer grounded in retrieved context."""

import httpx

from app.config.settings import settings
from app.utils.errors import ExternalServiceError
from app.utils.logger import log_info

_SYSTEM = (
    "You answer questions using only the numbered context snippets provided. "
    "If the context does not contain the answer, say you don't know based on the saved notes. "
    "Cite snippets inline as [1], [2], etc. Keep the answer short and concrete."
)


def _has_gemini_key() -> bool:
    key = settings.GEMINI_API_KEY.strip()
    return bool(key) and key not in ("", "your-key-here")


async def generate_answer(question: str, context: str) -> str:
    """Return an answer grounded in `context`. Falls back to the snippets when no API key is set."""
    if not context.strip():
        return "Nothing in your inbox matches that question yet."

    if not _has_gemini_key():
        log_info("llm.generate_answer.local", "Returning extractive answer (no Gemini key)")
        return (
            "Gemini is not configured, so this answer is taken directly from the closest saved passages:\n\n"
            + context.strip()
        )

    prompt = (
        f"{_SYSTEM}\n\nContext:\n{context}\n\nQuestion: {question}\n\nAnswer:"
    )

    model_name = settings.LLM_MODEL.removeprefix("models/")
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent",
                params={"key": settings.GEMINI_API_KEY},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024},
                },
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        response = getattr(exc, "response", None)
        details = {"reason": str(exc)}
        if response is not None:
            details = {"status": response.status_code, "body": response.text[:500]}
        raise ExternalServiceError("Gemini LLM request failed", details=details) from exc

    try:
        answer = payload["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, AttributeError) as exc:
        raise ExternalServiceError("Gemini LLM response was incomplete") from exc

    log_info("llm.generate_answer.success", "Answer generated via Gemini", model=settings.LLM_MODEL)
    return answer
