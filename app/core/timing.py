"""Request timing: a ``Server-Timing`` header on every response, and a log line for slow requests.

Open the browser DevTools Network tab and pick a request: the Timing panel shows
``app;dur=…`` (time spent inside FastAPI). Requests slower than SLOW_REQUEST_MS are
also logged, so regressions (e.g. a slow external call) are visible in the server log.
"""
import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

log = logging.getLogger("app.timing")
SLOW_REQUEST_MS = 300


class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        ms = (time.perf_counter() - start) * 1000
        response.headers["Server-Timing"] = f"app;dur={ms:.1f}"
        if ms > SLOW_REQUEST_MS:
            log.warning("SLOW %s %s -> %s in %.0f ms", request.method, request.url.path, response.status_code, ms)
        return response
