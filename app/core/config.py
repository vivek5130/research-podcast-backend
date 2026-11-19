"""Application settings.

All configuration comes from environment variables (or ``backend/.env`` locally) and
is validated once by pydantic-settings. Import ``get_settings()`` anywhere; it is cached,
so the environment is read a single time per process.
"""
from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed view of every environment variable the backend uses."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # URLs
    backend_url: str = "http://localhost:3001"
    frontend_url: str = "http://localhost:3000"

    # Database (Supabase Postgres, transaction pooler on :6543)
    database_url: str

    # Auth: Google ID token in, our own HS256 JWT out
    google_client_id: str
    jwt_secret: str
    jwt_expires_in: str = "7d"
    allow_dev_login: bool = False  # enables POST /api/auth/signin/dev for curl testing; NEVER in production

    # Session cookie (httpOnly, so page JavaScript can never read the token)
    session_cookie_name: str = "dailycast_session"
    session_cookie_secure: bool = True       # set false only for http://localhost
    session_cookie_samesite: str = "lax"     # "lax" when the frontend proxies /api (same site); "none" if cross-site
    session_cookie_domain: str | None = None

    # Scheduler / queue
    cron_secret: str = "dev-cron-secret"
    queue_mode: str = "local"  # "qstash" in production, "local" runs jobs in-process
    qstash_url: str = "https://qstash.upstash.io"
    qstash_token: str = Field(default="", validation_alias=AliasChoices("QSTASH_TOKEN", "QSTASH_API"))
    qstash_current_signing_key: str = ""
    qstash_next_signing_key: str = ""
    job_max_retries: int = 3

    # Research + LLM
    tavily_api_key: str = ""
    gemini_api_key: str = ""
    # Free tier: the Flash-Lite models answer in ~2-7 s while full Flash models are often
    # overloaded (503) or hang. With billing enabled, set GEMINI_MODEL=gemini-3.8-flash.
    gemini_model: str = "gemini-3.5-flash-lite"
    # Tried in order when the primary is overloaded, slow or out of quota (comma-separated).
    gemini_fallback_model: str = "gemini-flash-lite-latest,gemini-3.1-flash-lite,gemini-3.8-flash"
    gemini_timeout_seconds: float = 30  # per call; a hung model fails over instead of stalling the run
    # Context budget. The writer's source text scales with episode length (~32 chars per
    # target word, min 4k) up to source_context_chars_max; source_context_chars is the
    # default when no length is given. Any prompt above llm_max_input_chars is logged.
    source_context_chars: int = 24_000  # ~6k tokens (a 5-minute episode)
    source_context_chars_max: int = 40_000  # ~10k tokens (a 10-minute episode)
    llm_max_input_chars: int = 60_000  # logged as over-budget above this (~15k tokens)
    words_per_minute: int = 152  # Gnani's measured speaking rate; turns episode length into a word target
    blocked_terms_extra: str = ""  # extra comma-separated terms for the content policy (category "Custom")
    evaluation_enabled: bool = True  # score each episode (faithfulness, relevance, ...): one extra LLM call

    # Gnani TTS (Vachana API)
    gnani_api_key: str = ""
    gnani_tts_url: str = "https://api.vachana.ai/api/v1/tts/inference"
    gnani_model: str = "timbre-v3.0"
    gnani_voice: str = "Shivani"
    gnani_language: str = "en-IN"
    gnani_chunk_chars: int = 1200
    gnani_concurrency: int = 1  # Gnani allows 1 active request per account on the free tier

    # Storage (Supabase Storage, private bucket + signed URLs)
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    storage_bucket: str = "artifacts"

    # Email: "smtp" (Gmail + App Password, reaches any address) or "resend" (needs a verified domain)
    email_provider: str = "smtp"
    email_from: str = "DailyCast"  # display name (+ address for Resend); over SMTP the address is SMTP_USERNAME
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    smtp_username: str = ""  # the Gmail account that created the App Password
    smtp_password: str = Field(default="", validation_alias=AliasChoices("SMTP_PASSWORD", "GMAIL_APP_PASSWORD"))
    resend_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide Settings instance (created on first call)."""
    return Settings()
