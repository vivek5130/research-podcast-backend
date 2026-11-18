"""User domain model (mirrors the ``users`` table)."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class User(BaseModel):
    """A person who signed in with Google.

    ``google_sub`` is Google's stable account id; we key on it instead of email
    because a Google account's email address can change.
    """

    id: UUID
    google_sub: str
    email: str
    name: str | None = None
    picture: str | None = None
    created_at: datetime
    last_login_at: datetime
