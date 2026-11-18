"""FastAPI dependencies that guard each kind of caller.

* ``get_current_user_id``   - end users on ``/api/*`` (session cookie, or Bearer token).
* ``verify_cron_secret``    - the pg_cron scheduler on ``/internal/scheduler/tick``.
* ``verify_qstash_request`` - the QStash queue on ``/internal/runs/*``.
"""
import hmac
from uuid import UUID

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.core.exceptions import AuthError
from app.core.security import decode_session_token
from app.services import queue

_bearer = HTTPBearer(auto_error=False)


def get_current_user_id(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> UUID:
    """Return the signed-in user's id, or raise 401.

    The browser sends the httpOnly session cookie. Scripts and curl may send
    ``Authorization: Bearer <token>`` instead.
    """
    token = request.cookies.get(get_settings().session_cookie_name) or (creds.credentials if creds else None)
    if not token:
        raise AuthError("Not signed in")
    return decode_session_token(token)


def verify_cron_secret(x_cron_secret: str | None = Header(default=None)) -> None:
    """Allow the request only if ``X-Cron-Secret`` matches CRON_SECRET (constant-time compare)."""
    if not x_cron_secret or not hmac.compare_digest(x_cron_secret, get_settings().cron_secret):
        raise AuthError("Bad cron secret")


async def verify_qstash_request(
    request: Request,
    run_id: str,
    stage: str,
    upstash_signature: str | None = Header(default=None),
) -> None:
    """Allow the request only if it carries a valid QStash signature for this exact URL and body."""
    body = await request.body()
    if not queue.verify_qstash_signature(upstash_signature, body, queue.stage_url(run_id, stage)):
        raise AuthError("Invalid QStash signature")
