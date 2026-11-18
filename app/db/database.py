"""SQLite connection + table creation (uses Python's built-in sqlite3, no extra install).

How SQLite works here:
- The whole DB is one file (settings.DATABASE_PATH). Delete it to reset everything.
- `get_db()` gives a fresh connection per request (FastAPI dependency) and closes it after.
- `init_db()` runs schema.sql once at startup; `CREATE TABLE IF NOT EXISTS` makes it safe to re-run.

Quick usage inside a service:
    cur = db.execute("INSERT INTO items (source_type, content) VALUES (?, ?)", ("note", text))
    db.commit()                       # writes need commit
    item_id = cur.lastrowid
    rows = db.execute("SELECT * FROM items").fetchall()   # rows behave like dicts: row["content"]
Always use `?` placeholders, never f-strings (prevents SQL injection).
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

from app.config.settings import settings
from app.utils.logger import log_info

SCHEMA_FILE = Path(__file__).parent / "schema.sql"


def get_connection() -> sqlite3.Connection:
    """Open a connection that returns rows accessible by column name and enforces foreign keys."""
    Path(settings.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create tables from schema.sql if they don't exist yet."""
    with get_connection() as conn:
        conn.executescript(SCHEMA_FILE.read_text())
    log_info("db.init_db.success", "Database ready", path=settings.DATABASE_PATH)


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: `db: sqlite3.Connection = Depends(get_db)`."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()
