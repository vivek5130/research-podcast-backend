"""Request id + timing on every request.

* ``X-Request-ID`` header: Vercel's request id when deployed (the ``request=`` value in
  the Vercel runtime logs), otherwise a random one. Error responses include it too,
  so a failing request in the browser can be found in the logs in one search.
* ``Server-Timing`` header: open DevTools → Network → a request → Timing to see
  ``app;dur=…`` (time spent inside FastAPI).
* Requests slower than SLOW_REQUEST_MS are logged, so regressions are visible.
"""
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

log = logging.getLogger("app.timing")
SLOW_REQUEST_MS = 300


def _request_id(request: Request) -> str:
    # x-vercel-id looks like "bom1::iad1::5mqx9-1795040663712-7363ca00a377"; the last part is what the logs show.
    vercel_id = request.headers.get("x-vercel-id", "")
    return vercel_id.split("::")[-1] if vercel_id else uuid.uuid4().hex[:16]


class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.request_id = rid = _request_id(request)
        start = time.perf_counter()
        response = await call_next(request)
        ms = (time.perf_counter() - start) * 1000
        response.headers["Server-Timing"] = f"app;dur={ms:.1f}"
        response.headers["X-Request-ID"] = rid
        if ms > SLOW_REQUEST_MS:
            log.warning("SLOW %s %s -> %s in %.0f ms [request_id=%s]",
                        request.method, request.url.path, response.status_code, ms, rid)
        return response
