"""WellPeps Pulse heartbeat loop.

Each cycle: sweep escalations (always, quiet hours or not), read the state
summary, decide the next action, gate it on quiet hours (triage is exempt),
run it with per-task error isolation, log, and sleep. While any escalation
is open the sleep is capped at ``usage.urgent_tick_minutes`` so SLA breaches
are re-paged promptly. Triage checks the Claude budget before every mention.
Drafting (draft -> compliance filter -> adversarial review -> in_review)
runs when nothing is waiting for triage, respecting quiet hours and the
budget. When neither has work, a due Pulse brief (daily after
``pulse.daily_brief_hour`` local, weekly from ``pulse.weekly_day``) is built,
also respecting quiet hours and the budget. New triage phrases are banked
into the language bank every cycle (no Claude call). The full step order
lives in docs/PLAN.md §3; a later phase adds real collectors.
"""

import asyncio
import logging
import signal
import sys
from datetime import datetime, time, timedelta

import pytz

from harvey.agents.drafter import Drafter
from harvey.agents.reviewer import Reviewer
from harvey.agents.safety_screen import SafetyScreen
from harvey.agents.triager import Triager, triage_batch
from harvey.brain import Brain
from harvey.briefs import due_periods, run_due_briefs
from harvey.config import ConfigError, PulseConfig, load_config
from harvey.db.postgres import run as run_async
from harvey.drafting import draft_batch
from harvey.escalation import SweepReport, escalate, sweep
from harvey.notify import SlackNotifier
from harvey.state import StateManager
from harvey.trends import bank_language

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
# Mentions drafted (and reviewed) per heartbeat cycle.
DRAFT_BATCH_LIMIT = 10


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
    state: StateManager | None,
    config: PulseConfig,
    summary: dict | None = None,
) -> str:
    """Decide what the heartbeat should do next.

    PLAN.md §3 order: escalation sweep (every cycle, outside this
    decision) -> due collectors -> triage batch -> draft/filter/review ->
    briefs -> idle. Priority: triage when any mention is still ``new``, else
    draft when triaged mentions await a reply, else brief when a Pulse brief
    is due (``summary["briefs_due"]``, computed from state when absent),
    else idle.
    """
    if summary is None and state is not None:
        summary = await state.get_state_summary()
    summary = dict(summary or {})
    new_count = int((summary.get("mentions") or {}).get("new", 0) or 0)
    draftable = int(summary.get("draftable", 0) or 0)
    if new_count > 0:
        action, reason = "triage", f"{new_count} new mention(s)"
    elif draftable > 0:
        action, reason = "draft", f"{draftable} mention(s) awaiting a draft"
    else:
        due = summary.get("briefs_due")
        if due is None and state is not None:
            due = await due_periods(state, config)
        if due:
            action, reason = "brief", f"{', '.join(due)} brief due"
        else:
            action, reason = "idle", "nothing to do"
    logger.info(f"Decision: {action} ({reason})")
    return action


def build_safety_screen(brain, config: PulseConfig) -> SafetyScreen | None:
    """The independent safety screen, unless ``triage.safety_screen`` is off."""
    return SafetyScreen(brain) if config.triage.safety_screen else None


def sleep_seconds(config: PulseConfig, open_escalations: int) -> int:
    """Heartbeat sleep; capped at the urgent tick while escalations are open."""
    minutes = config.usage.heartbeat_interval_minutes
    if open_escalations > 0:
        minutes = min(minutes, config.usage.urgent_tick_minutes)
    return minutes * 60


async def run_sweep(state, notifier, config: PulseConfig) -> SweepReport | None:
    """Escalation sweep for one cycle. Logs and swallows errors."""
    try:
        report = await sweep(state, notifier, config)
    except Exception as e:
        logger.error(f"Escalation sweep failed: {e}", exc_info=True)
        return None
    if report.breached or report.paged or report.failed or report.errors:
        logger.info(f"Escalation sweep: {report}")
    return report


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


