"""SQLite -> Postgres dialect translation and backend selection (no database needed)."""

from datetime import datetime, timedelta, timezone

import pytest

from harvey import db as dbmod
from harvey.db import dialect
from harvey.state import StateManager

# ── Placeholders ──


def test_question_marks_become_numbered_placeholders():
    sql = "SELECT * FROM mentions WHERE platform = ? AND external_id = ? LIMIT ?"
    assert dialect.to_postgres(sql) == (
        "SELECT * FROM mentions WHERE platform = $1 AND external_id = $2 LIMIT $3"
    )


def test_question_marks_inside_string_literals_are_left_alone():
    sql = "SELECT '?' AS q, 'it''s ?' AS r FROM t WHERE a = ?"
    assert dialect.to_postgres(sql) == "SELECT '?' AS q, 'it''s ?' AS r FROM t WHERE a = $1"


def test_question_marks_inside_quoted_identifiers_and_comments_are_left_alone():
    sql = 'SELECT "we?rd" FROM t -- why?\nWHERE a = ?'
    assert dialect.to_postgres(sql) == 'SELECT "we?rd" FROM t -- why?\nWHERE a = $1'


def test_unterminated_literal_is_rejected():
    with pytest.raises(ValueError, match="unterminated"):
        dialect.to_postgres("SELECT 'oops FROM t")


# ── INSERT OR IGNORE ──


def test_insert_or_ignore_becomes_on_conflict_do_nothing():
    sql = "INSERT OR IGNORE INTO language_bank_mentions (mention_id) VALUES (?)"
    assert dialect.to_postgres(sql) == (
        "INSERT INTO language_bank_mentions (mention_id) VALUES ($1) ON CONFLICT DO NOTHING"
    )


def test_insert_or_ignore_puts_on_conflict_before_returning():
    sql = "INSERT OR IGNORE INTO mentions (url) VALUES (?) RETURNING id"
    assert dialect.to_postgres(sql) == (
        "INSERT INTO mentions (url) VALUES ($1) ON CONFLICT DO NOTHING RETURNING id"
    )


def test_insert_or_ignore_with_trailing_whitespace_and_newlines():
    sql = """INSERT OR IGNORE INTO usage_events
       (id, created_at) VALUES (?, COALESCE(?, CURRENT_TIMESTAMP))
    """
    out = dialect.to_postgres(sql)
    assert out.startswith("INSERT INTO usage_events")
    assert out.endswith("ON CONFLICT DO NOTHING")
    assert "COALESCE($2, (now() AT TIME ZONE 'utc'))" in out


def test_insert_or_ignore_after_a_leading_comment():
    sql = "-- bank once\nINSERT OR IGNORE INTO t (a) VALUES (?)"
    assert dialect.to_postgres(sql) == "-- bank once\nINSERT INTO t (a) VALUES ($1) ON CONFLICT DO NOTHING"


def test_insert_or_ignore_before_a_trailing_comment():
    sql = "INSERT OR IGNORE INTO t (a) VALUES (?); -- once"
    assert dialect.to_postgres(sql) == "INSERT INTO t (a) VALUES ($1) ON CONFLICT DO NOTHING -- once"


def test_returning_in_a_comment_does_not_count():
    assert dialect.returns_rows("UPDATE t SET a = 1 -- no RETURNING here") is False


def test_insert_or_ignore_text_inside_a_literal_is_not_rewritten():
    sql = "INSERT INTO notes (body) VALUES ('INSERT OR IGNORE INTO x')"
    assert dialect.to_postgres(sql) == sql


# ── Explicit rewrite table ──


def test_current_timestamp_is_naive_utc_on_postgres():
    out = dialect.to_postgres("UPDATE runs SET ended_at = CURRENT_TIMESTAMP WHERE id = ?")
    assert out == "UPDATE runs SET ended_at = (now() AT TIME ZONE 'utc') WHERE id = $1"


def test_current_timestamp_inside_a_string_is_not_rewritten():
    assert dialect.to_postgres("SELECT 'CURRENT_TIMESTAMP'") == "SELECT 'CURRENT_TIMESTAMP'"


def test_instr_becomes_strpos():
    out = dialect.to_postgres("SELECT 1 FROM t WHERE instr(lower(m.text), lower(?)) > 0")
    assert out == "SELECT 1 FROM t WHERE strpos(lower(m.text), lower($1)) > 0"


def test_identifier_containing_instr_is_not_rewritten():
    assert dialect.to_postgres("SELECT reinstr(a) FROM t") == "SELECT reinstr(a) FROM t"


@pytest.mark.parametrize("sql", [
    "PRAGMA foreign_keys=ON",
    "INSERT OR REPLACE INTO triage (mention_id) VALUES (?)",
    "SELECT * FROM runs WHERE started_at < datetime('now', ?)",
    "SELECT date('now')",
    "SELECT strftime('%Y', created_at) FROM usage_events",
    "SELECT julianday(created_at) FROM usage_events",
    "INSERT OR IGNORE INTO t (a) VALUES (?) ON CONFLICT DO NOTHING",
])
def test_sqlite_only_constructs_are_rejected(sql):
    with pytest.raises(dialect.NotPortableError):
        dialect.to_postgres(sql)


