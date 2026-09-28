"""Dashboard Pulse tab API (Phase 8): briefs, live trends, language bank,
and admin-only brief generation."""

from datetime import datetime, timedelta, timezone

import pytest

import harvey.dashboard as dashboard
from harvey import briefs, pulse_store, trends
from harvey.config import PulseConfig
from tests.dashboard_helpers import (
    ADMIN,
    REVIEWER,
    VIEWER,
    client_for,
    post,
    run,
    setup_app,
    teardown_app,
)
from tests.pulse_helpers import FakeBrain, add_triaged, good_answer


def _in_daily_window(minutes: int) -> datetime:
    """A moment inside the daily brief window as of the real clock."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start, _ = briefs.window_for("daily", now, PulseConfig().usage.quiet_hours.timezone)
    return start + timedelta(hours=1, minutes=minutes)


@pytest.fixture
def pulse_app(tmp_path, monkeypatch):
    state, notifier = setup_app(tmp_path, monkeypatch)
    brain = FakeBrain([good_answer() for _ in range(5)])
    dashboard.app.dependency_overrides[dashboard.get_brief_brain] = lambda: brain
    for i in range(4):
        run(add_triaged(state, f"p{i}", text="another shipping delay again this week",
                        posted_at=_in_daily_window(i), competitor="Hims & Hers", subject_type="competitor",
                        category="complaint", drug="tirzepatide",
                        phrases=["shipping delay again", f"phrase number {i}"]))
    run(trends.bank_language(state))
    yield state, brain, notifier
    teardown_app()


GETS = ["/api/briefs", "/api/trends", "/api/language-bank"]


@pytest.mark.parametrize("path", GETS)
def test_pulse_reads_need_a_session(pulse_app, path):
    client, _ = client_for()
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", GETS)
def test_viewer_can_read_pulse(pulse_app, path):
    client, _ = client_for(VIEWER)
    assert client.get(path).status_code == 200


def test_generate_needs_session_csrf_and_admin(pulse_app):
    state, brain, _ = pulse_app
    anon, _ = client_for()
    assert anon.post("/api/briefs/generate", json={"period": "daily"}).status_code == 401
    for email in (VIEWER, REVIEWER):
        client, csrf = client_for(email)
        assert post(client, csrf, "/api/briefs/generate", {"period": "daily"}).status_code == 403
    admin, csrf = client_for(ADMIN)
    assert admin.post("/api/briefs/generate", json={"period": "daily"}).status_code == 403  # no CSRF
    assert brain.calls == []

    resp = post(admin, csrf, "/api/briefs/generate", {"period": "daily"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] is True and body["headline"] == good_answer()["headline"]
    assert brain.calls == [("pulse", "brief")]


def test_generate_is_idempotent_unless_forced(pulse_app):
    _, brain, _ = pulse_app
    admin, csrf = client_for(ADMIN)
    first = post(admin, csrf, "/api/briefs/generate", {"period": "daily"}).json()
    again = post(admin, csrf, "/api/briefs/generate", {"period": "daily"}).json()
    forced = post(admin, csrf, "/api/briefs/generate", {"period": "daily", "force": True}).json()

    assert first["id"] == again["id"] == forced["id"]
    assert again["created"] is False
    assert len(brain.calls) == 2


def test_generate_rejects_unknown_period(pulse_app):
    admin, csrf = client_for(ADMIN)
    assert post(admin, csrf, "/api/briefs/generate", {"period": "monthly"}).status_code == 422


def test_brief_list_and_detail_with_trend_terms(pulse_app):
    admin, csrf = client_for(ADMIN)
    brief_id = post(admin, csrf, "/api/briefs/generate", {"period": "daily"}).json()["id"]
    client, _ = client_for(VIEWER)

    listed = client.get("/api/briefs?period=daily&limit=5").json()
    assert [b["id"] for b in listed["items"]] == [brief_id]
    assert "data" not in listed["items"][0]  # the list is light
    detail = client.get(f"/api/briefs/{brief_id}").json()
    assert detail["headline"] == good_answer()["headline"]
    assert len(detail["action_cards"]) == 3
    assert "shipping delay" in [t["term"] for t in detail["trend_terms"]]
    assert detail["data"]["share_of_voice"][0]["subject"] == "Hims & Hers"
    assert client.get("/api/briefs/99999").status_code == 404
    assert client.get("/api/briefs?period=monthly").status_code == 422


def test_live_trends_compute_without_claude(pulse_app):
    _, brain, _ = pulse_app
    client, _ = client_for(VIEWER)

    data = client.get("/api/trends?days=7").json()

    assert brain.calls == []
    assert "shipping delay" in [t["term"] for t in data["terms"]]
    assert data["mentions"] == 4
    assert client.get("/api/trends?days=0").status_code == 422
    assert client.get("/api/trends?days=400").status_code == 422


def test_language_bank_search_filter_sort_and_pagination(pulse_app):
    client, _ = client_for(VIEWER)

    everything = client.get("/api/language-bank").json()
    assert everything["total"] == 5
    assert everything["items"][0]["phrase"] == "shipping delay again"  # sort=count default
    assert everything["items"][0]["count"] == 4

    found = client.get("/api/language-bank?q=NUMBER").json()
    assert found["total"] == 4 and all("number" in r["phrase"] for r in found["items"])

    page = client.get("/api/language-bank?q=number&limit=2&offset=2").json()
    assert page["total"] == 4 and len(page["items"]) == 2 and page["offset"] == 2

    assert client.get("/api/language-bank?drug=semaglutide").json()["total"] == 0
    assert client.get("/api/language-bank?drug=tirzepatide").json()["total"] == 5
    assert client.get("/api/language-bank?category=complaint").json()["total"] == 5
    recent = client.get("/api/language-bank?sort=recent").json()["items"]
    assert recent[0]["last_seen"] >= recent[-1]["last_seen"]
    assert client.get("/api/language-bank?sort=bogus").status_code == 422
    assert client.get("/api/language-bank?limit=0").status_code == 422


def test_pulse_tab_is_in_the_page_after_review_desk(pulse_app):
    client, _ = client_for(VIEWER)
    html = client.get("/").text
    assert html.index('data-tab="feed"') < html.index('data-tab="review"') < html.index('data-tab="pulse"')
    assert '<script src="/static/pulse.js"></script>' in html
    assert client.get("/static/pulse.js").status_code == 200


def test_pulse_script_follows_the_csp_rules():
    import re

    js = (dashboard.WEB_DIR / "pulse.js").read_text(encoding="utf-8")
    assert "style=" not in js
    assert not re.search(r"\son[a-z]+=", js)
    assert "escHtml" in js
    # model text is never inserted raw
    assert "innerHTML = brief." not in js and "innerHTML = b." not in js


def test_unused_store_helpers_are_consistent(pulse_app):
    state, _, _ = pulse_app
    page = run(pulse_store.search_language_bank(state, limit=1))
    assert page["limit"] == 1 and len(page["items"]) == 1
    assert briefs.PERIODS == ("daily", "weekly")
