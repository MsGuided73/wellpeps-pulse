"""#pulse-query bot event handling with a fake Slack client (no network):
wrong channel, DMs, bots, rate limits, help, action requests, answers in
thread, audit rows, token validation, the disabled idle mode, and tokens
never reaching a log."""

import asyncio
import json
import logging
from datetime import timedelta

import pytest
import pytest_asyncio

from harvey import health
from harvey.config import ConfigError, PulseConfig
from harvey.slackbot import app as app_mod
from harvey.slackbot import audit
from tests.pulse_helpers import fresh_state
from tests.slackbot_helpers import (
    APP_TOKEN, BOT_TOKEN, CHANNEL, NOW, OTHER_CHANNEL, FakeSlackClient, ScriptedBrain, mention_event, seed_week,
)

DASH = "https://pulse.example.com"
PLAN = {"intent": "volume", "days": 7, "competitors": [], "drugs": [], "platforms": [], "categories": [],
        "term": ""}


def _config(**limits) -> PulseConfig:
    return PulseConfig(notify={"dashboard_url": DASH}, slack_query=limits or {})


@pytest_asyncio.fixture
async def state(tmp_path):
    state = await fresh_state(tmp_path)
    await seed_week(state)
    return state


def _bot(state, brain=None, config=None, clock=lambda: NOW):
    return app_mod.QueryBot(state, brain or ScriptedBrain(), config or _config(), CHANNEL, clock=clock)


@pytest.mark.asyncio
async def test_answers_in_thread_with_reaction_and_audit(state):
    brain = ScriptedBrain(plans=[PLAN], answers=["*10* relevant mentions this week, up from 2."])
    client = FakeSlackClient()

    outcome = await _bot(state, brain).handle_mention(mention_event(), client)

    assert outcome == "answered"
    [post] = client.posts
    assert post["channel"] == CHANNEL and post["thread_ts"] == "1727539200.000100"
    assert post["text"].startswith("*10* relevant mentions this week, up from 2.")
    assert post["text"].endswith(f"<{DASH}/#analytics?days=7|Open in Pulse dashboard>")
    assert post["unfurl_links"] is False
    assert [kind for kind, _ in client.reactions] == ["add", "remove"]
    assert client.reactions[0][1]["name"] == app_mod.REACTION
    assert [(a, t) for a, t, _ in brain.prompts] == [("slackbot", "plan"), ("slackbot", "answer")]

    [row] = await audit.recent(state)
    assert row["action_type"] == audit.ACTION_TYPE and row["agent"] == "slackbot:U0STAFF1"
    d = row["details"]
    assert d["slack_user"] == "U0STAFF1" and d["channel"] == CHANNEL
    assert d["intent"] == "volume" and d["days"] == 7 and d["outcome"] == "answered"
    assert d["answer_chars"] == len(post["text"]) and d["models"] == {"plan": "haiku", "answer": "sonnet"}
    assert d["blocked"] == "" and d["limited"] is False and isinstance(d["latency_ms"], int)
    assert d["question"] == "what's trending this week?"


@pytest.mark.asyncio
async def test_answer_text_never_carries_mention_text(state):
    brain = ScriptedBrain(plans=[{**PLAN, "intent": "search_count", "term": "price"}],
                          answers=['Users wrote "price hike on compounded semaglutide, contact me at a@b.example" '
                                   "and @bob agreed. See https://reddit.com/x."])
    client = FakeSlackClient()
    await _bot(state, brain).handle_mention(mention_event("<@UBOT> how many mentions of price?"), client)
    text = client.posts[0]["text"]
    for leak in ("contact me", "a@b.example", "@bob", "reddit.com"):
        assert leak not in text
    answer_prompt = brain.prompts[1][2]
    assert "contact me" not in answer_prompt and "a@b.example" not in answer_prompt


