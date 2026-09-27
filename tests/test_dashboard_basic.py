"""Dashboard API smoke tests against a temp database."""

import asyncio

import pytest
from fastapi.testclient import TestClient

import harvey.dashboard as dashboard
from harvey.models import Mention, MentionStatus, Platform
from harvey.state import StateManager


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "pulse.db"

    async def seed():
        state = StateManager(str(db_path))
        await state.init_db()
        first, _ = await state.upsert_mention(
            Mention(platform=Platform.REDDIT, external_id="a",
                    url="https://www.reddit.com/r/x/comments/a", text="first")
        )
        await state.upsert_mention(
            Mention(platform=Platform.TRUSTPILOT, external_id="b",
                    url="https://www.trustpilot.com/reviews/b", text="second")
        )
        await state.set_mention_status(first, MentionStatus.TRIAGED)

    asyncio.run(seed())
    monkeypatch.setattr(dashboard, "DB_PATH", db_path)
    return TestClient(dashboard.app)


def test_mentions_lists_newest_first(client):
    resp = client.get("/api/mentions")

    assert resp.status_code == 200
    rows = resp.json()
    assert [r["external_id"] for r in rows] == ["b", "a"]
    assert {"id", "platform", "url", "text", "status", "collected_at"} <= set(rows[0])


def test_mentions_status_filter(client):
    resp = client.get("/api/mentions", params={"status": "triaged"})

    assert resp.status_code == 200
    assert [r["external_id"] for r in resp.json()] == ["a"]


def test_mentions_rejects_unknown_status(client):
    assert client.get("/api/mentions", params={"status": "bogus"}).status_code == 400


def test_summary_shape(client):
    resp = client.get("/api/summary")

    assert resp.status_code == 200
    body = resp.json()
    assert body["mentions"]["new"] == 1
    assert body["mentions"]["triaged"] == 1
    assert body["total"] == 2
    assert body["open_escalations"] == 0


def test_summary_on_missing_database_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "DB_PATH", tmp_path / "absent.db")
    client = TestClient(dashboard.app)

    body = client.get("/api/summary").json()

    assert body["total"] == 0
    assert client.get("/api/mentions").json() == []


def test_index_page_serves(client):
    resp = client.get("/")

    assert resp.status_code == 200
    assert "WellPeps Pulse" in resp.text
