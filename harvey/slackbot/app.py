"""`pulse slackbot`: the read-only #pulse-query bot over Slack Socket Mode.

Socket Mode means no public URL: the bot opens an outbound WebSocket with
the app-level token (``SLACK_APP_TOKEN``, ``xapp-``, connections:write) and
posts with the bot token (``SLACK_BOT_TOKEN``, ``xoxb-``). Both are secrets:
they are registered with the log redactor and never logged.

Behavior (``QueryBot.handle_mention``, testable without Slack):
- Only ``app_mention`` events are subscribed (thread replies that mention
  the bot are app_mentions too). Messages from bots are ignored.
- Outside ``SLACK_QUERY_CHANNEL_ID`` the bot answers once, in thread, "I only
  answer in #pulse-query" and nothing else. DMs are ignored (the app has no
  DM scopes or ``message.im`` subscription; a DM-shaped event is dropped).
- In the channel: limits check -> :hourglass_flowing_sand: reaction ->
  action requests get "actions happen in the dashboard" -> plan (haiku) ->
  execute (deterministic aggregates) -> answer (sonnet) -> guards -> reply
  in thread -> reaction removed -> one audit row.
- Read-only: nothing here approves, acks, posts to the public, or writes
  anything except its own audit rows and heartbeat.
- Quiet hours don't apply (on-demand).

Without tokens the process logs "Slack query bot disabled (no tokens)" and
idles, still stamping its heartbeat, so a deploy without Slack stays healthy.
"""

import asyncio
import logging
import re
import time as _time
from collections import OrderedDict
from datetime import datetime, timezone

from harvey import analytics
from harvey.config import ConfigError, env_setting
from harvey.health import SLACKBOT_HEARTBEAT_KEY, record_heartbeat
from harvey.notify import slack as slack_notify
from harvey.slackbot import answer as answer_mod
from harvey.slackbot import audit, executor, planner

logger = logging.getLogger("harvey.slackbot")

BOT_TOKEN_ENV = "SLACK_BOT_TOKEN"
APP_TOKEN_ENV = "SLACK_APP_TOKEN"
CHANNEL_ENV = "SLACK_QUERY_CHANNEL_ID"
HEARTBEAT_SECONDS = 60
REACTION = "hourglass_flowing_sand"
DISABLED_MESSAGE = "Slack query bot disabled (no tokens)"
_CHANNEL_ID = re.compile(r"^[CG][A-Z0-9]{6,20}$")
_SEEN_MAX = 500
# Mentions that still arrive as app_mention with a subtype.
_ALLOWED_SUBTYPES = frozenset({"thread_broadcast", "file_share"})

EXAMPLES = (
    "what's trending this week?",
    "share of voice vs Hims & Hers, last 30 days",
    "sentiment for WellPeps this month",
    "top complaints this week",
    "how many mentions of shipping in the last 14 days?",
    "tirzepatide momentum this quarter",
    "escalations and SLA this week",
    "latest brief",
)


def help_text(dashboard_link: str = "") -> str:
    lines = ["I answer market questions from Pulse aggregates (read-only). Try:"]
    lines += [f"- `@Pulse {q}`" for q in EXAMPLES]
    lines.append("Windows are 1 to 90 days (default 7). Counts under 2 are not shown.")
    if dashboard_link:
        lines.append(f"<{dashboard_link}|Open in Pulse dashboard>")
    return "\n".join(lines)


def action_text(dashboard_link: str = "") -> str:
    where = f"<{dashboard_link}|in the Pulse dashboard>" if dashboard_link else "in the Pulse dashboard"
    return (f"I'm read-only: approvals, acknowledgements, replies and escalations happen {where}, "
            "where they are recorded in the audit log.")


def wrong_channel_text(channel_id: str) -> str:
    return f"I only answer in <#{channel_id}>."


