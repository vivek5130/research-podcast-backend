"""User domain model (mirrors the ``users`` table)."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class User(BaseModel):
    """A person who signs in with Google, with email + password, or both.

    ``google_sub`` is Google's stable account id (it survives email changes);
    ``password_hash`` is a scrypt hash. Either may be absent, never both.
    """

    id: UUID
    google_sub: str | None = None
    email: str
    name: str | None = None
    picture: str | None = None
    password_hash: str | None = None
    created_at: datetime
    last_login_at: datetime
