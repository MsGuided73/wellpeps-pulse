"""scripts/seed_demo.py: deterministic DEMO DATA seeding (no network, no claude)."""

import asyncio
import importlib.util
from pathlib import Path

import pytest

from harvey.models import MentionStatus
from harvey.state import StateManager

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "seed_demo.py"


def _load():
    spec = importlib.util.spec_from_file_location("seed_demo", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seed_fills_every_dashboard_tab(tmp_path):
    seed_demo = _load()
    db = str(tmp_path / "demo.db")

    asyncio.run(seed_demo.seed(db))

    state = StateManager(db)
    summary = asyncio.run(state.get_state_summary())
    assert summary["mentions"][MentionStatus.IN_REVIEW.value] >= 1
    assert summary["mentions"][MentionStatus.ESCALATED.value] >= 1
    assert summary["open_escalations"] >= 1


def test_seed_refuses_without_a_throwaway_db(monkeypatch):
    seed_demo = _load()
    monkeypatch.delenv("PULSE_DB_PATH", raising=False)
    with pytest.raises(SystemExit):
        seed_demo._check_target()


def test_seed_builds_trend_history_briefs_and_language_bank(tmp_path):
    from harvey import pulse_store

    seed_demo = _load()
    db = str(tmp_path / "demo.db")

    asyncio.run(seed_demo.seed(db))

    state = StateManager(db)
    daily = asyncio.run(pulse_store.list_briefs(state, period="daily"))
    weekly = asyncio.run(pulse_store.list_briefs(state, period="weekly"))
    assert len(daily) == 1 and len(weekly) == 1
    brief = asyncio.run(pulse_store.get_brief(state, weekly[0]["id"]))
    assert brief["headline"].startswith("DEMO")
    assert brief["status"] == "ok" and len(brief["action_cards"]) >= 3
    terms = [t["term"] for t in asyncio.run(pulse_store.list_trend_terms(state, brief["id"]))]
    spikes = {"shipping delay", "price hike", "compounded tirz shortage", "oral wegovy"}
    assert spikes & set(terms)
    bank = asyncio.run(pulse_store.search_language_bank(state))
    assert bank["total"] >= 5
    rows = asyncio.run(pulse_store.fetch_rows(state, brief["window_start"], brief["window_end"]))
    assert rows, "the weekly window has DEMO mentions"
    for row in asyncio.run(pulse_store.list_briefs(state)):
        stored = asyncio.run(pulse_store.get_brief(state, row["id"]))
        assert stored["data"]["verification"]["stripped"] == []  # the demo writer cites real numbers


def test_seed_spreads_enough_for_the_analytics_charts(tmp_path):
    from harvey import analytics

    seed_demo = _load()
    db = str(tmp_path / "demo.db")

    asyncio.run(seed_demo.seed(db))

    state = StateManager(db)
    p = analytics.parse_params(days="35", tz="America/New_York")
    rows = asyncio.run(analytics.fetch_mentions(state, p, p.start_utc, p.end_utc))
    assert len({r["competitor"] for r in rows if r["competitor"]}) >= 5
    assert len({round(r["sentiment_score"], 2) for r in rows}) >= 20  # not three flat values
    assert len({analytics.local_day(p, r["at"]) for r in rows}) >= 30
    stats = asyncio.run(analytics.chart(state, "escalations", p))
    assert stats["acked"] >= 5 and stats["median_ack_minutes"] is not None
    assert 0 < stats["breached_pct"] < 100
