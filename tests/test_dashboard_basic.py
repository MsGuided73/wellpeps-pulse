"""Dashboard API smoke tests against a temp database (logged in as a viewer)."""

import pytest

from harvey.models import MentionStatus
from tests.dashboard_helpers import VIEWER, add_mention, client_for, run, setup_app, teardown_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    state, _ = setup_app(tmp_path, monkeypatch)
    run(add_mention(state, "a", text="first", status=MentionStatus.TRIAGED))
    run(add_mention(state, "b", text="second"))
    client, _ = client_for(VIEWER)
    yield client
    teardown_app()


def test_mentions_lists_newest_first(client):
    resp = client.get("/api/mentions")

    assert resp.status_code == 200
    rows = resp.json()["items"]
    assert [r["external_id"] for r in rows] == ["b", "a"]
    assert {"id", "platform", "url", "text", "status", "collected_at"} <= set(rows[0])


def test_mentions_status_filter(client):
    resp = client.get("/api/mentions", params={"status": "triaged"})

    assert resp.status_code == 200
    assert [r["external_id"] for r in resp.json()["items"]] == ["a"]


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


def test_index_page_serves(client):
    resp = client.get("/")

    assert resp.status_code == 200
    assert "WellPeps Pulse" in resp.text
