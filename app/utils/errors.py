"""Custom errors + global handlers so every API error has the same shape:

{"error": {"code": "VALIDATION_ERROR", "message": "...", "details": {...}}}
"""

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.utils.logger import log_error


class AppError(Exception):
    """Base error. Raise subclasses from services/controllers; the handler formats the response."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, details: Any = None):
        super().__init__(message)
        self.message = message
        self.details = details


class BadRequestError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "BAD_REQUEST"


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"


class ExternalServiceError(AppError):
    """Use when OpenAI / URL fetch / any 3rd party call fails."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "EXTERNAL_SERVICE_ERROR"


class NotImplementedYetError(AppError):
    """Placeholder for stubs you still have to implement."""

    status_code = status.HTTP_501_NOT_IMPLEMENTED
    code = "NOT_IMPLEMENTED"


def error_body(code: str, message: str, details: Any = None) -> dict:
    """Build the standard error JSON body."""
    return {"error": {"code": code, "message": message, "details": details}}


def register_error_handlers(app: FastAPI) -> None:
    """Attach handlers for AppError, pydantic validation errors, and unexpected crashes."""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError):
        log_error(f"api.{_feature(request)}.error", exc.message, code=exc.code, details=exc.details)
        return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        details = [{"field": ".".join(map(str, e["loc"])), "issue": e["msg"]} for e in exc.errors()]
        log_error(f"api.{_feature(request)}.validation_error", "Invalid request", details=details)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=error_body("VALIDATION_ERROR", "Invalid request", details),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception):
        log_error(f"api.{_feature(request)}.unhandled_error", str(exc), exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_body("INTERNAL_ERROR", "Something went wrong"),
        )


def _feature(request: Request) -> str:
    """'/items' -> 'items' so log events read like 'api.items.error'."""
    return request.url.path.strip("/").replace("/", ".") or "root"