@pytest.mark.asyncio
async def test_wrong_channel_gets_one_brief_reply_and_no_data(state):
    brain = ScriptedBrain(plans=[PLAN], answers=["data"])
    client = FakeSlackClient()
    outcome = await _bot(state, brain).handle_mention(mention_event(channel=OTHER_CHANNEL), client)
    assert outcome == "wrong_channel"
    assert [p["text"] for p in client.posts] == [f"I only answer in <#{CHANNEL}>."]
    assert client.posts[0]["channel"] == OTHER_CHANNEL
    assert brain.prompts == [] and client.reactions == []
    [row] = await audit.recent(state)
    assert row["action_type"] == audit.REFUSED_TYPE and row["details"]["blocked"] == "wrong_channel"


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{"channel_type": "im", "channel": "D0DIRECT"}, {"channel": "D0DIRECT"}])
async def test_dms_are_ignored(state, extra):
    client = FakeSlackClient()
    outcome = await _bot(state).handle_mention(mention_event(**extra), client)
    assert outcome == "ignored_dm" and client.posts == []
    assert await audit.recent(state) == []


@pytest.mark.asyncio
async def test_bot_messages_and_redeliveries_are_ignored(state):
    client = FakeSlackClient()
    bot = _bot(state, ScriptedBrain(plans=[PLAN], answers=["ok"]))
    assert await bot.handle_mention(mention_event(bot_id="B1"), client) == "ignored_bot"
    assert await bot.handle_mention(mention_event(subtype="message_changed"), client) == "ignored_bot"
    assert await bot.handle_mention(mention_event(), client) == "answered"
    assert await bot.handle_mention(mention_event(), client) == "ignored_duplicate"
    assert len(client.posts) == 1


@pytest.mark.asyncio
async def test_thread_reply_mention_answers_in_that_thread(state):
    client = FakeSlackClient()
    bot = _bot(state, ScriptedBrain(plans=[PLAN], answers=["ok then"]))
    await bot.handle_mention(mention_event(ts="1727539300.000200", thread_ts="1727539200.000100"), client)
    assert client.posts[0]["thread_ts"] == "1727539200.000100"


@pytest.mark.asyncio
async def test_help_for_help_and_for_unplannable_questions(state):
    client = FakeSlackClient()
    bot = _bot(state, ScriptedBrain(plans=[{"intent": "nonsense"}]))
    assert await bot.handle_mention(mention_event("<@UBOT> help", ts="1.1"), client) == "help"
    assert await bot.handle_mention(mention_event("<@UBOT> tell me a joke", ts="1.2"), client) == "help"
    for post in client.posts:
        assert "what's trending this week?" in post["text"] and f"<{DASH}/|Open in Pulse dashboard>" in post["text"]
    rows = await audit.recent(state)
    assert {r["details"]["blocked"] for r in rows} == {"", "invalid_plan"}
    assert all(r["details"]["intent"] == "help" for r in rows)


@pytest.mark.asyncio
async def test_action_requests_point_to_the_dashboard(state):
    brain = ScriptedBrain()
    client = FakeSlackClient()
    outcome = await _bot(state, brain).handle_mention(mention_event("<@UBOT> approve the reply for 42"), client)
    assert outcome == "action_refused" and brain.prompts == []
    assert "read-only" in client.posts[0]["text"] and f"<{DASH}/|in the Pulse dashboard>" in client.posts[0]["text"]
    [row] = await audit.recent(state)
    assert row["details"]["blocked"] == "read_only" and row["details"]["intent"] == "action"


@pytest.mark.asyncio
async def test_per_user_hourly_limit(state):
    config = _config(per_user_per_hour=2, daily_limit=100)
    brain = ScriptedBrain(plans=[PLAN] * 5, answers=["ok"] * 5)
    client = FakeSlackClient()
    bot = _bot(state, brain, config)
    outcomes = [await bot.handle_mention(mention_event(ts=f"2.{i}"), client) for i in range(3)]
    assert outcomes == ["answered", "answered", "limited"]
    assert "hourly question limit" in client.posts[-1]["text"]
    # another user is unaffected; refusals do not count
    assert await bot.handle_mention(mention_event(ts="2.9", user="U0OTHER"), client) == "answered"
    limited = [r for r in await audit.recent(state) if r["action_type"] == audit.REFUSED_TYPE]
    assert len(limited) == 1 and limited[0]["details"]["limited"] is True
    assert limited[0]["details"]["blocked"] == "user_hourly_limit"


