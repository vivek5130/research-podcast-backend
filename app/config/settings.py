"""App-wide settings loaded once from environment variables (.env)."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# backend/ folder (two levels up from this file)
BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings:
    """Single place to read config. Import `settings`, never call os.getenv elsewhere."""

    APP_NAME: str = os.getenv("APP_NAME", "AI Knowledge Inbox")
    ENV: str = os.getenv("ENV", "development")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # SQLite file path; the file is created automatically on first run
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "inbox.db"))

    # AI provider — Gemini
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "models/gemini-embedding-001")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gemini-flash-lite-latest")

    # Comma separated list, e.g. "http://localhost:5173,http://localhost:3000"
    CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")


settings = Settings()
