"""Data access for the ``users`` table.

One account per email address (case-insensitive unique index, migration 006). An
account can have a Google identity (``google_sub``), a password (``password_hash``),
or both.
"""
from uuid import UUID

import asyncpg

from app.core import database as db
from app.core.exceptions import ConflictError
from app.models.user import User

_COLS = "id, google_sub, email, name, picture, password_hash, created_at, last_login_at"


async def upsert_by_google_sub(google_sub: str, email: str, name: str | None, picture: str | None) -> User:
    """Sign in with a verified Google identity: find, link, or create the account.

    1. Known Google account: refresh its profile.
    2. Same email already registered (e.g. with a password): link Google to it.
       Google has proven this person owns the address, so any password set before
       that is removed; otherwise someone could pre-register a victim's email and
       keep access after the real owner arrives. They can always sign in with Google.
    3. Otherwise: create the account.
    """
    async with db.transaction() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE users SET email = $2, name = COALESCE($3, name), picture = COALESCE($4, picture),
                             last_login_at = now()
            WHERE google_sub = $1 RETURNING {_COLS}
            """,
            google_sub, email, name, picture,
        )
        if row is None:
            row = await conn.fetchrow(
                f"""
                UPDATE users SET google_sub = $1, password_hash = NULL, name = COALESCE(name, $3),
                                 picture = COALESCE($4, picture), last_login_at = now()
                WHERE lower(email) = lower($2) RETURNING {_COLS}
                """,
                google_sub, email, name, picture,
            )
        if row is None:
            row = await conn.fetchrow(
                f"INSERT INTO users (google_sub, email, name, picture) VALUES ($1, $2, $3, $4) RETURNING {_COLS}",
                google_sub, email, name, picture,
            )
    return User(**row)


async def create_with_password(email: str, name: str | None, password_hash: str) -> User:
    """Register a new email + password account.

    Raises:
        ConflictError: an account with this email already exists.
    """
    try:
        row = await db.fetchrow(
            f"INSERT INTO users (email, name, password_hash) VALUES ($1, $2, $3) RETURNING {_COLS}",
            email, name, password_hash,
        )
    except asyncpg.UniqueViolationError as exc:
        raise ConflictError("An account with this email already exists. Sign in instead.",
                            code="EMAIL_TAKEN") from exc
    return User(**row)


async def get_by_email(email: str) -> User | None:
    """Fetch a user by email (case-insensitive)."""
    row = await db.fetchrow(f"SELECT {_COLS} FROM users WHERE lower(email) = lower($1)", email)
    return User(**row) if row else None


async def touch_login(user_id: UUID) -> None:
    """Record a successful sign-in."""
    await db.execute("UPDATE users SET last_login_at = now() WHERE id = $1", user_id)


async def get_by_id(user_id: UUID) -> User | None:
    """Fetch a user by id."""
    row = await db.fetchrow(f"SELECT {_COLS} FROM users WHERE id = $1", user_id)
    return User(**row) if row else None
