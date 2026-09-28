"""The Postgres schema (db/postgres/*.sql) must not drift from the SQLite migrations.

The SQLite schema is introspected from a fresh database after init_db; the
Postgres schema is parsed from the numbered SQL files. Tables, column names
and index names must match, so a migration added on one side only fails here.
"""

import re
import sqlite3
from pathlib import Path

import pytest

from harvey.state import MIGRATIONS, StateManager

PG_DIR = Path(__file__).resolve().parent.parent / "db" / "postgres"
PG_ONLY_TABLES = {"schema_version"}
CONSTRAINT_WORDS = {"unique", "primary", "constraint", "foreign", "check", "exclude"}


def _pg_sql() -> str:
    files = sorted(PG_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    assert files, f"no numbered migrations in {PG_DIR}"
    text = "\n".join(f.read_text(encoding="utf-8") for f in files)
    return re.sub(r"--[^\n]*", "", text)  # drop comments


def _split_top_level(body: str) -> list[str]:
    parts, depth, current = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _pg_tables() -> dict[str, set[str]]:
    sql = _pg_sql()
    tables: dict[str, set[str]] = {}
    pattern = re.compile(
        r"create\s+table\s+(?:if\s+not\s+exists\s+)?pulse\.(\w+)\s*\((.*?)\)\s*;",
        re.IGNORECASE | re.DOTALL,
    )
    for name, body in pattern.findall(sql):
        columns = set()
        for item in _split_top_level(body):
            first = item.split()[0].strip('"').lower()
            if first not in CONSTRAINT_WORDS:
                columns.add(first)
        tables[name.lower()] = columns
    for name, column in re.findall(
        r"alter\s+table\s+(?:if\s+exists\s+)?pulse\.(\w+)\s+add\s+column\s+(?:if\s+not\s+exists\s+)?(\w+)",
        sql, re.IGNORECASE,
    ):
        tables[name.lower()].add(column.lower())
    return tables


def _pg_indexes() -> set[str]:
    return {m.lower() for m in re.findall(
        r"create\s+(?:unique\s+)?index\s+(?:if\s+not\s+exists\s+)?(\w+)\s+on\s+pulse\.",
        _pg_sql(), re.IGNORECASE,
    )}


@pytest.fixture(scope="module")
def sqlite_schema(tmp_path_factory):
    import asyncio

    path = tmp_path_factory.mktemp("schema") / "schema.db"
    asyncio.run(StateManager(str(path)).init_db())
    with sqlite3.connect(path) as db:
        tables = [r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        columns = {t: {r[1].lower() for r in db.execute(f"PRAGMA table_info({t})")} for t in tables}
        indexes = {r[0].lower() for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'")}
    return columns, indexes


def test_postgres_has_exactly_the_sqlite_tables(sqlite_schema):
    columns, _ = sqlite_schema
    pg = _pg_tables()
    assert set(pg) - PG_ONLY_TABLES == set(columns)


def test_postgres_columns_match_sqlite(sqlite_schema):
    columns, _ = sqlite_schema
    pg = _pg_tables()
    mismatches = {t: {"sqlite_only": sorted(cols - pg.get(t, set())),
                      "postgres_only": sorted(pg.get(t, set()) - cols)}
                  for t, cols in columns.items() if cols != pg.get(t)}
    assert not mismatches, f"schema drift: {mismatches}"


def test_postgres_has_every_sqlite_index(sqlite_schema):
    _, indexes = sqlite_schema
    assert indexes - _pg_indexes() == set()


def test_postgres_schema_version_matches_migrations():
    versions = [int(v) for v in re.findall(
        r"insert\s+into\s+pulse\.schema_version\s*\(\s*version[^)]*\)\s*values\s*\(\s*(\d+)",
        _pg_sql(), re.IGNORECASE)]
    assert versions and max(versions) == len(MIGRATIONS)


def test_every_postgres_table_has_row_level_security():
    sql = _pg_sql()
    rls = {m.lower() for m in re.findall(
        r"alter\s+table\s+pulse\.(\w+)\s+enable\s+row\s+level\s+security", sql, re.IGNORECASE)}
    assert set(_pg_tables()) <= rls


def test_audit_log_is_append_only_in_postgres():
    sql = _pg_sql().lower()
    assert re.search(r"before\s+update\s+or\s+delete\s+on\s+pulse\.audit_log\s+for\s+each\s+row", sql)
    assert re.search(r"before\s+truncate\s+on\s+pulse\.audit_log\s+for\s+each\s+statement", sql)
    assert "set search_path = ''" in sql


def test_postgres_schema_is_not_exposed_to_supabase_api_roles():
    sql = _pg_sql().lower()
    assert "revoke all on schema pulse from" in sql
    for role in ("anon", "authenticated", "public"):
        assert role in sql
    assert not re.search(r"\bpassword\s+'", sql), "no role passwords in migrations"
