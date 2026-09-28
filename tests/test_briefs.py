"""Pulse briefs (Phase 8): window math, idempotency, fallback, numeric
verification, prompt privacy, and the PHI-safe Slack message."""

import json
import logging
from datetime import datetime, timedelta

import pytest

from harvey import briefs, pulse_store
from harvey.config import PulseConfig
from tests.dashboard_helpers import FakeNotifier
from tests.pulse_helpers import FakeBrain, add_triaged, fresh_state, good_answer

NOW = datetime(2026, 9, 27, 12, 0)          # naive UTC; 08:00 in New York (Sunday)
DAY_START = datetime(2026, 9, 26, 4, 0)      # previous local day, in UTC
DAY_END = datetime(2026, 9, 27, 4, 0)
IN_WINDOW = datetime(2026, 9, 26, 12, 0)


def _config(**notify) -> PulseConfig:
    return PulseConfig(notify=notify or {})


# --- Window math -----------------------------------------------------------------------------


def test_daily_window_is_the_previous_local_day():
    assert briefs.window_for("daily", NOW, "America/New_York") == (DAY_START, DAY_END)


def test_daily_window_uses_the_local_date_not_the_utc_date():
    late_evening = datetime(2026, 9, 28, 2, 0)  # Sep 27 22:00 in New York
    assert briefs.window_for("daily", late_evening, "America/New_York") == (DAY_START, DAY_END)


def test_weekly_window_is_the_previous_monday_to_sunday():
    assert briefs.window_for("weekly", NOW, "America/New_York") == (
        datetime(2026, 9, 14, 4, 0), datetime(2026, 9, 21, 4, 0))


def test_weekly_window_on_a_monday_is_the_week_just_ended():
    monday = datetime(2026, 9, 21, 15, 0)
    assert briefs.window_for("weekly", monday, "America/New_York") == (
        datetime(2026, 9, 14, 4, 0), datetime(2026, 9, 21, 4, 0))


def test_weekly_window_across_dst_end_is_169_hours():
    start, end = briefs.window_for("weekly", datetime(2026, 11, 3, 12, 0), "America/New_York")
    assert start == datetime(2026, 10, 26, 4, 0)
    assert end == datetime(2026, 11, 2, 5, 0)


def test_window_rejects_unknown_period():
    with pytest.raises(ValueError):
        briefs.window_for("monthly", NOW, "UTC")


# --- Numeric verification -------------------------------------------------------------------------


def test_numbers_in_parses_decimals_percents_and_thousands():
    assert briefs.numbers_in("up 42.9% from 1,250 posts, -0.12 shift, 3x") == {42.9, 1250.0, 0.12, 3.0}


def test_verify_cards_strips_cards_with_invented_numbers():
    payload = {"emerging_terms": [{"term": "shipping delay", "count": 3, "velocity": 2.5}]}
    cards = [{"title": "ok", "why": "3 mentions, velocity 2.5"},
             {"title": "bad", "why": "up 999% overnight"},
             {"title": "plain", "why": "no numbers here"}]

    kept, stripped = briefs.verify_cards(cards, payload)

    assert [c["title"] for c in kept] == ["ok", "plain"]
    assert [c["title"] for c in stripped] == ["bad"]


# --- build_brief ----------------------------------------------------------------------------------


async def _seed_window(state, n=3, **extra):
    for i in range(n):
        await add_triaged(state, f"s{i}", text="another shipping delay from them this week",
                          posted_at=IN_WINDOW + timedelta(minutes=i), competitor="Hims & Hers",
                          subject_type="competitor", category="complaint", sentiment_score=-0.5, **extra)


@pytest.mark.asyncio
async def test_build_brief_stores_brief_and_trend_terms(tmp_path):
    state = await fresh_state(tmp_path)
    await _seed_window(state)
    brain = FakeBrain([good_answer()])

    brief = await briefs.build_brief(state, brain, "daily", config=_config(), now=NOW)

    assert brief["created"] is True
    assert brief["period"] == "daily" and brief["status"] == "ok"
    assert brief["window_start"].startswith("2026-09-26T04:00")
    assert brief["headline"] == good_answer()["headline"]
    assert len(brief["action_cards"]) == 3
    assert brain.calls == [("pulse", "brief")]
    terms = await pulse_store.list_trend_terms(state, brief["id"])
    assert "shipping delay" in [t["term"] for t in terms]
    assert brief["data"]["mentions"] == 3


@pytest.mark.asyncio
async def test_build_brief_is_idempotent_per_window_unless_forced(tmp_path):
    state = await fresh_state(tmp_path)
    await _seed_window(state)
    brain = FakeBrain([good_answer(), good_answer(headline="Second take")])

    first = await briefs.build_brief(state, brain, "daily", config=_config(), now=NOW)
    again = await briefs.build_brief(state, brain, "daily", config=_config(), now=NOW + timedelta(hours=3))

    assert again["id"] == first["id"] and again["created"] is False
    assert len(brain.calls) == 1

    forced = await briefs.build_brief(state, brain, "daily", config=_config(), now=NOW, force=True)

    assert forced["id"] == first["id"] and forced["headline"] == "Second take"
    assert len(brain.calls) == 2
    assert len(await pulse_store.list_briefs(state, period="daily")) == 1
    terms = await pulse_store.list_trend_terms(state, first["id"])
    assert len(terms) == len({t["term"] for t in terms})  # replaced, not duplicated


@pytest.mark.asyncio
async def test_bad_json_gets_one_retry_then_a_fallback_brief(tmp_path):
    state = await fresh_state(tmp_path)
    await _seed_window(state)
    brain = FakeBrain(["not json", {"headline": "missing everything else"}])

    brief = await briefs.build_brief(state, brain, "daily", config=_config(), now=NOW)

    assert len(brain.calls) == 2
    assert brief["status"] == "fallback"
    assert brief["headline"] == briefs.FALLBACK_HEADLINE
    assert brief["action_cards"] == []
    assert "shipping delay" in brief["summary_md"]


