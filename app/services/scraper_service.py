"""Fetches a URL server-side and extracts readable text."""

import re

import httpx
from bs4 import BeautifulSoup

from app.utils.errors import BadRequestError, ExternalServiceError

_MAX_CHARS = 50_000
_USER_AGENT = "KnowledgeInbox/1.0"


async def fetch_page_text(url: str) -> tuple[str | None, str]:
    """Return (page_title, plain_text) for the URL."""
    try:
        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        response = getattr(exc, "response", None)
        details = {"url": url, "reason": str(exc)}
        if response is not None:
            details["status"] = response.status_code
        raise ExternalServiceError("Could not fetch that URL", details=details) from exc

    content_type = response.headers.get("content-type", "")
    if "html" not in content_type and "text" not in content_type and content_type:
        raise BadRequestError(
            "That URL did not return a web page or plain text",
            details={"content_type": content_type},
        )

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    title = None
    if soup.title and soup.title.string:
        title = soup.title.string.strip()[:200] or None

    raw = soup.get_text(separator="\n")
    lines = [line.strip() for line in raw.splitlines()]
    text = "\n".join(line for line in lines if line)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise BadRequestError("No readable text found at that URL")

    return title, text[:_MAX_CHARS]
