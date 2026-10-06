"""Slack incoming-webhook notifier.

- No webhook configured: every ``send`` is a no-op returning False (logged
  once at info level), so Pulse runs fine without Slack.
- One retry on a 5xx response or a timeout; 4xx is not retried.
- Never raises into callers; failures are logged and ``send`` returns False.
- The webhook URL is a secret: it is never logged or shown in ``repr``.

What goes *into* a message is decided by ``harvey.escalation.build_page``
(escalation pages, link and category only) or ``harvey.briefs.build_brief_message``
(brief headline + action titles). Pages go to ``notify.slack_webhook_env``
(#pulse-alerts); briefs go to ``notify.slack_briefs_webhook_env`` (#pulse-briefs)
and fall back to the alerts webhook when that is unset (``for_briefs``).

Redaction: every webhook URL in use, every ``register_secret`` value, and
anything shaped like a Slack token (``xoxb-``/``xapp-``/...) is scrubbed from
log records on the httpx/httpcore/slack loggers (``REDACTOR``), and
``install_redaction`` puts the same filter on the root handlers.
"""

import logging
import os
import re
from collections.abc import Mapping

import httpx
from dotenv import load_dotenv

logger = logging.getLogger("harvey.notify.slack")

TIMEOUT_SECONDS = 10.0
MAX_ATTEMPTS = 2  # first try + one retry
REDACTED = "[slack-webhook]"
REDACTED_TOKEN = "[slack-token]"
# Bot (xoxb), app-level (xapp), user (xoxp) and other Slack token shapes.
_TOKEN = re.compile(r"\bx(?:ox[abeoprs]|app)-[A-Za-z0-9-]+")
# Loggers that can carry a request URL or an Authorization header.
REDACTED_LOGGERS = ("httpx", "httpcore", "slack_sdk", "slack_bolt", "aiohttp")

# Webhook URLs (and other registered secrets) in use. httpx logs every
# request URL at INFO level, so a filter on its logger scrubs these before
# any handler sees the record.
_SECRET_URLS: set[str] = set()


def register_secret(value: str | None) -> None:
    """Scrub ``value`` from every log record the redaction filter sees."""
    value = (value or "").strip()
    if len(value) >= 8:
        _SECRET_URLS.add(value)


def redact(text: str) -> str:
    """``text`` with registered secrets and Slack-token shapes replaced."""
    for secret in sorted(_SECRET_URLS, key=len, reverse=True):
        if secret in text:
            text = text.replace(secret, REDACTED_TOKEN if _TOKEN.fullmatch(secret) else REDACTED)
    return _TOKEN.sub(REDACTED_TOKEN, text)


