"""Slack channel routing: escalation pages use SLACK_WEBHOOK_URL (#pulse-alerts),
briefs use SLACK_BRIEFS_WEBHOOK_URL (#pulse-briefs) and fall back to the
alerts webhook. Webhook URLs and Slack tokens never reach a log. No network."""

import json
import logging

import httpx
import pytest

from harvey.config import PulseConfig, load_env
from harvey.notify import SlackNotifier
from harvey.notify import slack as slack_mod

ALERTS = "https://hooks.slack.example.invalid/services/T0/B1/ALERTSSECRET111"
BRIEFS = "https://hooks.slack.example.invalid/services/T0/B2/BRIEFSSECRET222"


def _recorder():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, text="ok")

    return seen, httpx.MockTransport(handler)


def test_alerts_notifier_uses_the_alerts_webhook():
    notifier = SlackNotifier.from_config(PulseConfig(), environ={
        "SLACK_WEBHOOK_URL": ALERTS, "SLACK_BRIEFS_WEBHOOK_URL": BRIEFS})
    assert notifier._url == ALERTS


def test_briefs_notifier_prefers_the_briefs_webhook():
    notifier = SlackNotifier.for_briefs(PulseConfig(), environ={
        "SLACK_WEBHOOK_URL": ALERTS, "SLACK_BRIEFS_WEBHOOK_URL": BRIEFS})
    assert notifier._url == BRIEFS
    assert notifier.enabled


@pytest.mark.parametrize("briefs", ["", "   ", None])
def test_briefs_notifier_falls_back_to_the_alerts_webhook(briefs):
    environ = {"SLACK_WEBHOOK_URL": ALERTS}
    if briefs is not None:
        environ["SLACK_BRIEFS_WEBHOOK_URL"] = briefs
    assert SlackNotifier.for_briefs(PulseConfig(), environ=environ)._url == ALERTS


def test_briefs_notifier_disabled_when_nothing_is_set():
    assert SlackNotifier.for_briefs(PulseConfig(), environ={}).enabled is False


def test_briefs_env_name_is_configurable():
    config = PulseConfig(notify={"slack_briefs_webhook_env": "OTHER_BRIEFS"})
    notifier = SlackNotifier.for_briefs(config, environ={"OTHER_BRIEFS": BRIEFS, "SLACK_WEBHOOK_URL": ALERTS})
    assert notifier._url == BRIEFS


def test_repr_never_shows_the_url():
    notifier = SlackNotifier.for_briefs(PulseConfig(), environ={"SLACK_BRIEFS_WEBHOOK_URL": BRIEFS})
    assert "BRIEFSSECRET" not in repr(notifier)


@pytest.mark.asyncio
async def test_briefs_webhook_is_redacted_from_httpx_logs(caplog):
    caplog.set_level(logging.DEBUG)
    seen, transport = _recorder()
    notifier = SlackNotifier.for_briefs(PulseConfig(), environ={"SLACK_BRIEFS_WEBHOOK_URL": BRIEFS},
                                        transport=transport)
    assert await notifier.send("hello") is True
    assert str(seen[0].url) == BRIEFS
    assert "BRIEFSSECRET222" not in caplog.text


@pytest.mark.asyncio
async def test_disabled_briefs_notifier_says_briefs_not_pages(caplog):
    caplog.set_level(logging.INFO, logger="harvey.notify.slack")
    await SlackNotifier.for_briefs(PulseConfig(), environ={}).send("x")
    assert "brief" in caplog.text.lower()


def test_redaction_filter_scrubs_slack_tokens():
    record = logging.LogRecord("slack_sdk", logging.INFO, __file__, 1,
                               "auth with xoxb-1111-2222-abcdEFGH and xapp-1-A0-123-zzz", None, None)
    slack_mod.REDACTOR.filter(record)
    text = record.getMessage()
    assert "xoxb-1111" not in text and "xapp-1-A0" not in text
    assert "[slack-token]" in text


def test_redaction_filter_scrubs_registered_secrets():
    slack_mod.register_secret("some-registered-secret-value")
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "value=%s", ("some-registered-secret-value",), None)
    slack_mod.REDACTOR.filter(record)
    assert "some-registered-secret-value" not in record.getMessage()


def test_load_env_reads_the_new_slack_variables(monkeypatch):
    monkeypatch.setenv("SLACK_BRIEFS_WEBHOOK_URL", BRIEFS)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-1-2-abc")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-1-2-abc")
    monkeypatch.setenv("SLACK_QUERY_CHANNEL_ID", "C0TEST")
    env = load_env()
    assert env.slack_briefs_webhook_url == BRIEFS
    assert env.slack_bot_token == "xoxb-1-2-abc"
    assert env.slack_app_token == "xapp-1-2-abc"
    assert env.slack_query_channel_id == "C0TEST"
    text = repr(env)
    assert "xoxb" not in text and "xapp" not in text and "BRIEFSSECRET" not in text


# ── Briefs go to the briefs notifier, pages to the alerts notifier ──


def test_heartbeat_builds_a_briefs_notifier():
    import inspect

    from harvey import main as main_mod

    assert "SlackNotifier.for_briefs(" in inspect.getsource(main_mod.heartbeat)


def test_cli_brief_and_dashboard_use_the_briefs_notifier():
    import inspect

    from harvey import cli, dashboard

    assert "SlackNotifier.for_briefs(" in inspect.getsource(cli.cmd_brief)
    assert "get_briefs_notifier" in inspect.getsource(dashboard.generate_brief)
