"""Bounded mention text: ingest truncates, regex scans are capped, and the
mentions list API returns a preview."""

import asyncio
from datetime import datetime

import pytest

import harvey.dashboard as dashboard
from harvey import knowledge
from harvey.collectors.base import (
    MAX_MENTION_TEXT_CHARS,
    MAX_MENTION_TITLE_CHARS,
    Collector,
    bound_mention,
)
from harvey.compliance import compliance_filter
from harvey.ingest import run_collectors
from harvey.models import Mention, Platform
from harvey.state import StateManager


def _mention(text="hi", title="", n=1) -> Mention:
    return Mention(platform=Platform.REDDIT, external_id=f"x{n}",
                   url=f"https://www.reddit.com/r/t/comments/x{n}/", text=text, title=title)


def test_limits_have_the_agreed_values():
    assert MAX_MENTION_TEXT_CHARS == 20000
    assert MAX_MENTION_TITLE_CHARS == 500


def test_bound_mention_truncates_text_and_title_and_flags_it():
    m = _mention(text="a" * (MAX_MENTION_TEXT_CHARS + 50), title="t" * 900)

    out = bound_mention(m)

    assert len(out.text) == MAX_MENTION_TEXT_CHARS
    assert len(out.title) == MAX_MENTION_TITLE_CHARS
    assert out.engagement.get("truncated") is True
    assert len(m.text) == MAX_MENTION_TEXT_CHARS + 50  # original untouched


def test_bound_mention_leaves_short_mentions_alone():
    m = _mention(text="short", title="title")

    out = bound_mention(m)

    assert out.text == "short" and out.title == "title"
    assert "truncated" not in out.engagement


class _BigCollector(Collector):
    name = "big-test"

    async def collect(self, since: datetime | None = None):
        yield _mention(text="b" * (MAX_MENTION_TEXT_CHARS * 2), title="t" * 1000)


@pytest.mark.asyncio
async def test_ingest_stores_truncated_text(tmp_path):
    state = StateManager(str(tmp_path / "pulse.db"))
    await state.init_db()

    await run_collectors(state, [_BigCollector()])

    [stored] = await state.list_mentions(limit=5)
    assert len(stored.text) == MAX_MENTION_TEXT_CHARS
    assert len(stored.title) == MAX_MENTION_TITLE_CHARS
    assert stored.engagement.get("truncated") is True


def test_urgent_override_scans_at_most_the_limit():
    padding = "x" * MAX_MENTION_TEXT_CHARS
    assert knowledge.urgent_override("emergency room " + padding) is not None
    assert knowledge.urgent_override(padding + " emergency room") is None


def test_compliance_regexes_scan_at_most_the_limit():
    padding = "x " * MAX_MENTION_TEXT_CHARS
    late = compliance_filter(padding + " clinically proven", "facebook", ["CLM-R3-DISCLOSURE"])
    assert not any(h.rule_id == "R14" for h in late.hits)
    # The length limit still sees the full text.
    assert any(h.rule_id == "LIMIT" for h in late.hits)


@pytest.fixture
def client(tmp_path, monkeypatch):
    from tests.dashboard_helpers import VIEWER, client_for, setup_app, teardown_app

    state, _ = setup_app(tmp_path, monkeypatch)

    async def seed():
        await state.upsert_mention(_mention(text="L" * 5000, n=1))
        await state.upsert_mention(_mention(text="short text", n=2))

    asyncio.run(seed())
    client, _ = client_for(VIEWER)
    yield client
    teardown_app()


def test_mentions_api_returns_a_text_preview(client):
    rows = {r["external_id"]: r for r in client.get("/api/mentions").json()["items"]}

    assert len(rows["x1"]["text"]) == dashboard.MENTION_PREVIEW_CHARS == 2000
    assert rows["x1"]["text_truncated"] is True
    assert rows["x2"]["text"] == "short text"
    assert rows["x2"]["text_truncated"] is False
