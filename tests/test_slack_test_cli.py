"""`pulse slack-test`: one labelled TEST message per configured webhook (no
PHI), ok/fail per channel, webhook URLs never printed. MockTransport only."""

import json

import httpx
import pytest

from harvey import cli
from harvey.config import PulseConfig
from harvey.notify import slack as slack_mod

ALERTS = "https://hooks.slack.example.invalid/services/T0/B1/ALERTSSECRET111"
BRIEFS = "https://hooks.slack.example.invalid/services/T0/B2/BRIEFSSECRET222"


def _transport(status_by_url: dict[str, int], seen: list):
    def handler(request):
        seen.append(request)
        return httpx.Response(status_by_url.get(str(request.url), 404), text="x")

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_sends_one_test_message_per_channel():
    seen = []
    results = await slack_mod.send_test_messages(
        PulseConfig(), environ={"SLACK_WEBHOOK_URL": ALERTS, "SLACK_BRIEFS_WEBHOOK_URL": BRIEFS},
        transport=_transport({ALERTS: 200, BRIEFS: 200}, seen))
    assert results == [("alerts (SLACK_WEBHOOK_URL)", "ok"), ("briefs (SLACK_BRIEFS_WEBHOOK_URL)", "ok")]
    bodies = {str(r.url): json.loads(r.content)["text"] for r in seen}
    assert bodies[ALERTS].startswith("[TEST]") and "escalation pages" in bodies[ALERTS]
    assert bodies[BRIEFS].startswith("[TEST]") and "briefs" in bodies[BRIEFS]


@pytest.mark.asyncio
async def test_reports_failures_per_channel():
    seen = []
    results = await slack_mod.send_test_messages(
        PulseConfig(), environ={"SLACK_WEBHOOK_URL": ALERTS, "SLACK_BRIEFS_WEBHOOK_URL": BRIEFS},
        transport=_transport({ALERTS: 200, BRIEFS: 403}, seen))
    assert dict(results) == {"alerts (SLACK_WEBHOOK_URL)": "ok", "briefs (SLACK_BRIEFS_WEBHOOK_URL)": "failed"}


@pytest.mark.asyncio
async def test_briefs_fallback_and_unconfigured():
    seen = []
    results = await slack_mod.send_test_messages(PulseConfig(), environ={"SLACK_WEBHOOK_URL": ALERTS},
                                                 transport=_transport({ALERTS: 200}, seen))
    assert results[1][1] == "not configured (briefs fall back to SLACK_WEBHOOK_URL)"
    assert len(seen) == 1

    none = await slack_mod.send_test_messages(PulseConfig(), environ={}, transport=_transport({}, seen))
    assert [r[1] for r in none] == ["not configured", "not configured"]

    same = await slack_mod.send_test_messages(
        PulseConfig(), environ={"SLACK_WEBHOOK_URL": ALERTS, "SLACK_BRIEFS_WEBHOOK_URL": ALERTS},
        transport=_transport({ALERTS: 200}, seen))
    assert "not sent twice" in same[1][1]


def _cli(monkeypatch, capsys, env: dict, statuses: dict) -> tuple[int, str]:
    seen = []
    real = slack_mod.send_test_messages

    async def patched(config, environ=None, transport=None):
        return await real(config, environ=env, transport=_transport(statuses, seen))

    monkeypatch.setattr(slack_mod, "send_test_messages", patched)
    args = cli.build_parser().parse_args(["slack-test"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    return exc.value.code, capsys.readouterr().out


def test_cli_prints_ok_per_channel_without_urls(monkeypatch, capsys):
    code, out = _cli(monkeypatch, capsys, {"SLACK_WEBHOOK_URL": ALERTS, "SLACK_BRIEFS_WEBHOOK_URL": BRIEFS},
                     {ALERTS: 200, BRIEFS: 200})
    assert code == 0
    assert "alerts (SLACK_WEBHOOK_URL)" in out and "briefs (SLACK_BRIEFS_WEBHOOK_URL)" in out
    assert "SECRET" not in out and "hooks.slack" not in out


def test_cli_fails_when_a_channel_fails_or_nothing_is_configured(monkeypatch, capsys):
    code, out = _cli(monkeypatch, capsys, {"SLACK_WEBHOOK_URL": ALERTS}, {ALERTS: 500})
    assert code == 1 and "failed" in out and "SECRET" not in out
    code, out = _cli(monkeypatch, capsys, {}, {})
    assert code == 1 and "not configured" in out
