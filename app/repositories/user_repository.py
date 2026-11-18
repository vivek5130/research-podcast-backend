"""Data access for the ``users`` table."""
from uuid import UUID

from app.core import database as db
from app.models.user import User

_COLS = "id, google_sub, email, name, picture, created_at, last_login_at"


async def upsert_by_google_sub(google_sub: str, email: str, name: str | None, picture: str | None) -> User:
    """Create the user on first sign-in, or refresh their profile on later sign-ins."""
    row = await db.fetchrow(
        f"""
        INSERT INTO users (google_sub, email, name, picture)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (google_sub) DO UPDATE
          SET email = EXCLUDED.email, name = COALESCE(EXCLUDED.name, users.name),
              picture = COALESCE(EXCLUDED.picture, users.picture), last_login_at = now()
        RETURNING {_COLS}
        """,
        google_sub, email, name, picture,
    )
    return User(**row)


async def get_by_id(user_id: UUID) -> User | None:
    """Fetch a user by id."""
    row = await db.fetchrow(f"SELECT {_COLS} FROM users WHERE id = $1", user_id)
    return User(**row) if row else None