LIMIT_TEXT = {
    "daily_limit": "The team has reached today's question limit for Pulse. Please try again tomorrow "
                   "or use the dashboard.",
    "user_hourly_limit": "You've reached the hourly question limit for Pulse. Please try again in a "
                         "little while or use the dashboard.",
}
ERROR_TEXT = "Sorry, I couldn't answer that just now. Please try again, or use the Pulse dashboard."


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class QueryBot:
    """The bot's logic, independent of Bolt. ``client`` needs the async
    ``chat_postMessage`` / ``reactions_add`` / ``reactions_remove`` methods of
    ``slack_sdk.web.async_client.AsyncWebClient`` (keyword arguments)."""

    def __init__(self, state, brain, config, channel_id: str, *, clock=_utcnow):
        self.state = state
        self.brain = brain
        self.config = config
        self.channel_id = channel_id
        self.clock = clock
        self._seen: OrderedDict = OrderedDict()
        # Questions admitted but not yet audited, so concurrent mentions
        # can't all pass the limit check before any row is written.
        self._limit_lock = asyncio.Lock()
        self._pending: dict[str, int] = {}

    @property
    def dashboard_url(self) -> str:
        return self.config.notify.dashboard_url

    def _models(self) -> dict:
        model_for = getattr(self.brain, "model_for", None)
        if not callable(model_for):
            return {}
        try:
            return {"plan": model_for(planner.AGENT, planner.TASK) or "",
                    "answer": model_for(answer_mod.AGENT, answer_mod.TASK) or ""}
        except Exception:
            return {}

    def _duplicate(self, channel: str, ts: str) -> bool:
        """Slack redelivers an event it thinks wasn't acked; answer once."""
        key = (channel, ts)
        if key in self._seen:
            return True
        self._seen[key] = True
        while len(self._seen) > _SEEN_MAX:
            self._seen.popitem(last=False)
        return False

    async def _post(self, client, channel: str, thread_ts: str, text: str) -> None:
        await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text,
                                      unfurl_links=False, unfurl_media=False)

    async def _react(self, client, method: str, channel: str, ts: str) -> None:
        try:
            await getattr(client, method)(channel=channel, timestamp=ts, name=REACTION)
        except Exception as exc:  # cosmetic only
            logger.debug(f"reaction {method} failed: {type(exc).__name__}")

    async def handle_mention(self, event: dict, client) -> str:
        """Handle one app_mention; returns the outcome (for logs and tests)."""
        if event.get("bot_id") or (event.get("subtype") and event.get("subtype") not in _ALLOWED_SUBTYPES):
            return "ignored_bot"
        channel = str(event.get("channel") or "")
        user = str(event.get("user") or "")
        ts = str(event.get("ts") or "")
        if event.get("channel_type") == "im" or channel.startswith("D"):
            return "ignored_dm"
        if not channel or not ts or self._duplicate(channel, ts):
            return "ignored_duplicate"
        thread = str(event.get("thread_ts") or ts)
        now = self.clock()
        question = planner.clean_question(event.get("text") or "")

        if channel != self.channel_id:
            await self._post(client, channel, thread, wrong_channel_text(self.channel_id))
            await audit.record(self.state, user_id=user, channel_id=channel, now=now, counted=False,
                               question=question, blocked="wrong_channel", outcome="wrong_channel")
            return "wrong_channel"

        async with self._limit_lock:
            limit = await audit.check_limits(self.state, user, self.config, now,
                                             pending_total=sum(self._pending.values()),
                                             pending_user=self._pending.get(user, 0))
            if limit.ok:
                self._pending[user] = self._pending.get(user, 0) + 1
        if not limit.ok:
            await self._post(client, channel, thread, LIMIT_TEXT[limit.reason])
            await audit.record(self.state, user_id=user, channel_id=channel, now=now, counted=False,
                               question=question, blocked=limit.reason, limited=True, outcome="limited")
            return "limited"

        await self._react(client, "reactions_add", channel, ts)
        started = _time.monotonic()
        try:
            return await self._answer(client, channel, thread, user, question, now, started)
        except Exception as exc:
            logger.error(f"slackbot query failed: {type(exc).__name__}")
            try:
                await self._post(client, channel, thread, ERROR_TEXT)
                await audit.record(self.state, user_id=user, channel_id=channel, now=now, counted=True,
                                   question=question, latency_ms=_ms(started), blocked="error",
                                   outcome="error")
            except Exception as inner:
                logger.error(f"slackbot error reply/audit failed: {type(inner).__name__}")
            return "error"
        finally:
            left = self._pending.get(user, 1) - 1
            if left > 0:
                self._pending[user] = left
            else:
                self._pending.pop(user, None)
            await self._react(client, "reactions_remove", channel, ts)

    async def _answer(self, client, channel, thread, user, question, now, started) -> str:
        home = executor.full_link(self.dashboard_url, "")
        record = dict(user_id=user, channel_id=channel, now=now, counted=True, question=question)

        if planner.is_action_request(question):
            text = action_text(home)
            await self._post(client, channel, thread, text)
            await audit.record(self.state, **record, intent="action", answer_chars=len(text),
                               latency_ms=_ms(started), blocked="read_only", outcome="action_refused")
            return "action_refused"

        options = await analytics.options(self.state, now)
        spec = await planner.plan(self.brain, question, competitors=options["competitors"],
                                  drugs=options["drugs"], today=now)
        if spec is None or spec.intent == "help":
            text = help_text(home)
            await self._post(client, channel, thread, text)
            await audit.record(self.state, **record, intent="help", answer_chars=len(text),
                               models=self._models(), latency_ms=_ms(started),
                               blocked="" if spec else "invalid_plan", outcome="help")
            return "help"

        result = await executor.execute(self.state, spec, self.config, now=now)
        raw = await answer_mod.write(self.brain, result.payload())
        reply = answer_mod.finalize(raw, result, self.dashboard_url)
        await self._post(client, channel, thread, reply.text)
        await audit.record(self.state, **record, intent=spec.intent, days=spec.days, filters=result.filters,
                           answer_chars=len(reply.text), models=self._models(), latency_ms=_ms(started),
                           fallback=reply.fallback, outcome="answered")
        return "answered"


