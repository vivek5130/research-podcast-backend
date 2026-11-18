"""App entrypoint. Run from backend/:  uvicorn app.main:app --reload"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config.settings import settings
from app.db.database import init_db
from app.routes import ingest_routes, item_routes, query_routes
from app.utils.errors import register_error_handlers
from app.utils.logger import log_info


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once on startup (before first request) and once on shutdown."""
    init_db()
    log_info("app.lifespan.startup", "Server started", env=settings.ENV)
    yield
    log_info("app.lifespan.shutdown", "Server stopped")


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)

app.include_router(ingest_routes.router)
app.include_router(item_routes.router)
app.include_router(query_routes.router)


@app.get("/health", tags=["health"])
def health():
    """Simple liveness check."""
    return {"status": "ok"}
