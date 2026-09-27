"""WellPeps Pulse heartbeat loop.

Each cycle: read the state summary, decide the next action, gate it on
quiet hours (triage is exempt), run it with per-task error isolation, log,
and sleep. Triage checks the Claude budget before every mention. The full
step order lives in docs/PLAN.md §3; later phases add escalation sweeps,
collectors, drafting, and briefs.
"""

import asyncio
import logging
import signal
import sys
from datetime import datetime, time, timedelta

import pytz

from harvey.agents.triager import Triager, triage_batch
from harvey.brain import Brain
from harvey.config import ConfigError, PulseConfig, load_config
from harvey.state import StateManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("harvey")

# Backoff for consecutive failed cycles: 60s, 120s, 240s, ... capped at 15 min
ERROR_BACKOFF_BASE = 60
ERROR_BACKOFF_CAP = 900
# Actions that run even during quiet hours (PLAN.md §3 steps 1-3).
QUIET_HOURS_EXEMPT = frozenset({"triage"})
# Mentions triaged per heartbeat cycle.
TRIAGE_BATCH_LIMIT = 25


def in_quiet_hours(config: PulseConfig) -> bool:
    """Check if we're currently in quiet hours."""
    qh = config.usage.quiet_hours
    tz = pytz.timezone(qh.timezone)
    now = datetime.now(tz).time()
    start = time.fromisoformat(qh.start)
    end = time.fromisoformat(qh.end)

    if start <= end:
        return start <= now <= end
    # Quiet hours cross midnight (e.g., 22:00 - 07:00)
    return now >= start or now <= end


def seconds_until_quiet_hours_end(config: PulseConfig) -> int:
    """Calculate seconds until quiet hours end."""
    qh = config.usage.quiet_hours
    tz = pytz.timezone(qh.timezone)
    now = datetime.now(tz)
    end = time.fromisoformat(qh.end)
    end_today = now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)

    if end_today <= now:
        # End time is tomorrow
        end_today = end_today + timedelta(days=1)

    delta = end_today - now
    return max(int(delta.total_seconds()), 60)


async def decide_next_action(
    brain: Brain | None,
    state: StateManager | None,
    config: PulseConfig,
    summary: dict | None = None,
) -> str:
    """Decide what the heartbeat should do next.

    PLAN.md §3 order: escalation sweep -> due collectors -> triage batch ->
    draft/filter/review -> briefs -> idle. Implemented so far: triage when
    any mention is still ``new``, otherwise idle.
    """
    if summary is None and state is not None:
        summary = await state.get_state_summary()
    new_count = int(((summary or {}).get("mentions") or {}).get("new", 0) or 0)
    if new_count > 0:
        action, reason = "triage", f"{new_count} new mention(s)"
    else:
        action, reason = "idle", "nothing to do"
    logger.info(f"Decision: {action} ({reason})")
    return action


def apply_quiet_hours(action: str, quiet: bool) -> str:
    """During quiet hours only exempt actions run; the rest become idle."""
    if quiet and action not in QUIET_HOURS_EXEMPT:
        return "idle"
    return action


async def _interruptible_sleep(seconds: float, stop_event: asyncio.Event) -> bool:
    """Sleep up to `seconds`, waking immediately on shutdown.

    Returns True if a shutdown was requested during the sleep.
    """
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


def _tasks_for(action: str, state=None, triager=None, budget_ok=None) -> list[tuple[str, object]]:
    """Coroutines to run for an action."""
    if action == "triage" and state is not None and triager is not None:
        return [("triage", triage_batch(
            state, triager, limit=TRIAGE_BATCH_LIMIT, budget_ok=budget_ok,
        ))]
    return []