def _tasks_for(
    action: str, state=None, triager=None, budget_ok=None, escalate_hook=None,
    drafter=None, reviewer=None, screen=None, brief_runner=None,
) -> list[tuple[str, object]]:
    """Coroutines to run for an action. ``brief_runner`` is a zero-argument
    callable returning the brief coroutine."""
    if action == "triage" and state is not None and triager is not None:
        return [("triage", triage_batch(
            state, triager, limit=TRIAGE_BATCH_LIMIT, budget_ok=budget_ok,
            escalate=escalate_hook, screen=screen,
        ))]
    if action == "draft" and state is not None and drafter is not None and reviewer is not None:
        return [("draft", draft_batch(
            state, drafter, reviewer, limit=DRAFT_BATCH_LIMIT, budget_ok=budget_ok,
        ))]
    if action == "brief" and brief_runner is not None:
        return [("brief", brief_runner())]
    return []


async def run_language_bank(state) -> int:
    """Bank new triage phrases (deterministic, no Claude). Logs and swallows errors."""
    try:
        return await bank_language(state)
    except Exception as e:
        logger.warning(f"Language bank update failed: {e}")
        return 0


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
    screen = build_safety_screen(brain, config)
    drafter = Drafter(brain)
    reviewer = Reviewer(brain)
    notifier = SlackNotifier.from_config(config)

    await state.init_db()
    logger.info(f"Database ready at {state.location}.")

    max_calls = max(int(200 * (config.usage.max_daily_claude_percent / 100)), 1)
    consecutive_errors = 0

    async def escalate_hook(mention, triage):
        return await escalate(state, notifier, mention, triage, config)

    def budget_ok():
        # Real subscription quota when readable, else our own call counter.
        return brain.is_within_budget(
            max_calls, max_percent=config.usage.max_daily_claude_percent
        )

    async def brief_runner():
        built = await run_due_briefs(state, brain, config, notifier=notifier, budget_ok=budget_ok)
        # Log ids and status only; the brief itself lives in the DB.
        return [f"{b['period']} brief #{b['id']} ({b['status']})" for b in built]

    while not stop_event.is_set():
        try:
            # 0. Escalation sweep: every cycle, regardless of quiet hours,
            # budget, or what gets decided below.
            await run_sweep(state, notifier, config)

            # 1. Decide
            summary = await state.get_state_summary()
            logger.info(
                f"State: {summary['total']} mention(s), "
                f"{summary['open_escalations']} open escalation(s)."
            )
            decided = await decide_next_action(state, config, summary=summary)

            # 2. Quiet hours: triage still runs; drafting waits.
            action = apply_quiet_hours(decided, in_quiet_hours(config))
            if action != decided:
                logger.info(f"Quiet hours: deferring '{decided}'.")

            # 3. Execute — independent tasks in parallel, errors isolated
            # per task so one failure never takes down the cycle. Triage
            # and drafting check the Claude budget before each mention.
            tasks = _tasks_for(
                action, state=state, triager=triager, budget_ok=budget_ok,
                escalate_hook=escalate_hook, drafter=drafter, reviewer=reviewer,
                screen=screen, brief_runner=brief_runner,
            )
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

            # 3b. Language bank: new triage phrases (no Claude call).
            await run_language_bank(state)

            # 4. Log the action (best-effort; never kills the loop)
            try:
                await state.log_action(action_type=action, agent="main")
            except Exception as e:
                logger.warning(f"Failed to log action '{action}': {e}")

            consecutive_errors = 0

            # 5. Sleep until next heartbeat (shorter while escalations are open)
            open_now = (await state.get_state_summary())["open_escalations"]
            pause = sleep_seconds(config, open_now)
            logger.info(f"Cycle complete. Sleeping for {pause // 60} minute(s).")
            if await _interruptible_sleep(pause, stop_event):
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
        run_async(_run_with_signals())
    except KeyboardInterrupt:
        logger.info("Goodbye.")


if __name__ == "__main__":
    main()