def test_check_portable_accepts_portable_sql():
    dialect.check_portable("SELECT COUNT(*) FROM mentions WHERE status = ?")


def test_translation_is_cached():
    sql = "SELECT * FROM settings WHERE key = ?"
    assert dialect.to_postgres(sql) is dialect.to_postgres(sql)


# ── Statement classification ──


@pytest.mark.parametrize("sql,expected", [
    ("SELECT 1", True),
    ("  with x as (select 1) select * from x", True),
    ("INSERT INTO t (a) VALUES (?) RETURNING id", True),
    ("INSERT INTO t (a) VALUES (?)", False),
    ("UPDATE t SET a = ? WHERE id = ?", False),
    ("DELETE FROM t WHERE a = 'RETURNING'", False),
])
def test_returns_rows(sql, expected):
    assert dialect.returns_rows(sql) is expected


@pytest.mark.parametrize("status,expected", [
    ("INSERT 0 1", 1), ("INSERT 0 0", 0), ("UPDATE 3", 3), ("DELETE 2", 2),
    ("CREATE TABLE", -1), ("", -1), (None, -1),
])
def test_rowcount_from_status(status, expected):
    assert dialect.rowcount_from_status(status) == expected


# ── Timestamp codec (the app sees the same naive-UTC ISO strings on both backends) ──


def test_encode_timestamp_accepts_iso_strings_and_datetimes():
    assert dialect.encode_timestamp("2026-09-27T10:00:00") == "2026-09-27 10:00:00"
    assert dialect.encode_timestamp("2026-09-27 10:00:00.5") == "2026-09-27 10:00:00.500000"
    assert dialect.encode_timestamp(datetime(2026, 9, 27, 10)) == "2026-09-27 10:00:00"


def test_encode_timestamp_converts_aware_values_to_naive_utc():
    aware = datetime(2026, 9, 27, 12, tzinfo=timezone(timedelta(hours=2)))
    assert dialect.encode_timestamp(aware) == "2026-09-27 10:00:00"
    assert dialect.encode_timestamp("2026-09-27T12:00:00+02:00") == "2026-09-27 10:00:00"


def test_encode_timestamp_rejects_junk():
    with pytest.raises(ValueError):
        dialect.encode_timestamp("not a time")


def test_decode_timestamp_matches_python_isoformat():
    assert dialect.decode_timestamp("2026-09-27 10:00:00") == "2026-09-27T10:00:00"
    assert dialect.decode_timestamp("2026-09-27 10:00:00.123456") == "2026-09-27T10:00:00.123456"
    assert dialect.decode_timestamp("2026-09-27 10:00:00.5") == "2026-09-27T10:00:00.500000"


def test_sql_now_minus_matches_sqlite_datetime_format():
    now = datetime(2026, 9, 27, 12, 30, 45, 999)
    assert dialect.sql_utc(now - timedelta(hours=6)) == "2026-09-27 06:30:45"


# ── Backend selection ──


def test_is_postgres_url():
    assert dbmod.is_postgres_url("postgresql://u:p@h:5432/db")
    assert dbmod.is_postgres_url("postgres://u:p@h/db")
    assert not dbmod.is_postgres_url("")
    assert not dbmod.is_postgres_url("sqlite:///data/pulse.db")


def test_redact_url_hides_the_password():
    redacted = dbmod.redact_url("postgresql://postgres.ref:s3cret@aws-0.pooler.supabase.com:5432/postgres")
    assert "s3cret" not in redacted
    assert "aws-0.pooler.supabase.com" in redacted


def test_env_database_url_blank_means_sqlite(monkeypatch):
    monkeypatch.setenv("PULSE_DATABASE_URL", "")
    assert dbmod.env_database_url() == ""


def test_env_database_url_reads_environment(monkeypatch):
    monkeypatch.setenv("PULSE_DATABASE_URL", "  postgresql://u:p@h/db  ")
    assert dbmod.env_database_url() == "postgresql://u:p@h/db"


def test_env_database_url_rejects_non_postgres_values(monkeypatch):
    monkeypatch.setenv("PULSE_DATABASE_URL", "mysql://u:p@h/db")
    with pytest.raises(ValueError, match="PULSE_DATABASE_URL"):
        dbmod.env_database_url()


