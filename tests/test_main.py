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
    action = await decide_next_action(None, PulseConfig(), summary={"mentions": {"new": 5}})

    assert action == "triage"


@pytest.mark.asyncio
async def test_decide_next_action_idles_without_new_mentions():
    summary = {"mentions": {"new": 0, "triaged": 3}}

    assert await decide_next_action(None, PulseConfig(), summary=summary) == "idle"


@pytest.mark.asyncio
async def test_decide_next_action_reads_state_when_no_summary(tmp_path):
    state = StateManager(str(tmp_path / "pulse.db"))
    await state.init_db()
    assert await decide_next_action(state, PulseConfig()) == "idle"

    await state.upsert_mention(Mention(platform=Platform.WEB, url="https://example.invalid/1", text="hi"))

    assert await decide_next_action(state, PulseConfig()) == "triage"


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


def test_decide_next_action_takes_no_brain():
    import inspect

    assert "brain" not in inspect.signature(decide_next_action).parameters


# --- Phase 8: Pulse briefs in the heartbeat -------------------------------------------------


@pytest.mark.asyncio
async def test_priority_is_triage_then_draft_then_brief_then_idle():
    cfg = PulseConfig()
    base = {"mentions": {"new": 0}, "draftable": 0, "briefs_due": []}

    assert await decide_next_action(None, cfg, summary={
        **base, "mentions": {"new": 1}, "draftable": 2, "briefs_due": ["daily"]}) == "triage"
    assert await decide_next_action(None, cfg, summary={
        **base, "draftable": 2, "briefs_due": ["daily"]}) == "draft"
    assert await decide_next_action(None, cfg, summary={**base, "briefs_due": ["daily"]}) == "brief"
    assert await decide_next_action(None, cfg, summary=base) == "idle"


def test_brief_waits_out_quiet_hours():
    assert "brief" not in QUIET_HOURS_EXEMPT
    assert apply_quiet_hours("brief", quiet=True) == "idle"
    assert apply_quiet_hours("brief", quiet=False) == "brief"


def test_brief_action_runs_the_brief_task():
    from harvey.main import _tasks_for

    async def runner():
        return []

    tasks = _tasks_for("brief", brief_runner=runner)

    assert [name for name, _ in tasks] == ["brief"]
    tasks[0][1].close()


def _pulse_config() -> PulseConfig:
    return PulseConfig(usage={"quiet_hours": {"start": "22:00", "end": "07:00",
                                              "timezone": "America/New_York"}},
                       pulse={"daily_brief_hour": 7, "weekly_day": "monday"})


async def _state_with_sunday_mention(tmp_path):
    from harvey.models import MentionStatus
    from tests.pulse_helpers import add_triaged

    state = StateManager(str(tmp_path / "pulse.db"))
    await state.init_db()
    await add_triaged(state, "sun", text="shipping delay again", posted_at=datetime(2026, 9, 27, 12, 0),
                      status=MentionStatus.TRIAGED)
    return state


@pytest.mark.asyncio
async def test_daily_brief_is_due_only_after_the_configured_hour(tmp_path):
    from harvey.briefs import due_periods

    state = await _state_with_sunday_mention(tmp_path)
    monday_0630_local = datetime(2026, 9, 28, 10, 30)
    monday_0800_local = datetime(2026, 9, 28, 12, 0)

    assert await due_periods(state, _pulse_config(), now=monday_0630_local) == []
    assert await due_periods(state, _pulse_config(), now=monday_0800_local) == ["daily"]


@pytest.mark.asyncio
async def test_weekly_brief_follows_the_daily_on_the_weekly_day(tmp_path):
    from harvey.briefs import build_brief, due_periods
    from tests.pulse_helpers import FakeBrain, good_answer

    state = await _state_with_sunday_mention(tmp_path)
    now = datetime(2026, 9, 28, 12, 0)
    config = _pulse_config()

    await build_brief(state, FakeBrain([good_answer()]), "daily", config=config, now=now)
    assert await due_periods(state, config, now=now) == ["weekly"]

    await build_brief(state, FakeBrain([good_answer()]), "weekly", config=config, now=now)
    assert await due_periods(state, config, now=now) == []


@pytest.mark.asyncio
async def test_no_brief_is_due_for_an_empty_window(tmp_path):
    from harvey.briefs import due_periods

    state = StateManager(str(tmp_path / "pulse.db"))
    await state.init_db()

    assert await due_periods(state, _pulse_config(), now=datetime(2026, 9, 28, 12, 0)) == []


@pytest.mark.asyncio
async def test_due_briefs_skip_when_over_budget(tmp_path):
    from harvey.briefs import run_due_briefs
    from tests.pulse_helpers import FakeBrain, good_answer

    state = await _state_with_sunday_mention(tmp_path)
    brain = FakeBrain([good_answer()])

    built = await run_due_briefs(state, brain, _pulse_config(), budget_ok=lambda: False,
                                 now=datetime(2026, 9, 28, 12, 0))

    assert built == [] and brain.calls == []

    built = await run_due_briefs(state, brain, _pulse_config(), budget_ok=lambda: True,
                                 now=datetime(2026, 9, 28, 12, 0))

    assert [b["period"] for b in built] == ["daily"] and len(brain.calls) == 1


def test_pulse_config_defaults():
    pulse = PulseConfig().pulse
    assert (pulse.daily_brief_hour, pulse.weekly_day, pulse.top_terms, pulse.baseline_days,
            pulse.min_count) == (7, "monday", 25, 28, 3)


def test_pulse_config_rejects_bad_weekday():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PulseConfig(pulse={"weekly_day": "funday"})


def test_pulse_min_count_never_allows_single_post_terms():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PulseConfig(pulse={"min_count": 1})
