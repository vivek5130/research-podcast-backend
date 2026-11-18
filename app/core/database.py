"""Postgres connection pool and tiny query helpers (asyncpg).

Why asyncpg + raw SQL (no ORM): the queries are few and explicit, the scheduler
needs Postgres-specific SQL (``FOR UPDATE SKIP LOCKED``, CTEs, ``AT TIME ZONE``),
and there is nothing to hide behind an abstraction.

Serverless notes:
* The pool is created lazily on first use and reused while the instance is warm.
* ``statement_cache_size=0`` is required behind Supabase's transaction pooler
  (pgbouncer, port 6543), which cannot keep prepared statements between transactions.
* ``jsonb`` columns are decoded to Python objects automatically (see ``_init_connection``).
"""
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from app.core.config import get_settings

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Teach every new connection to convert jsonb <-> Python dict/list."""
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def get_pool() -> asyncpg.Pool:
    """Return the shared pool, creating it on first call."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            get_settings().database_url,
            min_size=1,  # keep one warm connection: a fresh connect to the pooler costs ~230 ms
            max_inactive_connection_lifetime=600,
            max_size=5,
            statement_cache_size=0,
            command_timeout=30,
            init=_init_connection,
        )
    return _pool


async def close_pool() -> None:
    """Close the pool (called on application shutdown)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def fetch(query: str, *args) -> list[dict]:
    """Run a query and return all rows as dicts."""
    rows = await (await get_pool()).fetch(query, *args)
    return [dict(r) for r in rows]


async def fetchrow(query: str, *args) -> dict | None:
    """Run a query and return the first row as a dict, or None."""
    row = await (await get_pool()).fetchrow(query, *args)
    return dict(row) if row else None


async def execute(query: str, *args) -> str:
    """Run a statement and return its status string (e.g. ``'UPDATE 1'``)."""
    return await (await get_pool()).execute(query, *args)


@asynccontextmanager
async def transaction() -> AsyncIterator[asyncpg.Connection]:
    """A connection inside one transaction (commit on success, rollback on error).

    Needed when rows locked with ``FOR UPDATE`` must stay locked across several statements.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        yield conn
