"""Token handling: verify Google ID tokens, issue and decode our session JWTs.

Sign-in flow:
1. The frontend obtains a Google **ID token** (Google Identity Services).
2. ``verify_google_id_token`` checks it against Google's public keys (JWKS).
3. We issue our **own** HS256 session JWT (``issue_session_token``) whose ``sub`` is
   our user id. It is set as an httpOnly cookie (and accepted as a Bearer token).
"""
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt

from app.core.config import get_settings
from app.core.exceptions import AuthError

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")

_jwks_client = jwt.PyJWKClient(GOOGLE_JWKS_URL, cache_keys=True)


def verify_google_id_token(token: str) -> dict:
    """Validate a Google ID token and return its claims.

    Checks signature (RS256 via Google's JWKS), audience (our GOOGLE_CLIENT_ID),
    issuer, expiry, and that the email is verified.

    Raises:
        AuthError: if any check fails.
    """
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(token, signing_key.key, algorithms=["RS256"], audience=get_settings().google_client_id)
    except jwt.PyJWTError as exc:
        raise AuthError(f"Invalid Google token: {exc}") from exc
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise AuthError("Invalid token issuer")
    if not claims.get("email_verified"):
        raise AuthError("Google email not verified")
    return claims


def parse_duration(value: str) -> timedelta:
    """Convert '7d' / '12h' / '30m' / '45s' / '3600' (seconds) into a timedelta."""
    match = re.fullmatch(r"(\d+)\s*([smhd]?)", value.strip())
    if not match:
        raise ValueError(f"Bad duration: {value}")
    amount, unit = int(match.group(1)), match.group(2) or "s"
    field = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}[unit]
    return timedelta(**{field: amount})


def issue_session_token(user_id: UUID, email: str) -> tuple[str, datetime]:
    """Issue our session JWT for a user. Returns ``(token, expires_at)``."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + parse_duration(settings.jwt_expires_in)
    payload = {"sub": str(user_id), "email": email, "iat": now, "exp": expires_at}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256"), expires_at


def decode_session_token(token: str) -> UUID:
    """Validate our session JWT and return the user id it was issued for.

    Raises:
        AuthError: if the token is malformed, tampered with, or expired.
    """
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
        return UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise AuthError("Invalid or expired session") from exc
