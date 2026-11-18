"""Transactional email with two interchangeable providers (EMAIL_PROVIDER).

* ``smtp`` (default): Gmail SMTP with an App Password. Free, no domain needed,
  delivers to any address (~500/day), and the mail genuinely comes from your Gmail
  account, so it's authenticated and lands in the inbox.
* ``resend``: Resend HTTP API. Needs a verified domain to email anyone other than
  the Resend account owner.

Callers only use ``send_podcast_ready``; the provider is a configuration choice.
"""
import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from html import escape

import httpx

from app.core.config import get_settings


class EmailError(RuntimeError):
    """The email provider is misconfigured or rejected the message."""


def _length(duration_sec: int) -> str:
    """'45 sec' under a minute, otherwise 'about 5 min'."""
    return f"{duration_sec} sec" if duration_sec < 60 else f"about {max(1, round(duration_sec / 60))} min"


def _render(name: str | None, topic: str, title: str, link: str, length: str) -> str:
    """Build the HTML body of the "podcast ready" email (all user text is escaped)."""
    hello = f"Hi {escape(name.split()[0])}," if name else "Hi,"
    return f"""
<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:560px;margin:auto;color:#111">
  <p>{hello}</p>
  <p>Your podcast is ready.</p>
  <h2 style="margin:16px 0 4px">{escape(title)}</h2>
  <p style="color:#555;margin:0 0 20px">{escape(topic)} &middot; {length}</p>
  <a href="{escape(link)}" style="display:inline-block;background:#111;color:#fff;padding:12px 20px;
     border-radius:8px;text-decoration:none">Listen now</a>
  <p style="color:#888;font-size:12px;margin-top:28px">You get this because you scheduled a podcast on DailyCast.</p>
</div>"""


def _render_text(title: str, topic: str, link: str, length: str) -> str:
    """Plain-text alternative (improves deliverability and accessibility)."""
    return f"Your podcast is ready.\n\n{title}\n{topic} - {length}\n\nListen now: {link}\n"


def _sender() -> str:
    """The From header. Over Gmail SMTP the address must be the authenticated account."""
    s = get_settings()
    display, address = parseaddr(s.email_from)
    if s.email_provider == "smtp":
        address = s.smtp_username
    return formataddr((display or "DailyCast", address))


def _send_smtp_blocking(to: str, subject: str, html: str, text: str) -> None:
    s = get_settings()
    if not s.smtp_username or not s.smtp_password:
        raise EmailError("SMTP_USERNAME and SMTP_PASSWORD (Gmail App Password) must be set")
    msg = EmailMessage()
    msg["From"] = _sender()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    context = ssl.create_default_context()
    password = s.smtp_password.replace(" ", "")  # Google shows App Passwords in groups of 4
    try:
        if s.smtp_port == 465:  # implicit TLS
            with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=context, timeout=20) as server:
                server.login(s.smtp_username, password)
                server.send_message(msg)
        else:  # 587: STARTTLS
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as server:
                server.starttls(context=context)
                server.login(s.smtp_username, password)
                server.send_message(msg)
    except smtplib.SMTPException as exc:
        raise EmailError(f"SMTP send failed: {exc}") from exc


async def _send_resend(to: str, subject: str, html: str, text: str) -> None:
    s = get_settings()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {s.resend_api_key}"},
            json={"from": _sender(), "to": [to], "subject": subject, "html": html, "text": text},
            timeout=20,
        )
    if resp.status_code >= 400:
        raise EmailError(f"Resend {resp.status_code}: {resp.text[:300]}")


async def send_email(to: str, subject: str, html: str, text: str) -> None:
    """Send one email through the configured provider."""
    provider = get_settings().email_provider
    if provider == "smtp":
        # smtplib is blocking; run it in a worker thread so the event loop stays free.
        await asyncio.to_thread(_send_smtp_blocking, to, subject, html, text)
    elif provider == "resend":
        await _send_resend(to, subject, html, text)
    else:
        raise EmailError(f"Unknown EMAIL_PROVIDER: {provider}")


async def send_podcast_ready(to: str, name: str | None, topic: str, title: str, run_id: str, duration_sec: int) -> None:
    """Email the user a link to their new podcast (the run page in the web app)."""
    link = f"{get_settings().frontend_url.rstrip('/')}/runs/{run_id}"
    length = _length(duration_sec)
    await send_email(
        to=to,
        subject=f"🎧 Your podcast: {title}",
        html=_render(name, topic, title, link, length),
        text=_render_text(title, topic, link, length),
    )
