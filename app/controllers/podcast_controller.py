"""Podcast-specific endpoints: the Gnani voice catalog and cached voice previews."""
import logging

from app.core.exceptions import InvalidRequestError, UpstreamError
from app.handlers.podcast.voices import LANGUAGES, PREVIEW_TEXT, VOICE_NAMES, catalog
from app.services import storage, tts

log = logging.getLogger(__name__)


def list_voices() -> dict:
    """Voices (with gender / persona / use case) and languages the user can choose."""
    return catalog()


async def voice_preview(voice: str, language: str) -> dict:
    """Signed URL of a short sample of ``voice`` speaking ``language``.

    Each (voice, language) sample is synthesized once and cached in storage, so
    browsing voices costs Gnani credits only the first time a sample is played.
    """
    if voice not in VOICE_NAMES:
        raise InvalidRequestError(f"Unknown voice '{voice}'")
    if language not in LANGUAGES:
        raise InvalidRequestError(f"Unsupported language '{language}'")
    # Languages without a sample line preview the voice in English.
    sample_language = language if language in PREVIEW_TEXT else "en-IN"
    path = f"voice-previews/{voice}-{sample_language}.mp3"
    try:
        url = await storage.signed_url(path)
        if url is None:  # not cached yet
            audio = await tts.synthesize_sample(PREVIEW_TEXT[sample_language].format(voice=voice), voice, sample_language)
            await storage.upload(path, audio, content_type="audio/mpeg")
            url = await storage.signed_url(path)
    except UpstreamError as exc:  # TTS_FAILED / STORAGE_FAILED: keep the specific code and reason
        raise type(exc)(f"No preview for {voice} ({sample_language}): {exc.message}") from exc
    except Exception as exc:  # anything else: still a clear 502 instead of a crash
        log.exception("voice preview %s/%s failed", voice, sample_language)
        raise UpstreamError(f"No preview for {voice} ({sample_language}): {type(exc).__name__}.") from exc
    return {"voice": voice, "language": sample_language, "audio_url": url}