@pytest.mark.asyncio
async def test_hourly_window_rolls(state):
    config = _config(per_user_per_hour=1)
    times = iter([NOW, NOW + timedelta(minutes=30), NOW + timedelta(minutes=61)])
    brain = ScriptedBrain(plans=[PLAN] * 3, answers=["ok"] * 3)
    bot = _bot(state, brain, config, clock=lambda: next(times))
    client = FakeSlackClient()
    outcomes = [await bot.handle_mention(mention_event(ts=f"3.{i}"), client) for i in range(3)]
    assert outcomes == ["answered", "limited", "answered"]


@pytest.mark.asyncio
async def test_daily_limit_across_users(state):
    config = _config(daily_limit=2, per_user_per_hour=20)
    brain = ScriptedBrain(plans=[PLAN] * 3, answers=["ok"] * 3)
    bot = _bot(state, brain, config)
    client = FakeSlackClient()
    outcomes = [await bot.handle_mention(mention_event(ts=f"4.{i}", user=f"U{i}"), client) for i in range(3)]
    assert outcomes == ["answered", "answered", "limited"]
    assert "today's question limit" in client.posts[-1]["text"]


@pytest.mark.asyncio
async def test_failures_reply_politely_and_still_audit(state, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("database exploded with secret detail")

    monkeypatch.setattr(app_mod.executor, "execute", boom)
    client = FakeSlackClient(fail_reactions=True)
    outcome = await _bot(state, ScriptedBrain(plans=[PLAN])).handle_mention(mention_event(), client)
    assert outcome == "error"
    assert client.posts[0]["text"] == app_mod.ERROR_TEXT
    [row] = await audit.recent(state)
    assert row["details"]["outcome"] == "error"


@pytest.mark.asyncio
async def test_audit_question_is_truncated_and_stripped(state):
    client = FakeSlackClient()
    question = "<@UBOT> volume for jane@x.example see https://x.example/y " + "word " * 100
    await _bot(state, ScriptedBrain(plans=[PLAN], answers=["ok"])).handle_mention(mention_event(question), client)
    [row] = await audit.recent(state)
    q = row["details"]["question"]
    assert len(q) <= 200 and "jane@x.example" not in q and "https://" not in q


# ── Settings, tokens, process ──


def test_read_settings_disabled_when_no_tokens():
    assert app_mod.read_settings({}) is None
    assert app_mod.read_settings({"SLACK_QUERY_CHANNEL_ID": CHANNEL}) is None


@pytest.mark.parametrize("env,needle", [
    ({"SLACK_BOT_TOKEN": BOT_TOKEN}, "SLACK_APP_TOKEN"),
    ({"SLACK_APP_TOKEN": APP_TOKEN}, "SLACK_BOT_TOKEN"),
    ({"SLACK_BOT_TOKEN": APP_TOKEN, "SLACK_APP_TOKEN": APP_TOKEN, "SLACK_QUERY_CHANNEL_ID": CHANNEL}, "xoxb-"),
    ({"SLACK_BOT_TOKEN": BOT_TOKEN, "SLACK_APP_TOKEN": BOT_TOKEN, "SLACK_QUERY_CHANNEL_ID": CHANNEL}, "xapp-"),
    ({"SLACK_BOT_TOKEN": BOT_TOKEN, "SLACK_APP_TOKEN": APP_TOKEN}, "SLACK_QUERY_CHANNEL_ID"),
    ({"SLACK_BOT_TOKEN": BOT_TOKEN, "SLACK_APP_TOKEN": APP_TOKEN, "SLACK_QUERY_CHANNEL_ID": "#pulse-query"},
     "SLACK_QUERY_CHANNEL_ID"),
])
def test_read_settings_rejects_bad_setups_without_echoing_tokens(env, needle):
    with pytest.raises(ConfigError) as exc:
        app_mod.read_settings(env)
    assert needle in str(exc.value)
    assert "SECRET" not in str(exc.value)


def test_read_settings_ok():
    env = {"SLACK_BOT_TOKEN": BOT_TOKEN, "SLACK_APP_TOKEN": APP_TOKEN, "SLACK_QUERY_CHANNEL_ID": CHANNEL}
    assert app_mod.read_settings(env) == (BOT_TOKEN, APP_TOKEN, CHANNEL)


@pytest.mark.asyncio
async def test_disabled_bot_idles_with_a_heartbeat(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    state = await fresh_state(tmp_path)
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.05)
        stop.set()

    asyncio.get_running_loop().create_task(stopper())
    outcome = await app_mod.run(PulseConfig(), stop_event=stop, environ={}, state=state, interval=0.01)
    assert outcome == "disabled"
    assert app_mod.DISABLED_MESSAGE in caplog.text
    ok, _ = await health.slackbot_health(state)
    assert ok


class _FakeHandler:
    instances: list = []

    def __init__(self, app, token):
        self.app, self.token = app, token
        self.connected = self.closed = False
        _FakeHandler.instances.append(self)

    async def connect_async(self):
        self.connected = True

    async def close_async(self):
        self.closed = True


@pytest.mark.asyncio
async def test_enabled_bot_connects_socket_mode_and_never_logs_tokens(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    state = await fresh_state(tmp_path)
    stop = asyncio.Event()
    env = {"SLACK_BOT_TOKEN": BOT_TOKEN, "SLACK_APP_TOKEN": APP_TOKEN, "SLACK_QUERY_CHANNEL_ID": CHANNEL}

    async def stopper():
        await asyncio.sleep(0.05)
        stop.set()

    asyncio.get_running_loop().create_task(stopper())
    _FakeHandler.instances.clear()
    outcome = await app_mod.run(_config(), stop_event=stop, environ=env, state=state,
                                brain=ScriptedBrain(), interval=0.01, handler_factory=_FakeHandler)
    assert outcome == "stopped"
    [handler] = _FakeHandler.instances
    assert handler.connected and handler.closed and handler.token == APP_TOKEN

    # A token that lands in any log record is scrubbed.
    logging.getLogger("slack_sdk.web.async_client").warning(f"Authorization: Bearer {BOT_TOKEN}")
    logging.getLogger("harvey.anything").error(f"app token {APP_TOKEN}")
    assert "SECRETBOTTOKEN" not in caplog.text and "SECRETAPPTOKEN" not in caplog.text


async def _run_briefly(state, env, handler_factory, seconds=0.08, **kwargs):
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(seconds)
        stop.set()

    asyncio.get_running_loop().create_task(stopper())
    return await app_mod.run(_config(), stop_event=stop, environ=env, state=state,
                             brain=ScriptedBrain(), interval=0.01,
                             handler_factory=handler_factory, **kwargs)


async def _heartbeat_stamp(state):
    from harvey.health import SLACKBOT_HEARTBEAT_KEY

    return await state.get_setting(SLACKBOT_HEARTBEAT_KEY, "")


@pytest.mark.asyncio
@pytest.mark.parametrize("env", [
    {"SLACK_BOT_TOKEN": BOT_TOKEN},                                  # one token only
    {"SLACK_BOT_TOKEN": "nope", "SLACK_APP_TOKEN": APP_TOKEN,
     "SLACK_QUERY_CHANNEL_ID": CHANNEL},                             # wrong prefix
    {"SLACK_BOT_TOKEN": BOT_TOKEN, "SLACK_APP_TOKEN": APP_TOKEN,
     "SLACK_QUERY_CHANNEL_ID": "not-a-channel"},                     # bad channel id
])
async def test_misconfigured_settings_idle_instead_of_crashing(tmp_path, caplog, env):
    caplog.set_level(logging.WARNING)
    state = await fresh_state(tmp_path)
    _FakeHandler.instances.clear()

    outcome = await _run_briefly(state, env, _FakeHandler)

    assert outcome == "misconfigured"
    assert _FakeHandler.instances == []                 # never tried to connect
    assert await _heartbeat_stamp(state)                # still healthy for Docker
    assert "Slack query bot disabled (misconfigured)" in caplog.text
    assert "SECRETBOTTOKEN" not in caplog.text and "SECRETAPPTOKEN" not in caplog.text


class _RejectingHandler(_FakeHandler):
    async def connect_async(self):
        from slack_sdk.errors import SlackApiError

        raise SlackApiError("invalid_auth", {"ok": False, "error": "invalid_auth"})


@pytest.mark.asyncio
async def test_rejected_tokens_idle_instead_of_crashing(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    state = await fresh_state(tmp_path)
    env = {"SLACK_BOT_TOKEN": BOT_TOKEN, "SLACK_APP_TOKEN": APP_TOKEN, "SLACK_QUERY_CHANNEL_ID": CHANNEL}

    outcome = await _run_briefly(state, env, _RejectingHandler)

    assert outcome == "auth_failed"
    assert await _heartbeat_stamp(state)
    assert "Slack rejected the tokens (invalid_auth)" in caplog.text
    assert "SECRETBOTTOKEN" not in caplog.text and "SECRETAPPTOKEN" not in caplog.text


class _FlakyHandler(_FakeHandler):
    failures_left = 2

    async def connect_async(self):
        if _FlakyHandler.failures_left > 0:
            _FlakyHandler.failures_left -= 1
            raise OSError("network unreachable")
        self.connected = True


@pytest.mark.asyncio
async def test_transient_connect_errors_retry_then_connect(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    state = await fresh_state(tmp_path)
    env = {"SLACK_BOT_TOKEN": BOT_TOKEN, "SLACK_APP_TOKEN": APP_TOKEN, "SLACK_QUERY_CHANNEL_ID": CHANNEL}
    _FlakyHandler.failures_left = 2
    _FakeHandler.instances.clear()

    outcome = await _run_briefly(state, env, _FlakyHandler, seconds=0.3, retry_base_seconds=0.01)

    assert outcome == "stopped"
    assert any(h.connected for h in _FakeHandler.instances)
    assert await _heartbeat_stamp(state)
    assert "retrying in" in caplog.text


@pytest.mark.asyncio
async def test_stop_during_connect_retries_exits_cleanly(tmp_path):
    state = await fresh_state(tmp_path)
    env = {"SLACK_BOT_TOKEN": BOT_TOKEN, "SLACK_APP_TOKEN": APP_TOKEN, "SLACK_QUERY_CHANNEL_ID": CHANNEL}
    _FlakyHandler.failures_left = 10_000

    outcome = await _run_briefly(state, env, _FlakyHandler, seconds=0.05, retry_base_seconds=0.01)

    assert outcome == "stopped"


@pytest.mark.asyncio
async def test_handler_never_logs_tokens_or_answers(state, caplog):
    caplog.set_level(logging.DEBUG)
    client = FakeSlackClient()
    brain = ScriptedBrain(plans=[PLAN], answers=["*10* mentions."])
    await _bot(state, brain).handle_mention(mention_event(), client)
    assert "xoxb" not in caplog.text and "xapp" not in caplog.text
    assert "10* mentions" not in caplog.text


def test_bolt_app_builds_offline_with_one_app_mention_listener(tmp_path):
    bot = app_mod.QueryBot(None, ScriptedBrain(), _config(), CHANNEL)
    bolt = app_mod.build_bolt_app(bot, BOT_TOKEN)
    listeners = bolt._async_listeners
    assert len(listeners) == 1
    matcher_source = json.dumps([str(m) for m in listeners[0].matchers])
    assert listeners[0] is not None and matcher_source


def test_cli_has_slackbot_and_slack_test_commands():
    from harvey.cli import build_parser

    parser = build_parser()
    assert parser.parse_args(["slackbot"]).func.__name__ == "cmd_slackbot"
    assert parser.parse_args(["slack-test"]).func.__name__ == "cmd_slack_test"
    assert parser.parse_args(["health", "--slackbot"]).slackbot is True


@pytest.mark.asyncio
async def test_concurrent_mentions_cannot_overrun_the_limit(state):
    config = _config(per_user_per_hour=2)
    gate = asyncio.Event()

    class SlowBrain(ScriptedBrain):
        async def think_json(self, *a, **k):
            await gate.wait()
            return dict(PLAN)

    bot = _bot(state, SlowBrain(answers=["ok"] * 5), config)
    client = FakeSlackClient()
    tasks = [asyncio.create_task(bot.handle_mention(mention_event(ts=f"5.{i}"), client)) for i in range(4)]
    await asyncio.sleep(0.05)
    gate.set()
    outcomes = sorted(await asyncio.gather(*tasks))
    assert outcomes == ["answered", "answered", "limited", "limited"]
    assert bot._pending == {}


@pytest.mark.asyncio
async def test_thread_broadcast_mentions_are_answered(state):
    client = FakeSlackClient()
    bot = _bot(state, ScriptedBrain(plans=[PLAN], answers=["ok"]))
    assert await bot.handle_mention(mention_event(subtype="thread_broadcast"), client) == "answered"
