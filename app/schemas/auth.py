"""Request/response bodies for ``/api/auth``."""
from uuid import UUID

from pydantic import BaseModel, EmailStr


class GoogleSignInRequest(BaseModel):
    """The Google ID token returned to the browser by Google Identity Services."""

    credential: str


class DevSignInRequest(BaseModel):
    """Local-testing sign-in (only when ALLOW_DEV_LOGIN=true)."""

    email: EmailStr
    name: str | None = None


class UserResponse(BaseModel):
    """Public profile of the signed-in user."""

    id: UUID
    email: str
    name: str | None = None
    picture: str | None = None


class SessionResponse(BaseModel):
    """The current session. The token itself travels in an httpOnly cookie."""

    user: UserResponse
    expires_at: int  # unix seconds


class DevSessionResponse(SessionResponse):
    """Dev sign-in also returns the raw token so curl/tests can use a Bearer header."""

    access_token: str
    token_type: str = "bearer"
