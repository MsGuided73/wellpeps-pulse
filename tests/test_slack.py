"""Slack notifier: no-op without a webhook, one retry on 5xx/timeout, never
raises, never logs the webhook URL. No real network (httpx.MockTransport)."""

import json
import logging

import httpx
import pytest

from harvey.config import PulseConfig
from harvey.notify import SlackNotifier

WEBHOOK = "https://hooks.slack.example.invalid/services/T000/B000/SECRETTOKEN123"


class Recorder:
    """MockTransport handler that replays a script of responses/exceptions."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(step, Exception):
            raise step
        return httpx.Response(step, text="ok" if step < 300 else "error")

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [None, "", "   "])
async def test_no_webhook_is_a_logged_once_noop(url, caplog):
    caplog.set_level(logging.INFO, logger="harvey.notify.slack")
    rec = Recorder(200)
    notifier = SlackNotifier(url, transport=rec.transport())

    first = await notifier.send("hello")
    second = await notifier.send("hello again")

    assert (first, second) == (False, False)
    assert rec.requests == []
    assert notifier.enabled is False
    unset = [r for r in caplog.records if "webhook" in r.getMessage().lower()]
    assert len(unset) == 1 and unset[0].levelno == logging.INFO


@pytest.mark.asyncio
async def test_success_posts_text_and_blocks():
    rec = Recorder(200)
    notifier = SlackNotifier(WEBHOOK, transport=rec.transport())
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}]

    ok = await notifier.send("hi", blocks=blocks)

    assert ok is True
    assert len(rec.requests) == 1
    body = json.loads(rec.requests[0].content)
    assert body == {"text": "hi", "blocks": blocks}
    assert str(rec.requests[0].url) == WEBHOOK


@pytest.mark.asyncio
async def test_blocks_omitted_when_none():
    rec = Recorder(200)

    await SlackNotifier(WEBHOOK, transport=rec.transport()).send("hi")

    assert json.loads(rec.requests[0].content) == {"text": "hi"}


@pytest.mark.asyncio
async def test_retries_once_on_500_then_succeeds():
    rec = Recorder(500, 200)

    ok = await SlackNotifier(WEBHOOK, transport=rec.transport()).send("hi")

    assert ok is True
    assert len(rec.requests) == 2


@pytest.mark.asyncio
async def test_gives_up_after_second_5xx():
    rec = Recorder(503, 502, 200)

    ok = await SlackNotifier(WEBHOOK, transport=rec.transport()).send("hi")

    assert ok is False
    assert len(rec.requests) == 2


@pytest.mark.asyncio
async def test_retries_once_on_timeout():
    rec = Recorder(httpx.ReadTimeout("slow"), 200)

    ok = await SlackNotifier(WEBHOOK, transport=rec.transport()).send("hi")

    assert ok is True
    assert len(rec.requests) == 2


@pytest.mark.asyncio
async def test_4xx_is_not_retried():
    rec = Recorder(404, 200)

    ok = await SlackNotifier(WEBHOOK, transport=rec.transport()).send("hi")

    assert ok is False
    assert len(rec.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("boom", [
    httpx.ConnectError("refused"), RuntimeError("unexpected"), ValueError("bad"),
])
async def test_never_raises(boom, caplog):
    caplog.set_level(logging.DEBUG)
    rec = Recorder(boom)

    ok = await SlackNotifier(WEBHOOK, transport=rec.transport()).send("hi")

    assert ok is False
    assert "SECRETTOKEN123" not in caplog.text


@pytest.mark.asyncio
async def test_webhook_url_never_logged_on_http_errors(caplog):
    caplog.set_level(logging.DEBUG)
    rec = Recorder(500)

    await SlackNotifier(WEBHOOK, transport=rec.transport()).send("hi")

    assert "SECRETTOKEN123" not in caplog.text
    assert "hooks.slack" not in caplog.text
    assert "500" in caplog.text  # the status is still reported


def test_repr_hides_the_webhook():
    notifier = SlackNotifier(WEBHOOK)

    assert "SECRETTOKEN123" not in repr(notifier)


def test_timeout_is_ten_seconds():
    assert SlackNotifier(WEBHOOK).timeout == 10.0


def test_from_config_reads_the_named_env_var():
    config = PulseConfig(notify={"slack_webhook_env": "PULSE_TEST_SLACK"})

    on = SlackNotifier.from_config(config, environ={"PULSE_TEST_SLACK": f"  {WEBHOOK} "})
    off = SlackNotifier.from_config(config, environ={"SLACK_WEBHOOK_URL": WEBHOOK})

    assert on.enabled is True
    assert off.enabled is False
