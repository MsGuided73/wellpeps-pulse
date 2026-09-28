"""One connection interface over aiosqlite and asyncpg.

Call sites use the aiosqlite shape they always had::

    async with state.connect() as db:
        cursor = await db.execute(sql, params)        # or: async with db.execute(...) as cursor
        row = await cursor.fetchone()                 # rows support row[0], row["col"], dict(row)
        cursor.rowcount
        await db.commit()                             # uncommitted work rolls back on close

SQL is written once, SQLite-flavoured and portable (see ``dialect``). There is
no ``lastrowid``: inserts that need the new id say ``RETURNING id``.
"""

from harvey.db import dialect


class Cursor:
    """A fully fetched result: rows plus the affected-row count."""

    def __init__(self, rows: list, rowcount: int):
        self._rows = list(rows)
        self._next = 0
        self.rowcount = rowcount

    async def fetchone(self):
        if self._next >= len(self._rows):
            return None
        row = self._rows[self._next]
        self._next += 1
        return row

    async def fetchall(self) -> list:
        rows = self._rows[self._next:]
        self._next = len(self._rows)
        return rows


class _Pending:
    """``db.execute(...)`` result: awaitable, or usable as ``async with``."""

    def __init__(self, coro):
        self._coro = coro

    def __await__(self):
        return self._coro.__await__()

    async def __aenter__(self) -> Cursor:
        return await self._coro

    async def __aexit__(self, *exc) -> bool:
        return False


class SqliteConnection:
    """aiosqlite connection (row_factory = aiosqlite.Row) behind the shared interface.

    Every statement is checked with ``dialect.check_portable`` so the SQLite
    test suite catches SQL the Postgres backend could not run.
    """

    backend = "sqlite"

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params=()) -> _Pending:
        return _Pending(self._run(sql, params))

    async def _run(self, sql: str, params) -> Cursor:
        dialect.check_portable(sql)
        async with self._conn.execute(sql, tuple(params)) as cur:
            rows = await cur.fetchall()
            return Cursor(rows, cur.rowcount)

    async def executemany(self, sql: str, seq_of_params) -> None:
        dialect.check_portable(sql)
        await self._conn.executemany(sql, [tuple(p) for p in seq_of_params])

    async def commit(self) -> None:
        await self._conn.commit()

    async def rollback(self) -> None:
        await self._conn.rollback()


class PostgresConnection:
    """asyncpg connection behind the shared interface.

    Mirrors sqlite3's implicit transactions: the first statement opens a
    transaction, ``commit()``/``rollback()`` end it, and ``close()`` rolls
    back anything left uncommitted. Each transaction pins ``search_path`` to
    ``pulse`` with SET LOCAL semantics, so it also works through a
    transaction-mode pooler. The session time zone is irrelevant: columns are
    naive-UTC ``timestamp`` and every "now" is ``now() AT TIME ZONE 'utc'``.
    """

    backend = "postgres"
    TX_SETUP = "SELECT set_config('search_path', 'pulse', true)"

    def __init__(self, conn):
        self._conn = conn
        self._tx = None

    async def _begin(self) -> None:
        if self._tx is None:
            self._tx = self._conn.transaction()
            await self._tx.start()
            await self._conn.execute(self.TX_SETUP)

    def execute(self, sql: str, params=()) -> _Pending:
        return _Pending(self._run(sql, params))

    async def _run(self, sql: str, params) -> Cursor:
        query = dialect.to_postgres(sql)
        args = tuple(params)
        await self._begin()
        if dialect.returns_rows(sql):
            rows = await self._conn.fetch(query, *args)
            return Cursor(rows, len(rows))
        status = await self._conn.execute(query, *args)
        return Cursor([], dialect.rowcount_from_status(status))

    async def executemany(self, sql: str, seq_of_params) -> None:
        rows = [tuple(p) for p in seq_of_params]
        if not rows:
            return
        await self._begin()
        await self._conn.executemany(dialect.to_postgres(sql), rows)

    async def commit(self) -> None:
        if self._tx is not None:
            tx, self._tx = self._tx, None
            await tx.commit()

    async def rollback(self) -> None:
        if self._tx is not None:
            tx, self._tx = self._tx, None
            await tx.rollback()

    async def close(self) -> None:
        await self.rollback()
