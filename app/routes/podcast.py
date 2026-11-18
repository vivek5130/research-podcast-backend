"""HTTP routes for podcast-specific options (``/api/podcast``)."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.controllers import podcast_controller
from app.core.dependencies import get_current_user_id

router = APIRouter(prefix="/api/podcast", tags=["podcast"])


@router.get("/voices")
async def list_voices(_: UUID = Depends(get_current_user_id)):
    """Gnani Timbre v3.0 voices (gender, persona, use case) and supported languages."""
    return podcast_controller.list_voices()


@router.get("/voices/{voice}/preview")
async def voice_preview(voice: str, language: str = Query("en-IN"), _: UUID = Depends(get_current_user_id)):
    """A short cached sample of the voice, as a signed audio URL."""
    return await podcast_controller.voice_preview(voice, language)
