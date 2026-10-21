"""drafts.context_json (migration 10, db/postgres/0006_draft_context.sql): the
Knowledge-library sections a draft relied on, as ids and names (never text)."""

from pathlib import Path

import pytest
import pytest_asyncio

from harvey.models import Draft, Mention, Platform, ReviewVerdict
from harvey.state import MIGRATIONS, StateManager

PG_FILE = Path(__file__).resolve().parent.parent / "db" / "postgres" / "0006_draft_context.sql"
RECORDS = [{"section_id": "16:2:voice-principles", "doc_id": 16, "doc_name": "Community Agent Voice",
            "heading": "Voice principles"}]


@pytest_asyncio.fixture
async def state(tmp_path):
    sm = StateManager(str(tmp_path / "pulse.db"))
    await sm.init_db()
    yield sm


async def _mention(state) -> int:
    mid, _ = await state.upsert_mention(Mention(platform=Platform.REDDIT, external_id="c1", text="hello",
                                                url="https://www.reddit.com/r/t/comments/c1/"))
    return mid


def test_migration_10_adds_the_column():
    assert len(MIGRATIONS) == 10
    assert "ALTER TABLE drafts ADD COLUMN context_json TEXT" in MIGRATIONS[9]


def test_postgres_0006_is_idempotent_and_bumps_the_version_to_10():
    sql = PG_FILE.read_text(encoding="utf-8").lower()
    assert "alter table pulse.drafts add column if not exists context_json text" in sql
    assert "values (10," in sql and "on conflict (version) do nothing" in sql


@pytest.mark.asyncio
async def test_context_is_stored_and_read_back(state):
    mid = await _mention(state)
    await state.add_draft(Draft(mention_id=mid, text="reply", review_verdict=ReviewVerdict.PASS, context=RECORDS))

    draft = await state.get_latest_draft(mid)

    assert draft.context == RECORDS


@pytest.mark.asyncio
async def test_a_draft_without_context_reads_back_empty(state):
    mid = await _mention(state)
    await state.add_draft(Draft(mention_id=mid, text="reply"))

    draft = await state.get_latest_draft(mid)

    assert draft.context == []
    async with state.connect() as db:
        async with db.execute("SELECT context_json FROM drafts WHERE mention_id = ?", (mid,)) as cursor:
            (raw,) = await cursor.fetchone()
    assert raw is None
