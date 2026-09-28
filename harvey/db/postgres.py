"""Postgres (Supabase) backend: asyncpg pool, codecs and the schema check.

The schema is NOT created by the app. It is applied from db/postgres/*.sql
(see db/postgres/README.md); ``StateManager.init_db`` only checks that
``pulse.schema_version`` has caught up with ``harvey.state.MIGRATIONS``.

Pools are cached per (URL, event loop): the dashboard builds a StateManager
per request and the CLI runs one ``asyncio.run`` per command, and a pool can't
outlive the loop it was created on. ``statement_cache_size=0`` keeps asyncpg
compatible with Supabase's pooler (no named prepared statements).
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from harvey.db import dialect
from harvey.db.connection import PostgresConnection

logger = logging.getLogger("harvey.db")

POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 5
README_HINT = "db/postgres/README.md"

# (url, id(loop)) -> (loop, task creating the pool)
_POOLS: dict[tuple[str, int], tuple[asyncio.AbstractEventLoop, asyncio.Task]] = {}


def _asyncpg():
    try:
        import asyncpg
    except ImportError as exc:  # pragma: no cover - asyncpg is a hard dependency
        raise RuntimeError(
            "PULSE_DATABASE_URL points at Postgres but asyncpg is not installed; "
            "run `pip install -e .`"
        ) from exc
    return asyncpg


async def _init_connection(conn) -> None:
    """Timestamps/dates cross the wire as the same ISO strings SQLite returns."""
    await conn.set_type_codec("timestamp", schema="pg_catalog", format="text",
                              encoder=dialect.encode_timestamp, decoder=dialect.decode_timestamp)
    await conn.set_type_codec("date", schema="pg_catalog", format="text",
                              encoder=dialect.encode_date, decoder=dialect.decode_date)


async def _create_pool(url: str):
    return await _asyncpg().create_pool(
        url, min_size=POOL_MIN_SIZE, max_size=POOL_MAX_SIZE, statement_cache_size=0,
        init=_init_connection, server_settings={"application_name": "wellpeps-pulse"},
    )


async def get_pool(url: str):
    loop = asyncio.get_running_loop()
    for key, (owner, _) in list(_POOLS.items()):
        if owner.is_closed():
            _POOLS.pop(key, None)
    key = (url, id(loop))
    entry = _POOLS.get(key)
    if entry is None or entry[0] is not loop:
        entry = (loop, loop.create_task(_create_pool(url)))
        _POOLS[key] = entry
    try:
        return await asyncio.shield(entry[1])
    except Exception:
        if _POOLS.get(key) is entry:
            _POOLS.pop(key, None)
        raise


async def close_pool(url: str) -> None:
    """Close this loop's pool for ``url`` (no-op if there is none)."""
    loop = asyncio.get_running_loop()
    entry = _POOLS.pop((url, id(loop)), None)
    if entry is None or entry[0] is not loop:
        return
    try:
        pool = await entry[1]
    except Exception:  # creation failed: nothing to close
        return
    await pool.close()


async def close_loop_pools() -> None:
    """Close every pool created on the running loop."""
    loop = asyncio.get_running_loop()
    for (url, loop_id), (owner, _) in list(_POOLS.items()):
        if owner is loop:
            await close_pool(url)


def run(coro):
    """``asyncio.run`` that closes this loop's Postgres pools before the loop dies.

    The CLI runs one event loop per command; without this a pool would be
    left open when its loop closes.
    """
    async def _main():
        try:
            return await coro
        finally:
            await close_loop_pools()

    return asyncio.run(_main())


@asynccontextmanager
async def connect(url: str):
    pool = await get_pool(url)
    async with pool.acquire() as raw:
        conn = PostgresConnection(raw)
        try:
            yield conn
        finally:
            try:
                await conn.close()
            except Exception as exc:  # the pool discards a broken connection
                logger.warning("rollback on release failed: %s", type(exc).__name__)


async def schema_version(url: str) -> int | None:
    """Highest version in pulse.schema_version, or None if the schema isn't there."""
    async with connect(url) as db:
        cursor = await db.execute("SELECT to_regclass('pulse.schema_version') IS NOT NULL")
        (exists,) = await cursor.fetchone()
        if not exists:
            return None
        cursor = await db.execute("SELECT MAX(version) FROM schema_version")
        (version,) = await cursor.fetchone()
        return version


async def verify_schema(url: str, expected: int, location: str) -> int:
    """Fail loudly unless the applied schema is at least ``expected``."""
    version = await schema_version(url)
    if version is None:
        raise RuntimeError(
            f"Postgres at {location} has no pulse schema. Apply db/postgres/*.sql first; "
            f"see {README_HINT}."
        )
    if version < expected:
        raise RuntimeError(
            f"Postgres at {location} is at pulse schema version {version}, but this code "
            f"needs {expected}. Apply the newer db/postgres/*.sql files; see {README_HINT}."
        )
    return version