@pytest.mark.asyncio
async def test_retry_succeeds_on_second_answer(tmp_path):
    state = await fresh_state(tmp_path)
    await _seed_window(state)
    brain = FakeBrain([None, good_answer()])

    brief = await briefs.build_brief(state, brain, "daily", config=_config(), now=NOW)

    assert brief["status"] == "ok" and len(brain.calls) == 2


@pytest.mark.asyncio
async def test_brain_exception_falls_back(tmp_path):
    state = await fresh_state(tmp_path)
    await _seed_window(state)

    class Boom(FakeBrain):
        async def think_json(self, *a, **k):
            raise RuntimeError("cli died")

    brief = await briefs.build_brief(state, Boom([]), "daily", config=_config(), now=NOW)

    assert brief["status"] == "fallback"


@pytest.mark.asyncio
async def test_invented_numbers_are_stripped_and_logged(tmp_path, caplog):
    state = await fresh_state(tmp_path)
    await _seed_window(state)
    answer = good_answer()
    answer["action_cards"][0]["why"] = "shipping delay hit 3 mentions in the window"
    answer["action_cards"][1]["why"] = "complaints are up 999% overnight"
    brain = FakeBrain([answer])

    with caplog.at_level(logging.WARNING, logger="harvey.briefs"):
        brief = await briefs.build_brief(state, brain, "daily", config=_config(), now=NOW)

    titles = [c["title"] for c in brief["action_cards"]]
    assert "Review price-change messaging" not in titles
    assert "Refresh the shipping FAQ" in titles
    assert brief["data"]["verification"]["stripped"] == ["Review price-change messaging"]
    assert any("999" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_prompt_carries_aggregates_only(tmp_path):
    state = await fresh_state(tmp_path)
    await _seed_window(state)
    await add_triaged(state, "leak", title="ZQXTITLEMARK headline",
                      text="The zqxsentencemark fox told everyone about her refill",
                      author="@zqxhandlemark", url="https://www.reddit.com/r/test/comments/zqxurlmark/",
                      posted_at=IN_WINDOW, subject_type="wellpeps", category="complaint")
    brain = FakeBrain([good_answer()])

    await briefs.build_brief(state, brain, "daily", config=_config(), now=NOW)

    prompt = brain.prompts[0].lower()
    for marker in ("zqxtitlemark", "zqxsentencemark", "zqxhandlemark", "zqxurlmark", "reddit.com", "http"):
        assert marker not in prompt
    assert "shipping delay" in prompt  # the aggregates are there
    assert "hims & hers" in prompt


@pytest.mark.asyncio
async def test_prompt_rules_are_present():
    prompt = briefs.build_prompt({"emerging_terms": []})
    for rule in ("no medical claims", "individual", "disparag", "compliance", "invent"):
        assert rule in prompt.lower()
    assert "{{" not in prompt


@pytest.mark.asyncio
async def test_slack_message_is_headline_titles_and_link_only(tmp_path):
    state = await fresh_state(tmp_path)
    await _seed_window(state, phrases=["zqxphrase marker words"])
    for i in range(3):
        await add_triaged(state, f"m{i}", text=f"zqxtextmark body {i} zqxphrase marker words",
                          author=f"@zqxauthor{i}", posted_at=IN_WINDOW, drug="semaglutide",
                          url=f"https://www.reddit.com/r/test/comments/zqxurl{i}/",
                          phrases=["zqxphrase marker words"])
    answer = good_answer(headline="Buzz: ZQXPHRASE marker words is spreading")
    answer["action_cards"][0]["title"] = "Answer @zqxauthor0 at https://evil.example/x"
    answer["action_cards"].append({
        "title": "Fourth card zqxfourth", "why": "none", "action": "none", "owner_hint": "product",
        "urgency": "watch", "evidence_terms": []})
    notifier = FakeNotifier()
    config = _config(dashboard_url="https://pulse.example.test/")

    brief = await briefs.build_brief(state, FakeBrain([answer]), "daily", config=config, now=NOW,
                                     notifier=notifier)

    assert len(notifier.sent) == 1
    text = notifier.sent[0].lower()
    for marker in ("zqxphrase", "zqxtextmark", "zqxauthor", "zqxurl", "evil.example", "zqxfourth"):
        assert marker not in text
    assert "buzz" in text and "is spreading" in text
    assert "review price-change messaging" in text
    assert f"https://pulse.example.test/#pulse-brief-{brief['id']}" in text
    stored = await pulse_store.get_brief(state, brief["id"])
    assert stored["slack_sent_at"]


@pytest.mark.asyncio
async def test_slack_not_marked_sent_when_send_fails(tmp_path):
    state = await fresh_state(tmp_path)
    await _seed_window(state)

    brief = await briefs.build_brief(state, FakeBrain([good_answer()]), "daily", config=_config(),
                                     now=NOW, notifier=FakeNotifier(ok=False))

    assert (await pulse_store.get_brief(state, brief["id"]))["slack_sent_at"] is None


@pytest.mark.asyncio
async def test_payload_json_has_no_example_ids_or_links(tmp_path):
    state = await fresh_state(tmp_path)
    await _seed_window(state)
    from harvey import trends

    report = await trends.compute_trends(state, DAY_START, DAY_END, min_count=1)
    payload = briefs.build_payload(report, [], "daily")

    raw = json.dumps(payload)
    assert "example" not in raw and "http" not in raw
    assert payload["emerging_terms"][0]["term"]
