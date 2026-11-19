"""Background job queue for run stages.

Production (QUEUE_MODE=qstash): Upstash QStash stores each message and POSTs it to
``/internal/runs/{run_id}/stages/{stage}``, retrying with backoff on non-2xx.
No long-lived worker process is needed, which suits serverless hosting.

Local dev (QUEUE_MODE=local): QStash can't reach localhost, so the stage runs as an
in-process asyncio task instead. Same executor code, different transport.
"""
import asyncio
import base64
import hashlib
import logging
from uuid import UUID

import httpx
import jwt

from app.core.config import get_settings
from app.core.exceptions import UpstreamError

log = logging.getLogger(__name__)
_local_tasks: set[asyncio.Task] = set()  # strong refs so tasks aren't garbage-collected mid-run


def stage_url(run_id: UUID | str, stage: str) -> str:
    """Public URL QStash POSTs to for a stage (also the ``sub`` claim it signs)."""
    return f"{get_settings().backend_url.rstrip('/')}/internal/runs/{run_id}/stages/{stage}"


async def enqueue_stage(run_id: UUID | str, stage: str, generation: int = 0) -> None:
    """Schedule ``stage`` of a run: publish to QStash, or run in-process in local mode.

    ``generation`` is the run's ``retry_count``. It is part of the QStash
    deduplication id, so accidental double-publishes within one attempt are
    dropped, but a manual retry (a new generation) is never mistaken for a duplicate.
    """
    settings = get_settings()
    if settings.queue_mode == "local":
        from app.workers import run_executor  # local import avoids an import cycle

        task = asyncio.create_task(run_executor.execute_stage_locally(str(run_id), stage))
        _local_tasks.add(task)
        task.add_done_callback(_local_tasks.discard)
        return

    if not settings.qstash_token:
        raise QueueError("Couldn't start the job: QSTASH_TOKEN is not configured on the server.")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.qstash_url.rstrip('/')}/v2/publish/{stage_url(run_id, stage)}",
                headers={
                    "Authorization": f"Bearer {settings.qstash_token}",
                    "Content-Type": "application/json",
                    "Upstash-Retries": str(settings.job_max_retries),
                    # Drops accidental double-publishes of the same stage for the same run attempt.
                    "Upstash-Deduplication-Id": f"{run_id}-{stage}-g{generation}",
                },
                json={"run_id": str(run_id), "stage": stage},
                timeout=15,
            )
    except httpx.HTTPError as exc:
        raise QueueError(f"Couldn't reach the job queue (QStash): {type(exc).__name__}.") from exc
    if resp.status_code >= 400:
        hint = _QSTASH_HINTS.get(resp.status_code, "")
        log.error("QStash publish %s for run %s failed: HTTP %s %s", stage, run_id, resp.status_code, resp.text[:300])
        raise QueueError(f"Couldn't start the job: the queue rejected the request (QStash HTTP {resp.status_code}).{hint}")
    log.info("enqueued %s for run %s", stage, run_id)


class QueueError(UpstreamError):
    """Publishing a stage to QStash failed (bad token, QStash down, network)."""

    code = "QUEUE_UNAVAILABLE"


_QSTASH_HINTS = {
    401: " The server's QSTASH_TOKEN is invalid.",
    403: " The QStash token lacks permission or the plan limit was reached.",
    429: " QStash rate limit reached; try again in a minute.",
}


def verify_qstash_signature(signature: str | None, body: bytes, url: str) -> bool:
    """Verify an ``Upstash-Signature`` JWT (HS256).

    Checks issuer, expiry, that ``sub`` equals the URL that was called, and that the
    ``body`` claim equals SHA-256 of the received body. Tries the current signing key,
    then the next one (keys rotate).
    """
    if not signature:
        return False
    settings = get_settings()
    body_hash = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).decode().rstrip("=")
    for key in filter(None, (settings.qstash_current_signing_key, settings.qstash_next_signing_key)):
        try:
            claims = jwt.decode(signature, key, algorithms=["HS256"], issuer="Upstash", leeway=30,
                                options={"verify_aud": False})
        except jwt.PyJWTError:
            continue
        if claims.get("sub") == url and claims.get("body", "").rstrip("=") == body_hash:
            return True
    return False
