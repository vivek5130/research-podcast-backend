"""HTTP routes for ``/api/auth``: sign in, sign out, current session.

The session JWT is stored in an **httpOnly, Secure, SameSite** cookie, so page
JavaScript (and therefore XSS) can't read it, and browsers won't attach it to
cross-site POSTs (CSRF). The frontend proxies ``/api`` to this backend so the cookie
is first-party.
"""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from app.controllers import auth_controller
from app.controllers.auth_controller import IssuedSession
from app.core.config import get_settings
from app.core.dependencies import get_current_user_id
from app.schemas.auth import (
    DevSessionResponse,
    DevSignInRequest,
    GoogleSignInRequest,
    SessionResponse,
    UserResponse,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_session_cookie(response: Response, session: IssuedSession) -> None:
    s = get_settings()
    response.set_cookie(
        key=s.session_cookie_name,
        value=session.token,
        max_age=int((session.expires_at - datetime.now(timezone.utc)).total_seconds()),
        httponly=True,
        secure=s.session_cookie_secure,
        samesite=s.session_cookie_samesite,
        domain=s.session_cookie_domain,
        path="/",
    )


@router.post("/signin/google", response_model=SessionResponse)
async def sign_in_with_google(body: GoogleSignInRequest, response: Response):
    """Exchange a Google ID token for a session cookie."""
    session = await auth_controller.sign_in_with_google(body.credential)
    _set_session_cookie(response, session)
    return SessionResponse(user=session.user, expires_at=int(session.expires_at.timestamp()))


@router.post("/signin/dev", response_model=DevSessionResponse, include_in_schema=False)
async def sign_in_dev(body: DevSignInRequest, response: Response):
    """Local testing only (ALLOW_DEV_LOGIN=true): sign in by email; also returns the token for curl."""
    session = await auth_controller.dev_sign_in(body)
    _set_session_cookie(response, session)
    return DevSessionResponse(
        user=session.user, expires_at=int(session.expires_at.timestamp()), access_token=session.token
    )


@router.post("/signout", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out(response: Response):
    """Clear the session cookie. (Stateless JWT: the token simply stops being sent.)"""
    s = get_settings()
    response.delete_cookie(
        key=s.session_cookie_name, path="/", domain=s.session_cookie_domain,
        secure=s.session_cookie_secure, httponly=True, samesite=s.session_cookie_samesite,
    )


@router.get("/session", response_model=UserResponse)
async def get_session(user_id: UUID = Depends(get_current_user_id)):
    """The signed-in user, or 401. The frontend calls this on load."""
    return await auth_controller.get_session_user(user_id)
