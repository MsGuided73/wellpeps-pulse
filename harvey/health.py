"""Liveness checks for container healthchecks.

- ``check_database``: a trivial round-trip, plus the schema-version check on
  Postgres. Used by the dashboard's ``GET /healthz`` and ``pulse health``.
- ``record_heartbeat`` / ``worker_health``: the heartbeat loop stamps
  ``settings.heartbeat_at`` (naive UTC ISO) every cycle, idle and quiet-hour
  cycles included; ``pulse health --worker`` fails when the stamp is older
  than the allowed age.
"""

from datetime import datetime, timezone

from harvey.config import PulseConfig
from harvey.db import postgres

HEARTBEAT_KEY = "heartbeat_at"
# Slack on top of two sleep intervals, for a long triage/draft cycle.
MAX_AGE_SLACK_MINUTES = 10


def utcnow() -> datetime:
    """Naive UTC now (patched in tests)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def default_max_age_minutes(config: PulseConfig) -> int:
    usage = config.usage
    return 2 * max(usage.heartbeat_interval_minutes, usage.urgent_tick_minutes) + MAX_AGE_SLACK_MINUTES


async def check_database(state) -> None:
    """Raise unless the database answers (and, on Postgres, has the schema)."""
    from harvey.state import MIGRATIONS

    if state.database_url:
        await postgres.verify_schema(state.database_url, len(MIGRATIONS), state.location)
    async with state.connect() as db:
        async with db.execute("SELECT 1") as cursor:
            row = await cursor.fetchone()
    if row is None or row[0] != 1:
        raise RuntimeError("database round-trip returned no row")


async def record_heartbeat(state, now: datetime | None = None) -> None:
    await state.set_setting(HEARTBEAT_KEY, (now or utcnow()).replace(microsecond=0).isoformat())


async def worker_health(state, config: PulseConfig, max_age_minutes: int | None = None,
                        now: datetime | None = None) -> tuple[bool, str]:
    """(healthy, one-line reason) from the last heartbeat stamp."""
    limit = max_age_minutes if max_age_minutes is not None else default_max_age_minutes(config)
    raw = await state.get_setting(HEARTBEAT_KEY, "")
    if not raw:
        return False, "no heartbeat recorded yet (is `pulse run` running?)"
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return False, "heartbeat timestamp is unreadable"
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None)
    age_minutes = ((now or utcnow()) - stamp).total_seconds() / 60
    if age_minutes > limit:
        return False, (f"heartbeat is stale: last beat {age_minutes:.0f} min ago "
                       f"(limit {limit} min)")
    return True, f"worker heartbeat {max(age_minutes, 0):.0f} min ago (limit {limit} min)"
