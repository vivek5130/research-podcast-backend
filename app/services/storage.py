"""Supabase Storage via its REST API (private bucket; playback uses short-lived signed URLs).

Performance:
* One shared, keep-alive HTTP client per process. Opening a new TLS connection per
  call cost ~1 s per signed URL (measured); reusing the connection removes that.
* Signed URLs are cached in memory. They're valid for 6 hours, so we reuse one for
  up to 5 hours and every episode page view stops waiting on Supabase.
"""
import asyncio
import time

import httpx

from app.core.config import get_settings
from app.core.exceptions import UpstreamError

SIGNED_URL_TTL = 60 * 60 * 6  # 6 hours (validity of the URL Supabase issues)
CACHE_TTL = SIGNED_URL_TTL - 60 * 60  # reuse for 5 hours, so a served URL always has 1h+ left
CACHE_MAX = 2_000

_client: httpx.AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None
_signed_cache: dict[str, tuple[str, float]] = {}  # path -> (url, expires_at monotonic)


def _http() -> httpx.AsyncClient:
    """Shared keep-alive client (recreated if the event loop changed, e.g. in tests/CLI)."""
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client.is_closed or _client_loop is not loop:
        _client = httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=120))
        _client_loop = loop
    return _client


async def close() -> None:
    """Close the shared client (application shutdown)."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


class StorageError(UpstreamError):
    """Supabase Storage refused or failed a request (bad key, bucket missing, outage)."""

    code = "STORAGE_FAILED"


def _check(resp: httpx.Response, action: str) -> None:
    """Raise a StorageError with the HTTP status and Supabase's message."""
    if resp.status_code >= 400:
        hint = " The server's SUPABASE_SERVICE_ROLE_KEY is missing or invalid." if resp.status_code in (401, 403) else ""
        raise StorageError(f"Storage {action} failed (Supabase HTTP {resp.status_code}: {resp.text[:150]}).{hint}")


def _base() -> str:
    """Storage REST base URL."""
    return f"{get_settings().supabase_url.rstrip('/')}/storage/v1"


def _headers() -> dict:
    """Server-side auth headers (bypass storage policies).

    Accepts either key format from Supabase -> Project Settings -> API Keys:
    * new secret key ``sb_secret_...``: not a JWT, so it goes only in the ``apikey`` header
    * legacy ``service_role`` key (a JWT): sent as both ``apikey`` and Bearer token
    """
    key = get_settings().supabase_service_role_key
    if not key:
        raise StorageError("Storage isn't configured: SUPABASE_SERVICE_ROLE_KEY is empty on the server.")
    if key.startswith("sb_"):
        return {"apikey": key}
    return {"Authorization": f"Bearer {key}", "apikey": key}


async def upload(path: str, data: bytes, content_type: str = "audio/mpeg") -> str:
    """Upload (or overwrite) an object in the private bucket and return its path."""
    bucket = get_settings().storage_bucket
    resp = await _http().post(
        f"{_base()}/object/{bucket}/{path}",
        headers={**_headers(), "Content-Type": content_type, "x-upsert": "true"},
        content=data,
        timeout=120,
    )
    _check(resp, "upload")
    _signed_cache.pop(path, None)  # content changed: don't serve an old signed URL
    return path


async def signed_url(path: str, expires_in: int = SIGNED_URL_TTL) -> str | None:
    """Temporary download URL for a private object (cached; None if the object is missing)."""
    now = time.monotonic()
    cached = _signed_cache.get(path)
    if cached and cached[1] > now:
        return cached[0]

    bucket = get_settings().storage_bucket
    resp = await _http().post(
        f"{_base()}/object/sign/{bucket}/{path}",
        headers=_headers(),
        json={"expiresIn": expires_in},
        timeout=15,
    )
    if resp.status_code in (400, 404):  # Supabase answers 400/404 "Object not found"
        return None
    _check(resp, "signed URL")
    signed = resp.json().get("signedURL") or resp.json().get("signedUrl")
    if not signed:
        return None
    url = f"{_base()}{signed}"
    if len(_signed_cache) >= CACHE_MAX:  # simple bound: drop the oldest entry
        _signed_cache.pop(next(iter(_signed_cache)))
    _signed_cache[path] = (url, now + min(CACHE_TTL, expires_in - 3600))
    return url