def _ms(started: float) -> int:
    return int((_time.monotonic() - started) * 1000)


# ── Settings and process ──


def read_settings(environ=None) -> tuple[str, str, str] | None:
    """(bot token, app token, channel id), or None when both tokens are unset.

    Raises ConfigError for a half-configured or malformed setup (never
    echoing a token)."""
    get = (lambda name: (environ.get(name) or "").strip()) if environ is not None else env_setting
    bot, app, channel = get(BOT_TOKEN_ENV), get(APP_TOKEN_ENV), get(CHANNEL_ENV)
    if not bot and not app:
        return None
    if not bot or not app:
        missing = BOT_TOKEN_ENV if not bot else APP_TOKEN_ENV
        raise ConfigError(f"{missing} is not set; the Slack query bot needs both tokens (or neither).")
    if not bot.startswith("xoxb-"):
        raise ConfigError(f"{BOT_TOKEN_ENV} must be a bot token starting with xoxb-.")
    if not app.startswith("xapp-"):
        raise ConfigError(f"{APP_TOKEN_ENV} must be an app-level token starting with xapp- "
                          "(Basic Information -> App-Level Tokens, scope connections:write).")
    if not _CHANNEL_ID.match(channel):
        raise ConfigError(f"{CHANNEL_ENV} must be the #pulse-query channel ID (like C0123456789).")
    return bot, app, channel


def _quiet_slack_loggers() -> None:
    slack_notify.install_redaction()
    for name in ("slack_sdk", "slack_bolt", "aiohttp", "websockets"):
        logging.getLogger(name).setLevel(logging.WARNING)


def build_bolt_app(bot: QueryBot, bot_token: str):
    """The thin Bolt layer: one app_mention listener that delegates."""
    from slack_bolt.async_app import AsyncApp

    app = AsyncApp(token=bot_token, logger=logging.getLogger("slack_bolt.app"))

    @app.event("app_mention")
    async def _on_mention(event, client):
        outcome = await bot.handle_mention(event, client)
        logger.info(f"app_mention handled: {outcome}")

    return app


async def _beat(state) -> None:
    try:
        await record_heartbeat(state, key=SLACKBOT_HEARTBEAT_KEY)
    except Exception as exc:
        logger.warning(f"slackbot heartbeat failed: {type(exc).__name__}")


async def _heartbeat_loop(state, stop_event: asyncio.Event, interval: float) -> None:
    while not stop_event.is_set():
        await _beat(state)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def run(config, *, stop_event: asyncio.Event | None = None, environ=None, state=None,
              brain=None, interval: float = HEARTBEAT_SECONDS, handler_factory=None) -> str:
    """Run until ``stop_event``. Returns "disabled" or "stopped"."""
    from harvey.state import StateManager

    stop_event = stop_event or asyncio.Event()
    settings = read_settings(environ)
    _quiet_slack_loggers()
    state = state or StateManager()
    await state.init_db()
    if settings is None:
        logger.info(DISABLED_MESSAGE)
        await _heartbeat_loop(state, stop_event, interval)
        return "disabled"

    bot_token, app_token, channel = settings
    slack_notify.register_secret(bot_token)
    slack_notify.register_secret(app_token)
    if brain is None:
        from harvey.brain import Brain

        brain = Brain(state, models=config.usage.models)
    bot = QueryBot(state, brain, config, channel)
    if handler_factory is None:
        from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

        def handler_factory(app, token):
            return AsyncSocketModeHandler(app, token)

    handler = handler_factory(build_bolt_app(bot, bot_token), app_token)
    await handler.connect_async()
    logger.info("Slack query bot connected (Socket Mode).")
    try:
        await _heartbeat_loop(state, stop_event, interval)
    finally:
        await handler.close_async()
    return "stopped"


def main() -> None:
    """`pulse slackbot` entry point (SIGINT/SIGTERM stop it cleanly)."""
    import signal

    from harvey.config import load_config
    from harvey.db.postgres import run as run_async

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")

    async def _main():
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except (NotImplementedError, RuntimeError):
                pass  # Windows: Ctrl+C raises KeyboardInterrupt instead
        await run(load_config(), stop_event=stop_event)

    run_async(_main())