class _RedactWebhook(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = redact(message)
        if redacted != message:
            record.msg, record.args = redacted, ()
        return True


REDACTOR = _RedactWebhook()
_REDACTOR = REDACTOR  # backward-compatible name
for _name in REDACTED_LOGGERS:
    if REDACTOR not in logging.getLogger(_name).filters:
        logging.getLogger(_name).addFilter(REDACTOR)


def install_redaction() -> None:
    """Also filter at the root handlers, so a record from any child logger
    (``slack_sdk.web.async_client``, ...) is scrubbed before it is written."""
    for handler in logging.getLogger().handlers:
        if REDACTOR not in handler.filters:
            handler.addFilter(REDACTOR)


class SlackNotifier:
    def __init__(
        self,
        webhook_url: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = TIMEOUT_SECONDS,
        purpose: str = "escalation pages",
    ):
        self._url = (webhook_url or "").strip()
        if self._url:
            _SECRET_URLS.add(self._url)
        self.purpose = purpose
        self._transport = transport
        self.timeout = float(timeout)
        self._told_disabled = False

    @classmethod
    def from_config(
        cls,
        config,
        environ: Mapping[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> "SlackNotifier":
        """Build from ``config.notify.slack_webhook_env`` (the env var's name)."""
        if environ is None:
            load_dotenv()
            environ = os.environ
        return cls(environ.get(config.notify.slack_webhook_env, ""), transport=transport)

    @classmethod
    def for_briefs(
        cls,
        config,
        environ: Mapping[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> "SlackNotifier":
        """Briefs channel: ``config.notify.slack_briefs_webhook_env``, falling
        back to the alerts webhook (``slack_webhook_env``) when unset."""
        if environ is None:
            load_dotenv()
            environ = os.environ
        url = (environ.get(config.notify.slack_briefs_webhook_env) or "").strip()
        url = url or (environ.get(config.notify.slack_webhook_env) or "")
        return cls(url, transport=transport, purpose="brief messages")

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    def __repr__(self) -> str:
        return f"SlackNotifier(enabled={self.enabled})"

    async def send(self, text: str, blocks: list | None = None) -> bool:
        """Post one message. True only when Slack accepted it."""
        if not self._url:
            if not self._told_disabled:
                logger.info(f"Slack webhook not configured; {self.purpose} are skipped.")
                self._told_disabled = True
            return False
        payload: dict = {"text": text}
        if blocks is not None:
            payload["blocks"] = blocks
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
                return await self._post(client, payload)
        except Exception as exc:  # never raise into callers
            logger.error(f"Slack send failed: {type(exc).__name__}")
            return False

    async def _post(self, client: httpx.AsyncClient, payload: dict) -> bool:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            retry = attempt < MAX_ATTEMPTS
            try:
                response = await client.post(self._url, json=payload)
            except httpx.TimeoutException as exc:
                logger.warning(f"Slack send timed out ({type(exc).__name__}), attempt {attempt}")
                if retry:
                    continue
                return False
            except httpx.HTTPError as exc:
                # Connection errors: don't echo the message, it can carry the URL.
                logger.error(f"Slack send failed: {type(exc).__name__}")
                return False
            if response.status_code < 300:
                return True
            if response.status_code >= 500 and retry:
                logger.warning(f"Slack returned {response.status_code}, retrying once")
                continue
            logger.error(f"Slack returned {response.status_code}; message not delivered")
            return False
        return False  # pragma: no cover - loop always returns


# ── `pulse slack-test` ──

TEST_ALERT_TEXT = ("[TEST] WellPeps Pulse test page. No action needed: this checks that "
                   "escalation pages reach this channel. It carries no mention data.")
TEST_BRIEF_TEXT = ("[TEST] WellPeps Pulse test message. No action needed: this checks that "
                   "daily and weekly briefs reach this channel. It carries no mention data.")


async def send_test_messages(config, environ: Mapping[str, str] | None = None,
                             transport: httpx.AsyncBaseTransport | None = None) -> list[tuple[str, str]]:
    """One clearly labelled TEST message per configured webhook (no PHI).

    Returns [(channel label, "ok" | "failed" | "not configured" | note)].
    Never returns or logs a webhook URL."""
    if environ is None:
        load_dotenv()
        environ = os.environ
    alerts_env = config.notify.slack_webhook_env
    briefs_env = config.notify.slack_briefs_webhook_env
    alerts_url = (environ.get(alerts_env) or "").strip()
    briefs_url = (environ.get(briefs_env) or "").strip()
    results = []
    if alerts_url:
        ok = await SlackNotifier(alerts_url, transport=transport).send(TEST_ALERT_TEXT)
        results.append((f"alerts ({alerts_env})", "ok" if ok else "failed"))
    else:
        results.append((f"alerts ({alerts_env})", "not configured"))
    if briefs_url and briefs_url != alerts_url:
        ok = await SlackNotifier(briefs_url, transport=transport, purpose="brief messages").send(TEST_BRIEF_TEXT)
        results.append((f"briefs ({briefs_env})", "ok" if ok else "failed"))
    elif briefs_url:
        results.append((f"briefs ({briefs_env})", "same webhook as alerts; not sent twice"))
    else:
        note = f"not configured (briefs fall back to {alerts_env})" if alerts_url else "not configured"
        results.append((f"briefs ({briefs_env})", note))
    return results
