"""This month's spend, for the review desk header (GET /api/spend).

Claude: ``usage_events.cost_usd`` since the first of the (UTC) month, in total
and per agent. Under subscription billing it is the API-equivalent cost the
Claude CLI reports, not an invoice; ``billing`` says which account pays
(harvey/claude_billing.py). Apify: ``runs.cost_usd`` of the Apify collector
runs in the same month. Aggregates only; no post text.
"""

import calendar
from datetime import datetime, timezone

from harvey import claude_billing
from harvey.db.dialect import sql_utc

APIFY_STAGE_PREFIX = "apify"
OTHER_AGENT = "other"


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def billing_label() -> str:
    """subscription | api, even if the billing setting is misconfigured."""
    try:
        return claude_billing.billing_mode()
    except claude_billing.ClaudeBillingError:
        return claude_billing.API if claude_billing.deployed() else claude_billing.SUBSCRIPTION


async def _rows(state, sql: str, params: tuple) -> list[dict]:
    async with state.connect() as db:
        async with db.execute(sql, params) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


async def month_spend(state, now: datetime | None = None) -> dict:
    start = month_start(now)
    since = (sql_utc(start),)
    claude_rows = await _rows(
        state, "SELECT CASE WHEN agent = '' THEN 'other' ELSE agent END AS agent, "
               "COALESCE(SUM(cost_usd), 0) AS cost FROM usage_events WHERE created_at >= ? GROUP BY 1", since)
    by_agent: dict[str, float] = {}
    for row in claude_rows:
        by_agent[row["agent"]] = round(by_agent.get(row["agent"], 0.0) + float(row["cost"] or 0.0), 4)
    claude_total = round(sum(by_agent.values()), 4)
    apify = (await _rows(
        state, "SELECT COALESCE(SUM(cost_usd), 0) AS cost, COUNT(*) AS runs FROM runs "
               "WHERE started_at >= ? AND stage LIKE 'apify%'", since) or [{}])[0]
    apify_total = round(float(apify.get("cost") or 0.0), 4)
    return {
        "month": start.strftime("%Y-%m"),
        "month_label": calendar.month_name[start.month],
        "claude": {"total": claude_total, "by_agent": dict(sorted(by_agent.items(), key=lambda kv: -kv[1])),
                   "billing": billing_label()},
        "apify": {"total": apify_total, "runs": int(apify.get("runs") or 0)},
        "total": round(claude_total + apify_total, 4),
    }
