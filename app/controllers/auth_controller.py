"""Sign-in business logic: turn a verified identity into a session.

Setting/clearing the cookie is an HTTP concern and lives in ``routes/auth.py``;
this module only decides *who* the user is and issues the token.
"""
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.exceptions import AuthError, NotFoundError
from app.core.security import issue_session_token, verify_google_id_token
from app.models.user import User
from app.repositories import user_repository
from app.schemas.auth import DevSignInRequest, UserResponse


@dataclass(frozen=True)
class IssuedSession:
    """A freshly issued session: the token (for the cookie), its expiry, and the user."""

    token: str
    expires_at: datetime
    user: UserResponse


def _issue(user: User) -> IssuedSession:
    token, expires_at = issue_session_token(user.id, user.email)
    return IssuedSession(token=token, expires_at=expires_at, user=UserResponse(**user.model_dump()))


async def sign_in_with_google(credential: str) -> IssuedSession:
    """Verify a Google ID token, create/refresh the user, and issue a session."""
    # Fetching Google's signing keys uses blocking urllib, so run it off the event loop.
    claims = await run_in_threadpool(verify_google_id_token, credential)
    user = await user_repository.upsert_by_google_sub(
        google_sub=claims["sub"], email=claims["email"], name=claims.get("name"), picture=claims.get("picture"),
    )
    return _issue(user)


async def dev_sign_in(body: DevSignInRequest) -> IssuedSession:
    """Local testing only: sign in by email without Google. 404 unless ALLOW_DEV_LOGIN=true."""
    if not get_settings().allow_dev_login:
        raise NotFoundError("Not Found")
    email = body.email.lower()
    user = await user_repository.upsert_by_google_sub(google_sub=f"dev:{email}", email=email, name=body.name, picture=None)
    return _issue(user)


async def get_session_user(user_id: UUID) -> UserResponse:
    """Profile of the signed-in user (401 if the account no longer exists)."""
    user = await user_repository.get_by_id(user_id)
    if not user:
        raise AuthError("Account no longer exists")
    return UserResponse(**user.model_dump())
