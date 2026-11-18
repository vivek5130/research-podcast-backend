"""Domain exceptions and their HTTP mapping.

Controllers and services raise these plain Python errors; they never import FastAPI.
``register_exception_handlers`` turns them into HTTP responses in one place, so the
business logic stays framework-free and easy to unit test.
"""
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base class for errors that map to an HTTP status."""

    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class AuthError(AppError):
    """Missing/invalid credentials (401)."""

    status_code = status.HTTP_401_UNAUTHORIZED


class InvalidRequestError(AppError):
    """Input is well-formed JSON but semantically invalid, e.g. bad scheduler config (422)."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY


class NotFoundError(AppError):
    """Resource missing *or owned by someone else* (404, so ids are not leaked)."""

    status_code = status.HTTP_404_NOT_FOUND


class ConflictError(AppError):
    """Request conflicts with current state, e.g. a run already in progress (409)."""

    status_code = status.HTTP_409_CONFLICT


class PermanentJobError(Exception):
    """A background job failed in a way retrying cannot fix (e.g. no sources found)."""


def register_exception_handlers(app: FastAPI) -> None:
    """Attach a handler that renders any AppError as ``{"detail": message}``."""

    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})
