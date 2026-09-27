"""Heartbeat skeleton: decision stub and quiet-hours helpers."""

from datetime import datetime

import pytest
import pytz

from harvey.config import PulseConfig
from harvey.main import decide_next_action, in_quiet_hours, seconds_until_quiet_hours_end


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
async def test_decide_next_action_is_idle_stub():
    action = await decide_next_action(None, None, PulseConfig(), summary={"mentions": {"new": 5}})

    assert action == "idle"


def test_quiet_hours_window_covering_whole_day_is_quiet(frozen_noon):
    config = PulseConfig(usage={"quiet_hours": {"start": "00:00", "end": "23:59", "timezone": "UTC"}})

    assert in_quiet_hours(config) is True


def test_seconds_until_quiet_hours_end_is_at_least_a_minute(frozen_noon):
    config = PulseConfig(usage={"quiet_hours": {"start": "00:00", "end": "23:59", "timezone": "UTC"}})

    assert seconds_until_quiet_hours_end(config) >= 60
