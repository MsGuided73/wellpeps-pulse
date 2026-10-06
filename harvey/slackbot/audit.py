"""Rate limits and the audit trail for #pulse-query, in the ``actions`` table.

No schema change: one ``actions`` row per question.

- ``action_type = 'slack_query'``: a question the bot handled (answered,
  help, or an action request it declined). These count toward the limits.
- ``action_type = 'slack_query_refused'``: wrong channel or over a limit.
  These never count, so a refusal can't lock anyone out.
- ``agent = 'slackbot:<slack user id>'``, so the per-user count is a plain
  portable ``WHERE agent = ?`` (no JSON functions).
- ``created_at`` is written explicitly (``sql_utc``), so the cut-offs compare
  like with like on SQLite and Postgres.

``details_json`` holds: Slack user id, channel id, intent, days, filters,
answer length, models, latency, blocked reason, limited flag, whether the
templated fallback was used, and the question truncated to 200 characters
with links/handles/e-mails stripped (staff questions; never the answer
text, never mention text).
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta

import pytz

from harvey import trends
from harvey.db.dialect import sql_utc

ACTION_TYPE = "slack_query"
REFUSED_TYPE = "slack_query_refused"
AGENT_PREFIX = "slackbot:"
MAX_QUESTION_CHARS = 200

COUNT_SINCE_SQL = "SELECT COUNT(*) FROM actions WHERE action_type = ? AND created_at >= ?"
COUNT_USER_SINCE_SQL = "SELECT COUNT(*) FROM actions WHERE action_type = ? AND agent = ? AND created_at >= ?"
INSERT_SQL = ("INSERT INTO actions (id, action_type, agent, details_json, created_at) "
              "VALUES (?, ?, ?, ?, ?)")


@dataclass
class LimitCheck:
    ok: bool
    reason: str = ""          # "" | "daily_limit" | "user_hourly_limit"
    today: int = 0
    user_hour: int = 0


def local_day_start(now: datetime, tz_name: str) -> datetime:
    """Local midnight (org timezone) of ``now``'s local day, as naive UTC."""
    tz = pytz.timezone(tz_name)
    today = pytz.utc.localize(now).astimezone(tz).date()
    return tz.localize(datetime.combine(today, time())).astimezone(pytz.utc).replace(tzinfo=None)


async def _count(state, sql: str, params: tuple) -> int:
    async with state.connect() as db:
        async with db.execute(sql, params) as cursor:
            row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def check_limits(state, user_id: str, config, now: datetime, *, pending_total: int = 0,
                       pending_user: int = 0) -> LimitCheck:
    """``pending_*``: questions admitted in this process but not yet audited."""
    limits = config.slack_query
    day_start = sql_utc(local_day_start(now, config.usage.quiet_hours.timezone))
    today = pending_total + await _count(state, COUNT_SINCE_SQL, (ACTION_TYPE, day_start))
    user_hour = pending_user + await _count(
        state, COUNT_USER_SINCE_SQL, (ACTION_TYPE, AGENT_PREFIX + user_id, sql_utc(now - timedelta(hours=1))))
    if today >= limits.daily_limit:
        return LimitCheck(False, "daily_limit", today, user_hour)
    if user_hour >= limits.per_user_per_hour:
        return LimitCheck(False, "user_hourly_limit", today, user_hour)
    return LimitCheck(True, "", today, user_hour)


def safe_question(question: str) -> str:
    return " ".join(trends.strip_identifiers(question or "").split())[:MAX_QUESTION_CHARS]


async def record(state, *, user_id: str, channel_id: str, now: datetime, counted: bool,
                 question: str = "", intent: str = "", days: int | None = None, filters: dict | None = None,
                 answer_chars: int = 0, models: dict | None = None, latency_ms: int = 0,
                 blocked: str = "", limited: bool = False, fallback: bool = False, outcome: str = "") -> None:
    details = {
        "slack_user": user_id, "channel": channel_id, "intent": intent, "days": days,
        "filters": filters or {}, "answer_chars": answer_chars, "models": models or {},
        "latency_ms": latency_ms, "blocked": blocked, "limited": limited, "fallback": fallback,
        "outcome": outcome, "question": safe_question(question),
    }
    async with state.connect() as db:
        await db.execute(INSERT_SQL, (uuid.uuid4().hex[:12], ACTION_TYPE if counted else REFUSED_TYPE,
                                      AGENT_PREFIX + (user_id or "unknown"), json.dumps(details),
                                      sql_utc(now)))
        await db.commit()


async def recent(state, limit: int = 50) -> list[dict]:
    """Newest audit rows first (for tests and ops)."""
    async with state.connect() as db:
        async with db.execute(
            "SELECT action_type, agent, details_json, created_at FROM actions "
            "WHERE action_type IN (?, ?) ORDER BY created_at DESC, id DESC LIMIT ?",
            (ACTION_TYPE, REFUSED_TYPE, int(limit)),
        ) as cursor:
            rows = [dict(r) for r in await cursor.fetchall()]
    for row in rows:
        row["details"] = json.loads(row.pop("details_json") or "{}")
    return rows
