"""Slack incoming-webhook notifier.

- No webhook configured: every ``send`` is a no-op returning False (logged
  once at info level), so Pulse runs fine without Slack.
- One retry on a 5xx response or a timeout; 4xx is not retried.
- Never raises into callers; failures are logged and ``send`` returns False.
- The webhook URL is a secret: it is never logged or shown in ``repr``.

What goes *into* a message is decided by ``harvey.escalation.build_page``,
which carries a link and a category only (no post text, no handles).
"""

import logging
import os
from collections.abc import Mapping

import httpx
from dotenv import load_dotenv

logger = logging.getLogger("harvey.notify.slack")

TIMEOUT_SECONDS = 10.0
MAX_ATTEMPTS = 2  # first try + one retry
REDACTED = "[slack-webhook]"

# Webhook URLs in use. httpx logs every request URL at INFO level, so a
# filter on its logger scrubs these before any handler sees the record.
_SECRET_URLS: set[str] = set()


class _RedactWebhook(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not _SECRET_URLS:
            return True
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = message
        for url in _SECRET_URLS:
            redacted = redacted.replace(url, REDACTED)
        if redacted != message:
            record.msg, record.args = redacted, ()
        return True


_REDACTOR = _RedactWebhook()
for _name in ("httpx", "httpcore"):
    if _REDACTOR not in logging.getLogger(_name).filters:
        logging.getLogger(_name).addFilter(_REDACTOR)


class SlackNotifier:
    def __init__(
        self,
        webhook_url: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = TIMEOUT_SECONDS,
    ):
        self._url = (webhook_url or "").strip()
        if self._url:
            _SECRET_URLS.add(self._url)
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

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    def __repr__(self) -> str:
        return f"SlackNotifier(enabled={self.enabled})"

    async def send(self, text: str, blocks: list | None = None) -> bool:
        """Post one message. True only when Slack accepted it."""
        if not self._url:
            if not self._told_disabled:
                logger.info("Slack webhook not configured; escalation pages are skipped.")
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
