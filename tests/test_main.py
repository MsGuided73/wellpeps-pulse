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
