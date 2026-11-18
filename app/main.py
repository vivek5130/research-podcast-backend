"""FastAPI application: wires settings, middleware, error handling and routes.

Layers (each only calls the ones below it):

    routes/        HTTP: parse/validate input, auth dependencies, cookies
    controllers/   business logic (generic: schedulers, runs, tick, stages)
    handlers/      type-specific logic, e.g. handlers/podcast (agent, TTS, email)
    workers/       generic run executor (one queued job per stage)
    repositories/  SQL (one module per table)      services/  external APIs
    models/ schemas/   domain types / API bodies
    core/          settings, DB pool, security, errors, dependencies
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core import database
from app.services import storage
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.timing import TimingMiddleware
from app.routes import ALL_ROUTERS

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Close the DB pool on shutdown."""
    yield
    await database.close_pool()
    await storage.close()


def create_app() -> FastAPI:
    """Build and configure the FastAPI app."""
    settings = get_settings()
    app = FastAPI(title="DailyCast API", version="1.0.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in settings.frontend_url.split(",")],
        allow_credentials=True,  # lets the browser send the session cookie
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.add_middleware(TimingMiddleware)
    for router in ALL_ROUTERS:
        app.include_router(router)

    @app.get("/health", tags=["health"])
    async def health():
        """Liveness check (no dependencies)."""
        return {"ok": True, "queue_mode": settings.queue_mode}

    @app.get("/health/db", tags=["health"])
    async def health_db():
        """Readiness check: can we reach Postgres?"""
        row = await database.fetchrow("SELECT now() AS now")
        return {"ok": True, "db_time": row["now"]}

    return app


app = create_app()
