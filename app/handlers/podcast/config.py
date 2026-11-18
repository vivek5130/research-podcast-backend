"""Podcast scheduler configuration and stage names."""
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.handlers.podcast.voices import DEFAULT_LANGUAGE, DEFAULT_VOICE, LANGUAGES, VOICE_NAMES

# Episode lengths offered to users (seconds): 15 s, 30 s, 1, 2, 5, 10 minutes.
DURATION_OPTIONS = (15, 30, 60, 120, 300, 600)
DEFAULT_DURATION_SECONDS = 300


class PodcastStage(StrEnum):
    """Ordered stages of a podcast run. Each runs as its own queued job."""

    RESEARCH = "research"      # search current news + write a script of the configured length
    SYNTHESIZE = "synthesize"  # Gnani TTS -> MP3 -> storage


class PodcastConfig(BaseModel):
    """``schedulers.config`` for ``type = "podcast"``."""

    topic: str = Field(min_length=3, max_length=500, examples=["Startup funding announced in the last 24 hours"])
    duration_seconds: Literal[15, 30, 60, 120, 300, 600] = Field(
        default=DEFAULT_DURATION_SECONDS, description="Target episode length in seconds"
    )
    voice: str = Field(default=DEFAULT_VOICE, description="Gnani Timbre v3.0 voice name (see GET /api/podcast/voices)")
    language: str = Field(default=DEFAULT_LANGUAGE, description="Language code; the script is written and voiced in it")

    @field_validator("topic")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("voice")
    @classmethod
    def _known_voice(cls, value: str) -> str:
        match = next((v for v in VOICE_NAMES if v.lower() == value.strip().lower()), None)
        if not match:
            raise ValueError(f"Unknown voice '{value}'. Choose one of: {', '.join(VOICE_NAMES)}")
        return match  # Gnani voice names are case-sensitive

    @field_validator("language")
    @classmethod
    def _known_language(cls, value: str) -> str:
        if value not in LANGUAGES:
            raise ValueError(f"Unsupported language '{value}'. Choose one of: {', '.join(LANGUAGES)}")
        return value
