"""State layer plumbing kept from Harvey: migrations, settings, runs, actions, usage."""

import os

import aiosqlite
import pytest
import pytest_asyncio

from harvey.state import MIGRATIONS, StateManager


@pytest_asyncio.fixture
async def state(tmp_path):
    """StateManager backed by a temp SQLite DB."""
    sm = StateManager(str(tmp_path / "test.db"))
    await sm.init_db()
    yield sm


async def _tables(db_path: str) -> set[str]:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT name FROM sqlite_master WHERE type = 'table'") as cur:
            return {row[0] for row in await cur.fetchall()}


# ── Init / migrations ──


@pytest.mark.asyncio
async def test_init_db_is_idempotent(tmp_path):
    sm = StateManager(str(tmp_path / "test.db"))
    await sm.init_db()
    await sm.init_db()  # second call must not raise
    assert os.path.exists(sm.db_path)


@pytest.mark.asyncio
async def test_init_db_creates_parent_dirs(tmp_path):
    sm = StateManager(str(tmp_path / "nested" / "dirs" / "test.db"))
    await sm.init_db()
    assert os.path.exists(sm.db_path)


@pytest.mark.asyncio
async def test_migrations_set_user_version(state):
    async with aiosqlite.connect(state.db_path) as db:
        async with db.execute("PRAGMA user_version") as cur:
            (version,) = await cur.fetchone()
    assert version == len(MIGRATIONS)


@pytest.mark.asyncio
async def test_schema_has_kept_and_new_tables(state):
    tables = await _tables(state.db_path)

    kept = {"settings", "runs", "actions", "usage_events", "usage_log"}
    new = {
        "sources", "mentions", "triage", "escalations", "drafts", "audit_log",
        "briefs", "trend_terms", "language_bank", "users", "sessions",
    }
    assert kept | new <= tables


@pytest.mark.asyncio
async def test_schema_has_no_sales_tables(state):
    tables = await _tables(state.db_path)

    assert not tables & {"companies", "outbox", "conversations", "observations"}


def test_default_db_is_pulse_db():
    from harvey.paths import DB_PATH

    assert DB_PATH.name == "pulse.db"


# ── Settings ──


@pytest.mark.asyncio
async def test_settings_roundtrip_and_default(state):
    assert await state.get_setting("paused", "no") == "no"
    await state.set_setting("paused", "yes")
    await state.set_setting("paused", "still-yes")
    assert await state.get_setting("paused") == "still-yes"


@pytest.mark.asyncio
async def test_increment_setting(state):
    assert await state.increment_setting("counter") == 1
    assert await state.increment_setting("counter", by=4) == 5


# ── Runs ──


@pytest.mark.asyncio
async def test_run_lifecycle(state):
    run_id = await state.start_run("collect", provider="fixture", params={"q": "x"})
    await state.finish_run(run_id, records=3)

    runs = await state.get_runs()
    assert runs[0]["id"] == run_id
    assert runs[0]["status"] == "completed"
    assert runs[0]["records"] == 3


@pytest.mark.asyncio
async def test_sweep_stale_runs_ignores_fresh_runs(state):
    await state.start_run("collect")
    assert await state.sweep_stale_runs(older_than_hours=6) == 0


# ── Actions / usage ──


@pytest.mark.asyncio
async def test_log_action_with_and_without_details(state):
    await state.log_action("idle", "main", {"reason": "stub"})
    await state.log_action("idle", "main")  # details=None path must not raise


@pytest.mark.asyncio
async def test_usage_today_zero_when_empty(state):
    assert await state.get_usage_today() == 0


@pytest.mark.asyncio
async def test_usage_counter_upserts(state):
    await state.increment_usage()
    await state.increment_usage()
    await state.increment_usage()
    assert await state.get_usage_today() == 3
