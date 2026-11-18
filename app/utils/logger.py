"""Structured JSON logger.

Every log line is one JSON object, e.g.
{"timestamp": "...", "level": "INFO", "event": "ingest.ingest_content.success", "message": "...", "params": {...}}

Event naming convention: <feature>.<function>.<success|error|start>
"""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings


class JsonFormatter(logging.Formatter):
    """Turns a LogRecord into a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event", record.name),
            "message": record.getMessage(),
        }
        params = getattr(record, "params", None)
        if params:
            payload["params"] = params
        if record.exc_info:
            payload["traceback"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _build_logger() -> logging.Logger:
    """Create the shared 'app' logger that writes JSON to stdout."""
    logger = logging.getLogger("app")
    logger.setLevel(settings.LOG_LEVEL)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.propagate = False
    return logger


_logger = _build_logger()


def log_info(event: str, message: str, **params: Any) -> None:
    """Log a success/info event. Usage: log_info("ingest.ingest_content.success", "Saved", item_id=1)"""
    _logger.info(message, extra={"event": event, "params": params})


def log_error(event: str, message: str, exc_info: bool = False, **params: Any) -> None:
    """Log a failure event. Pass exc_info=True inside an except block to include the traceback."""
    _logger.error(message, exc_info=exc_info, extra={"event": event, "params": params})