async def heartbeat(stop_event: asyncio.Event | None = None):
    """Main loop. Wakes up, decides, acts, sleeps. Repeat."""
    if stop_event is None:
        stop_event = asyncio.Event()

    logger.info("=" * 60)
    logger.info("WellPeps Pulse is online.")
    logger.info("=" * 60)

    try:
        config = load_config()
    except (ConfigError, Exception) as e:
        if isinstance(e, (KeyboardInterrupt, asyncio.CancelledError)):
            raise
        logger.error(f"Cannot start — configuration error:\n{e}")
        return
    state = StateManager()
    brain = Brain(state, models=config.usage.models)
    triager = Triager(brain)

    await state.init_db()
    logger.info(f"Database initialized at {state.db_path}.")

    interval = config.usage.heartbeat_interval_minutes * 60
    max_calls = max(int(200 * (config.usage.max_daily_claude_percent / 100)), 1)
    consecutive_errors = 0

    def budget_ok():
        # Real subscription quota when readable, else our own call counter.
        return brain.is_within_budget(
            max_calls, max_percent=config.usage.max_daily_claude_percent
        )

    while not stop_event.is_set():
        try:
            # 1. Decide
            summary = await state.get_state_summary()
            logger.info(
                f"State: {summary['total']} mention(s), "
                f"{summary['open_escalations']} open escalation(s)."
            )
            decided = await decide_next_action(brain, state, config, summary=summary)

            # 2. Quiet hours: triage (and later escalation) still runs.
            action = apply_quiet_hours(decided, in_quiet_hours(config))
            if action != decided:
                logger.info(f"Quiet hours: deferring '{decided}'.")

            # 3. Execute — independent tasks in parallel, errors isolated
            # per task so one failure never takes down the cycle. Triage
            # checks the Claude budget before each mention.
            tasks = _tasks_for(action, state=state, triager=triager, budget_ok=budget_ok)
            results = await asyncio.gather(
                *[t[1] for t in tasks], return_exceptions=True
            )
            for (name, _), result in zip(tasks, results):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                if isinstance(result, Exception):
                    logger.error(f"Task {name} failed: {result}", exc_info=result)
                elif result is not None:
                    logger.info(f"Task {name}: {result}")

            # 4. Log the action (best-effort; never kills the loop)
            try:
                await state.log_action(action_type=action, agent="main")
            except Exception as e:
                logger.warning(f"Failed to log action '{action}': {e}")

            consecutive_errors = 0

            # 5. Sleep until next heartbeat
            logger.info(
                f"Cycle complete. Sleeping for {config.usage.heartbeat_interval_minutes} minutes."
            )
            if await _interruptible_sleep(interval, stop_event):
                break

        except (KeyboardInterrupt, asyncio.CancelledError):
            break
        except Exception as e:
            consecutive_errors += 1
            backoff = min(
                ERROR_BACKOFF_BASE * (2 ** (consecutive_errors - 1)),
                ERROR_BACKOFF_CAP,
            )
            logger.error(
                f"Error in heartbeat (failure #{consecutive_errors}): {e}",
                exc_info=True,
            )
            logger.info(f"Recovering... sleeping {backoff}s before retry.")
            if await _interruptible_sleep(backoff, stop_event):
                break

    logger.info("WellPeps Pulse shutting down.")


async def _run_with_signals():
    """Run the heartbeat with SIGINT/SIGTERM wired to a graceful shutdown."""
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown(sig_name: str):
        if stop_event.is_set():
            logger.info("Second shutdown signal — exiting immediately.")
            sys.exit(1)
        logger.info(f"Received {sig_name}. Finishing current work, then shutting down...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_shutdown, sig.name)
        except (NotImplementedError, RuntimeError):
            # Windows / non-main-thread fallback
            signal.signal(sig, lambda s, f: _request_shutdown(signal.Signals(s).name))

    await heartbeat(stop_event)


def main():
    """Entry point."""
    try:
        asyncio.run(_run_with_signals())
    except KeyboardInterrupt:
        logger.info("Goodbye.")


if __name__ == "__main__":
    main()
