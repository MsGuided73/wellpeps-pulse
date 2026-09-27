"""Heartbeat: decision, quiet-hours gating, and quiet-hours helpers."""

from datetime import datetime

import pytest
import pytz

from harvey.config import PulseConfig
from harvey.main import (
    QUIET_HOURS_EXEMPT,
    apply_quiet_hours,
    decide_next_action,
    in_quiet_hours,
    seconds_until_quiet_hours_end,
)
from harvey.models import Mention, Platform
from harvey.state import StateManager


class _NoonUTC(datetime):
    """Pins "now" to midday so the 00:00-23:59 window can't flake at 23:59."""

    @classmethod
    def now(cls, tz=None):
        noon = datetime(2026, 9, 27, 12, 0, tzinfo=pytz.UTC)
        return noon.astimezone(tz) if tz else noon.replace(tzinfo=None)


@pytest.fixture
def frozen_noon(monkeypatch):
    monkeypatch.setattr("harvey.main.datetime", _NoonUTC)


@pytest.mark.asyncio
async def test_decide_next_action_triages_when_new_mentions_exist():
    action = await decide_next_action(None, None, PulseConfig(), summary={"mentions": {"new": 5}})

    assert action == "triage"


@pytest.mark.asyncio
async def test_decide_next_action_idles_without_new_mentions():
    summary = {"mentions": {"new": 0, "triaged": 3}}

    assert await decide_next_action(None, None, PulseConfig(), summary=summary) == "idle"


@pytest.mark.asyncio
async def test_decide_next_action_reads_state_when_no_summary(tmp_path):
    state = StateManager(str(tmp_path / "pulse.db"))
    await state.init_db()
    assert await decide_next_action(None, state, PulseConfig()) == "idle"

    await state.upsert_mention(Mention(platform=Platform.WEB, url="https://example.invalid/1", text="hi"))

    assert await decide_next_action(None, state, PulseConfig()) == "triage"


def test_triage_ignores_quiet_hours():
    assert "triage" in QUIET_HOURS_EXEMPT
    assert apply_quiet_hours("triage", quiet=True) == "triage"


def test_non_exempt_actions_idle_during_quiet_hours():
    assert apply_quiet_hours("draft", quiet=True) == "idle"
    assert apply_quiet_hours("draft", quiet=False) == "draft"


def test_quiet_hours_window_covering_whole_day_is_quiet(frozen_noon):
    config = PulseConfig(usage={"quiet_hours": {"start": "00:00", "end": "23:59", "timezone": "UTC"}})

    assert in_quiet_hours(config) is True


def test_seconds_until_quiet_hours_end_is_at_least_a_minute(frozen_noon):
    config = PulseConfig(usage={"quiet_hours": {"start": "00:00", "end": "23:59", "timezone": "UTC"}})

    assert seconds_until_quiet_hours_end(config) >= 60


# --- Phase 5: sweep every cycle, urgent tick -------------------------------------


def test_sleep_uses_heartbeat_without_open_escalations():
    from harvey.main import sleep_seconds

    config = PulseConfig(usage={"heartbeat_interval_minutes": 15, "urgent_tick_minutes": 5})

    assert sleep_seconds(config, open_escalations=0) == 15 * 60


def test_sleep_uses_urgent_tick_when_escalations_are_open():
    from harvey.main import sleep_seconds

    config = PulseConfig(usage={"heartbeat_interval_minutes": 15, "urgent_tick_minutes": 5})

    assert sleep_seconds(config, open_escalations=2) == 5 * 60


def test_urgent_tick_never_lengthens_the_heartbeat():
    from harvey.main import sleep_seconds

    config = PulseConfig(usage={"heartbeat_interval_minutes": 2, "urgent_tick_minutes": 5})

    assert sleep_seconds(config, open_escalations=1) == 2 * 60


def test_urgent_tick_defaults_to_five_minutes():
    assert PulseConfig().usage.urgent_tick_minutes == 5


@pytest.mark.asyncio
async def test_heartbeat_sweeps_escalations_even_in_quiet_hours(tmp_path, monkeypatch, frozen_noon):
    import asyncio

    import harvey.main as main_mod
    from harvey.escalation import SweepReport

    config = PulseConfig(usage={"quiet_hours": {"start": "00:00", "end": "23:59", "timezone": "UTC"}})
    db_path = str(tmp_path / "pulse.db")
    swept = []
    slept = []

    async def fake_sweep(state, notifier, cfg, now=None):
        swept.append(notifier)
        return SweepReport(open=1)

    async def fake_sleep(seconds, stop_event):
        slept.append(seconds)
        stop_event.set()
        return True

    from harvey.models import Escalation

    seeded = StateManager(db_path)
    await seeded.init_db()
    mid, _ = await seeded.upsert_mention(Mention(platform=Platform.WEB, url="https://example.invalid/e", text="x"))
    await seeded.set_mention_status(mid, "escalated")  # nothing to triage: no Claude calls
    await seeded.create_escalation(Escalation(mention_id=mid, kind="legal"))

    monkeypatch.setattr(main_mod, "load_config", lambda: config)
    monkeypatch.setattr(main_mod, "StateManager", lambda: StateManager(db_path))
    monkeypatch.setattr(main_mod, "sweep", fake_sweep)
    monkeypatch.setattr(main_mod.SlackNotifier, "from_config", classmethod(lambda cls, cfg: cls(None)))
    monkeypatch.setattr(main_mod, "_interruptible_sleep", fake_sleep)

    await main_mod.heartbeat(asyncio.Event())

    assert len(swept) == 1
    assert swept[0].enabled is False
    assert slept == [config.usage.urgent_tick_minutes * 60]
