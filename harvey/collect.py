"""Live collection schedule (Phase 9): which collectors are due this cycle.

The heartbeat calls ``run_due_collectors`` every cycle (before triage). A
collector runs when it is enabled in harvey.yaml (``collectors:``), its
interval has passed since its last run started, and its runs this calendar
month (UTC) have cost less than its monthly budget. ``pulse ingest --apify``
runs one on demand (still within the monthly budget).

Collection never calls a model; triage picks the new mentions up next.
"""

import logging
from datetime import datetime, timedelta, timezone

from harvey.collectors import get_collector
from harvey.config import PulseConfig
from harvey.ingest import IngestReport, run_collectors

logger = logging.getLogger("harvey.collect")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def collector_settings(config: PulseConfig) -> dict:
    """name -> (settings model, kwargs for the collector)."""
    reddit = config.collectors.apify_reddit
    return {
        "apify_reddit": (reddit, {"searches": list(reddit.searches), "max_items": reddit.max_items,
                                  "max_charge_usd": reddit.max_charge_usd,
                                  "lookback_hours": reddit.lookback_hours}),
    }


async def blocked_reason(state, name: str, settings, now: datetime, *, scheduled: bool) -> str:
    """Why ``name`` may not run now ("" = it may)."""
    if scheduled and not settings.enabled:
        return "disabled in harvey.yaml"
    spent = await state.run_cost_since(name, month_start(now))
    if spent >= settings.monthly_budget_usd:
        return f"monthly budget reached (${spent:.2f} of ${settings.monthly_budget_usd:.2f})"
    if scheduled:
        last = await state.last_run_started(name)
        if last is not None and now - last < timedelta(minutes=settings.interval_minutes):
            return "not due yet"
    return ""


async def run_collector(state, config: PulseConfig, name: str, *, scheduled: bool,
                        now: datetime | None = None, build=get_collector) -> IngestReport | None:
    """Run ``name`` once if allowed; None (logged) when blocked."""
    now = now or _utcnow()
    settings, kwargs = collector_settings(config)[name]
    why = await blocked_reason(state, name, settings, now, scheduled=scheduled)
    if why:
        (logger.debug if why == "not due yet" else logger.info)(f"collector {name}: skipped ({why})")
        return None
    last = await state.last_run_started(name)
    report = await run_collectors(state, [build(name, **kwargs)], since=last)
    logger.info(f"collector {name}: {'; '.join(report.lines())}")
    return report


async def run_due_collectors(state, config: PulseConfig, now: datetime | None = None,
                             build=get_collector) -> list[IngestReport]:
    """Every enabled, due, within-budget collector, once. Never raises."""
    reports = []
    for name in collector_settings(config):
        try:
            report = await run_collector(state, config, name, scheduled=True, now=now, build=build)
        except Exception as exc:
            logger.error(f"collector {name} failed to run: {exc}", exc_info=True)
            continue
        if report is not None:
            reports.append(report)
    return reports
