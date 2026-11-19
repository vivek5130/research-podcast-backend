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
from app.core.security import hash_password, issue_session_token, verify_google_id_token, verify_password
from app.models.user import User
from app.repositories import user_repository
from app.schemas.auth import DevSignInRequest, PasswordSignInRequest, PasswordSignUpRequest, UserResponse


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


async def sign_up_with_password(body: PasswordSignUpRequest) -> IssuedSession:
    """Create an email + password account and sign it in.

    Raises:
        ConflictError (EMAIL_TAKEN): the email is already registered (password or Google).
    """
    # scrypt is deliberately slow (~50 ms): keep it off the event loop.
    password_hash = await run_in_threadpool(hash_password, body.password)
    name = (body.name or "").strip() or None
    user = await user_repository.create_with_password(body.email.lower(), name, password_hash)
    return _issue(user)


async def sign_in_with_password(body: PasswordSignInRequest) -> IssuedSession:
    """Check email + password and issue a session.

    The same error is returned for an unknown email, a wrong password, and a
    Google-only account, so the endpoint can't be used to discover who has an account.
    """
    user = await user_repository.get_by_email(body.email)
    ok = await run_in_threadpool(verify_password, body.password, user.password_hash if user else None)
    if not user or not ok:
        raise AuthError("Incorrect email or password. If you signed up with Google, use the Google button.",
                        code="INVALID_CREDENTIALS")
    await user_repository.touch_login(user.id)
    return _issue(user)


async def dev_sign_in(body: DevSignInRequest) -> IssuedSession:
    """Local testing only: sign in by email without Google. 404 unless ALLOW_DEV_LOGIN=true."""
    if not get_settings().allow_dev_login:
        raise NotFoundError("Not Found")
    email = body.email.lower()
    user = await user_repository.get_by_email(email) or await user_repository.upsert_by_google_sub(
        google_sub=f"dev:{email}", email=email, name=body.name, picture=None
    )
    return _issue(user)


async def get_session_user(user_id: UUID) -> UserResponse:
    """Profile of the signed-in user (401 if the account no longer exists)."""
    user = await user_repository.get_by_id(user_id)
    if not user:
        raise AuthError("Account no longer exists")
    return UserResponse(**user.model_dump())
