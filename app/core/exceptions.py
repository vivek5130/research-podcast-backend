"""Domain exceptions and their HTTP mapping.

Controllers and services raise these plain Python errors; they never import FastAPI.
``register_exception_handlers`` turns them into HTTP responses in one place, so the
business logic stays framework-free and easy to unit test.

Every error response has the same shape, so the UI and curl users can debug it::

    {
      "detail": "Couldn't start the episode: the job queue rejected the request (QStash 401).",
      "code": "QUEUE_UNAVAILABLE",          # stable, machine-readable
      "request_id": "5mqx9-1795040663712-7363ca00a377"   # search this in the Vercel / server logs
    }

``request_id`` is Vercel's own request id when running on Vercel (the same id the
Vercel runtime logs show as ``request=``), otherwise a random one. It is also sent
as the ``X-Request-ID`` response header and written into the server log line.
"""
import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("app.errors")


class AppError(Exception):
    """Base class for errors that map to an HTTP status.

    Args:
        message: human-readable explanation shown to the user (``detail``).
        code: optional override of the class's machine-readable ``code``.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    code = "BAD_REQUEST"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class AuthError(AppError):
    """Missing/invalid credentials (401)."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "UNAUTHORIZED"


class InvalidRequestError(AppError):
    """Input is well-formed JSON but semantically invalid, e.g. bad scheduler config (422)."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "INVALID_REQUEST"


class NotFoundError(AppError):
    """Resource missing *or owned by someone else* (404, so ids are not leaked)."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"


class ConflictError(AppError):
    """Request conflicts with current state, e.g. a run already in progress (409)."""

    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"


class UpstreamError(AppError):
    """An external service failed or is misconfigured (502).

    Subclasses (in the services) set a specific code: ``QUEUE_UNAVAILABLE`` (QStash),
    ``TTS_FAILED`` (Gnani), ``STORAGE_FAILED`` (Supabase Storage).
    """

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "UPSTREAM_ERROR"


class PermanentJobError(Exception):
    """A background job failed in a way retrying cannot fix (e.g. no sources found)."""


def request_id(request: Request) -> str:
    """The id of this request (set by TimingMiddleware; see module docstring)."""
    return getattr(request.state, "request_id", "") or "-"


def _error(request: Request, status_code: int, code: str, detail, **extra) -> JSONResponse:
    rid = request_id(request)
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail, "code": code, "request_id": rid, **extra},
        headers={"X-Request-ID": rid},
    )


_HTTP_CODES = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}


def register_exception_handlers(app: FastAPI) -> None:
    """Render every error, expected or not, as ``{detail, code, request_id}``."""

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        level = logging.WARNING if exc.status_code >= 500 else logging.INFO
        log.log(level, "%s %s -> %s %s: %s [request_id=%s]", request.method, request.url.path,
                exc.status_code, exc.code, exc.message, request_id(request))
        return _error(request, exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Keep FastAPI's list of field errors in ``detail`` (the UI joins their messages).
        errors = [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return _error(request, status.HTTP_422_UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", errors)

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(request, exc.status_code, _HTTP_CODES.get(exc.status_code, f"HTTP_{exc.status_code}"), exc.detail)

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # A bug or an unhandled failure: full traceback in the log, a safe message to the client.
        log.exception("Unhandled %s on %s %s [request_id=%s]", type(exc).__name__,
                      request.method, request.url.path, request_id(request))
        return _error(
            request, status.HTTP_500_INTERNAL_SERVER_ERROR, "INTERNAL_ERROR",
            f"Something went wrong on our side ({type(exc).__name__}). Quote the request id when reporting it.",
        )