def test_env_database_url_falls_back_to_dotenv_file(monkeypatch, tmp_path):
    monkeypatch.delenv("PULSE_DATABASE_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("PULSE_DATABASE_URL=postgresql://u:p@h/db\n", encoding="utf-8")
    monkeypatch.setattr(dbmod, "ENV_FILE", env_file)
    assert dbmod.env_database_url() == "postgresql://u:p@h/db"


def test_state_manager_defaults_to_sqlite(monkeypatch, tmp_path):
    monkeypatch.setenv("PULSE_DATABASE_URL", "")
    sm = StateManager(str(tmp_path / "x.db"))
    assert sm.backend == "sqlite"
    assert sm.db_path == str(tmp_path / "x.db")


def test_explicit_db_path_wins_over_env_url(monkeypatch, tmp_path):
    monkeypatch.setenv("PULSE_DATABASE_URL", "postgresql://u:p@h/db")
    sm = StateManager(str(tmp_path / "x.db"))
    assert sm.backend == "sqlite"


def test_no_args_uses_env_url(monkeypatch):
    monkeypatch.setenv("PULSE_DATABASE_URL", "postgresql://u:p@h/db")
    sm = StateManager()
    assert sm.backend == "postgres"
    assert sm.db_path is None
    assert "p@" not in sm.location


def test_from_env_picks_postgres_when_url_set(monkeypatch, tmp_path):
    monkeypatch.setenv("PULSE_DATABASE_URL", "postgresql://u:p@h/db")
    assert StateManager.from_env(tmp_path / "x.db").backend == "postgres"
    monkeypatch.setenv("PULSE_DATABASE_URL", "")
    sm = StateManager.from_env(tmp_path / "x.db")
    assert sm.backend == "sqlite"
    assert sm.db_path == str(tmp_path / "x.db")


def test_explicit_database_url_must_be_postgres():
    with pytest.raises(ValueError, match="postgres"):
        StateManager(database_url="mysql://u:p@h/db")


@pytest.mark.asyncio
async def test_postgres_init_db_fails_clearly_when_schema_is_missing(monkeypatch):
    from harvey.db import postgres

    async def fake_version(url):
        return None

    monkeypatch.setattr(postgres, "schema_version", fake_version)
    sm = StateManager(database_url="postgresql://u:p@h/db")
    with pytest.raises(RuntimeError, match="db/postgres/README.md"):
        await sm.init_db()


@pytest.mark.asyncio
async def test_postgres_init_db_fails_when_schema_is_behind(monkeypatch):
    from harvey.db import postgres
    from harvey.state import MIGRATIONS

    async def fake_version(url):
        return len(MIGRATIONS) - 1

    monkeypatch.setattr(postgres, "schema_version", fake_version)
    sm = StateManager(database_url="postgresql://u:p@h/db")
    with pytest.raises(RuntimeError, match=f"version {len(MIGRATIONS) - 1}"):
        await sm.init_db()


@pytest.mark.asyncio
async def test_postgres_init_db_accepts_current_schema(monkeypatch):
    from harvey.db import postgres
    from harvey.state import MIGRATIONS

    async def fake_version(url):
        return len(MIGRATIONS)

    monkeypatch.setattr(postgres, "schema_version", fake_version)
    await StateManager(database_url="postgresql://u:p@h/db").init_db()


# ── Every query the app runs is portable ──


@pytest.mark.asyncio
async def test_sqlite_connection_rejects_non_portable_sql(tmp_path):
    sm = StateManager(str(tmp_path / "x.db"))
    await sm.init_db()
    async with sm.connect() as conn:
        with pytest.raises(dialect.NotPortableError):
            await conn.execute("SELECT datetime('now')")


# ── Pool lifecycle (fake pools; no database) ──


def _fake_pools(monkeypatch):
    from harvey.db import postgres

    made, closed = [], []

    class FakePool:
        async def close(self):
            closed.append(self)

    async def fake_create(url):
        pool = FakePool()
        made.append(pool)
        return pool

    monkeypatch.setattr(postgres, "_create_pool", fake_create)
    monkeypatch.setattr(postgres, "_POOLS", {})
    return postgres, made, closed


def test_pool_is_reused_within_a_loop_and_closed_by_run(monkeypatch):
    postgres, made, closed = _fake_pools(monkeypatch)

    async def work():
        first = await postgres.get_pool("postgresql://u@h/db")
        second = await postgres.get_pool("postgresql://u@h/db")
        return first is second

    assert postgres.run(work()) is True
    assert len(made) == 1 and closed == made
    assert postgres._POOLS == {}


def test_each_event_loop_gets_its_own_pool(monkeypatch):
    postgres, made, closed = _fake_pools(monkeypatch)

    async def work():
        return await postgres.get_pool("postgresql://u@h/db")

    assert postgres.run(work()) is not postgres.run(work())
    assert len(made) == 2 and len(closed) == 2


def test_failed_pool_creation_is_not_cached(monkeypatch):
    from harvey.db import postgres

    calls = []

    async def boom(url):
        calls.append(url)
        raise OSError("connection refused")

    monkeypatch.setattr(postgres, "_create_pool", boom)
    monkeypatch.setattr(postgres, "_POOLS", {})

    async def work():
        for _ in range(2):
            with pytest.raises(OSError):
                await postgres.get_pool("postgresql://u@h/db")

    postgres.run(work())
    assert len(calls) == 2
