"""WellPeps Pulse CLI: run, dashboard, status, health, ingest, usage, escalations,
ack, user, brief, trends.

Installed as both `pulse` and `harvey` (same entry point).
"""

import argparse
import getpass
import sys

from harvey.db.postgres import run as run_async


def cmd_run(args):
    """Start the heartbeat loop."""
    from harvey.main import main

    main()


def cmd_dashboard(args):
    """Launch the web dashboard (127.0.0.1 unless --host and an admin exists)."""
    from harvey import dashboard

    if not 1 <= args.port <= 65535:
        print(f"  Invalid port: {args.port}. Must be 1-65535.")
        sys.exit(2)

    dashboard.start_dashboard(port=args.port, host=args.host)


def _auth_store():
    from harvey.auth import AuthStore
    from harvey.state import StateManager

    async def _open():
        state = StateManager()
        await state.init_db()
        return AuthStore(state)

    return run_async(_open())


def _read_new_password(email: str, what: str) -> str:
    """getpass twice; exits (nothing changed) when they differ."""
    from harvey.auth import MIN_PASSWORD_LENGTH

    password = getpass.getpass(f"  Password for {email} (min {MIN_PASSWORD_LENGTH} chars): ")
    confirm = getpass.getpass("  Confirm password: ")
    if password != confirm:
        print(f"\n  Passwords do not match; {what}.\n")
        sys.exit(1)
    return password


def cmd_user_add(args):
    """Create a dashboard user; the password is read with getpass, twice.

    The user must choose their own password at first sign-in unless
    --no-force-change (e.g. you are creating your own account).
    """
    from harvey.auth import check_new_password

    password = _read_new_password(args.email, "no user created")
    force = not args.no_force_change
    try:
        check_new_password(password, email=args.email)
        store = _auth_store()
        run_async(store.create_user(args.email, password, args.role, name=args.name,
                                    must_change_password=force))
    except ValueError as exc:
        print(f"\n  {exc}; no user created.\n")
        sys.exit(1)
    note = " They must choose a new password at first sign-in." if force else ""
    print(f"\n  User {args.email.strip().lower()} created with role {args.role}.{note}\n")


def cmd_user_reset_password(args):
    """Set a new password (getpass, twice), end every session, force a change."""
    from harvey.auth import check_new_password

    store = _auth_store()
    if run_async(store.get_user(args.email)) is None:
        print(f"\n  No user {args.email}.\n")
        sys.exit(1)
    password = _read_new_password(args.email, "password unchanged")
    force = not args.no_force_change
    try:
        check_new_password(password, email=args.email)
        target = run_async(store.reset_password(args.email, password, by="cli", agent="cli",
                                                must_change=force))
    except (ValueError, LookupError) as exc:
        print(f"\n  {exc}; password unchanged.\n")
        sys.exit(1)
    note = " They must choose a new password at next sign-in." if force else ""
    print(f"\n  Password reset for {target['email']}; their sessions are ended.{note}\n")


def cmd_user_list(args):
    """List dashboard users (never their password hashes)."""
    users = run_async(_auth_store().list_users())
    print("\n  Dashboard users")
    print("  " + "=" * 60)
    if not users:
        print("  No users. Create one with `pulse user add EMAIL --role admin --name NAME`.")
    for user in users:
        state = "active" if user["active"] else "disabled"
        pending = "  (must change password)" if user["must_change_password"] else ""
        print(f"  {user['email']:<32} {user['role']:<10} {state:<9} {user['display_name']}{pending}")
    print()


def cmd_user_disable(args):
    """Disable a user and end their sessions."""
    if not run_async(_auth_store().disable_user(args.email)):
        print(f"\n  No user {args.email}.\n")
        sys.exit(1)
    print(f"\n  User {args.email.strip().lower()} disabled; their sessions are ended.\n")


def cmd_status(args):
    """Show mention counts by status."""
    from harvey.state import StateManager

    async def _status():
        state = StateManager()
        await state.init_db()
        summary = await state.get_state_summary()

        print("\n  WellPeps Pulse Status")
        print("  " + "=" * 40)
        for status, count in summary["mentions"].items():
            print(f"  {status:<22} {count:>6}")
        print("  " + "-" * 40)
        print(f"  {'total mentions':<22} {summary['total']:>6}")
        print(f"  {'open escalations':<22} {summary['open_escalations']:>6}")
        print(f"  {'Claude calls today':<22} {summary['usage_today']:>6}")
        print()

    run_async(_status())


def cmd_usage(args):
    """Show Claude usage: quota gauges, totals, per-agent breakdown."""
    from harvey.state import StateManager

    async def _usage():
        state = StateManager()
        await state.init_db()

        # Live quota (best-effort; undocumented endpoint)
        from harvey.integrations.quota import QuotaClient
        windows = None
        try:
            windows = await QuotaClient().get_utilization()
        except Exception:
            pass

        print("\n  Claude Usage")
        print("  " + "=" * 52)
        if windows:
            labels = {"five_hour": "5-hour window", "seven_day": "Weekly"}
            for key, w in windows.items():
                resets = f"  (resets {w['resets_at']})" if w.get("resets_at") else ""
                print(f"  {labels.get(key, key):<16} {w['utilization']:5.1f}% used{resets}")
        else:
            print("  Quota gauge unavailable (run 'claude login' or check network).")

        totals = await state.usage_totals()
        print()
        print(f"  {'Period':<10} {'Calls':>7} {'Input':>12} {'Output':>10} {'Cache read':>12}")
        for label, key in (("Today", "today"), ("7 days", "week"), ("30 days", "month")):
            t = totals.get(key) or {}
            print(
                f"  {label:<10} {t.get('calls', 0):>7} "
                f"{t.get('input_tokens', 0):>12,} {t.get('output_tokens', 0):>10,} "
                f"{t.get('cache_read_tokens', 0):>12,}"
            )

        by_agent = await state.usage_by_agent(days=args.days)
        if by_agent:
            print(f"\n  By agent (last {args.days} days):")
            for row in by_agent:
                print(
                    f"    {row['agent']:<14} {row['calls']:>5} calls  "
                    f"{row['output_tokens']:>10,} out tokens"
                )

        by_task = await state.usage_by_task(days=args.days)
        if by_task:
            print(f"\n  By task (last {args.days} days):")
            for row in by_task[:10]:
                print(
                    f"    {row['task']:<22} {row['calls']:>5} calls  "
                    f"{row['output_tokens']:>10,} out tokens"
                )
        print(
            "\n  Subscription plans aren't billed per token — these are usage"
            "\n  counts, not costs.\n"
        )

    run_async(_usage())


def cmd_ingest(args):
    """Run collectors once and print what they stored."""
    if args.fixture is None:
        print(
            "\n  Nothing to ingest: pass --fixture [DIR]. "
            "Real collectors arrive in a later phase.\n"
        )
        sys.exit(2)

    from harvey.collectors import get_collector
    from harvey.ingest import run_collectors
    from harvey.state import StateManager

    cfg = {"directory": args.fixture} if args.fixture else {}
    collector = get_collector("fixture", **cfg)

    async def _ingest():
        state = StateManager()
        await state.init_db()
        report = await run_collectors(state, [collector])
        print("\n  Ingest report")
        print("  " + "=" * 52)
        for line in report.lines():
            print(f"  {line}")
        print(f"  total: {report.created} created, {report.duplicates} duplicates\n")

    run_async(_ingest())


def _sla_status(escalation, now) -> str:
    if escalation.breached:
        return "BREACHED"
    if escalation.sla_due_at is None:
        return "no SLA"
    minutes = int((escalation.sla_due_at - now).total_seconds() // 60)
    return f"due in {minutes}m" if minutes >= 0 else f"OVERDUE {-minutes}m"


async def escalation_lines(state, now=None) -> list[str]:
    """One line per open escalation: id, kind, owner, SLA, paging. No post text."""
    from datetime import datetime, timezone

    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    open_escalations = await state.list_open_escalations()
    if not open_escalations:
        return ["No open escalations."]
    lines = []
    for esc in open_escalations:
        mention = await state.get_mention(esc.mention_id)
        paged = "paged" if esc.notified_at else "not paged"
        lines.append(
            f"#{esc.id:<5} {esc.kind:<15} {esc.owner or 'UNASSIGNED':<20} "
            f"{_sla_status(esc, now):<14} {paged:<10} {mention.url if mention else ''}"
        )
    return lines


def cmd_escalations(args):
    """List open escalations with their SLA status."""
    from harvey.state import StateManager

    async def _list():
        state = StateManager()
        await state.init_db()
        print("\n  Open escalations")
        print("  " + "=" * 60)
        for line in await escalation_lines(state):
            print(f"  {line}")
        print()

    run_async(_list())


def cmd_ack(args):
    """Acknowledge an escalation."""
    from harvey.escalation import ack
    from harvey.state import StateManager

    async def _ack():
        state = StateManager()
        await state.init_db()
        if await ack(state, args.id, args.by):
            print(f"\n  Escalation #{args.id} acknowledged by {args.by.strip()}.\n")
        else:
            print(f"\n  Escalation #{args.id} not found or already acknowledged.\n")
            sys.exit(1)

    run_async(_ack())


def _health_config():
    from harvey.config import ConfigFileNotFoundError, PulseConfig, load_config

    try:
        return load_config()
    except ConfigFileNotFoundError:
        return PulseConfig()


def _one_line(exc: BaseException) -> str:
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text[:300]


def cmd_health(args):
    """Container healthcheck: DB reachable (and, with --worker, a fresh heartbeat).

    Prints one line and exits 0 (healthy) or 1 (unhealthy).
    """
    from harvey import health
    from harvey.state import StateManager

    if args.max_age_minutes is not None and args.max_age_minutes < 1:
        print("unhealthy: --max-age-minutes must be at least 1")
        sys.exit(2)

    async def _check() -> tuple[bool, str]:
        config = _health_config()
        state = StateManager()
        try:
            await state.init_db()
            await health.check_database(state)
            if not args.worker:
                return True, f"database reachable ({state.backend})"
            return await health.worker_health(state, config, args.max_age_minutes)
        finally:
            await state.close()

    try:
        ok, reason = run_async(_check())
    except Exception as exc:
        ok, reason = False, f"database check failed: {_one_line(exc)}"
    print(f"{'ok' if ok else 'unhealthy'}: {reason}")
    sys.exit(0 if ok else 1)


def trend_lines(report) -> list[str]:
    """Plain-text trend tables (aggregates only)."""
    lines = [f"{report.mentions} triaged mention(s) from {report.window_start:%Y-%m-%d %H:%M} to "
             f"{report.window_end:%Y-%m-%d %H:%M} UTC ({report.previous_mentions} in the window before)", ""]
    if report.terms:
        lines.append(f"{'Term':<34} {'Count':>6} {'Base':>6} {'Velocity':>9}")
        for t in report.terms:
            lines.append(f"{t.term[:34]:<34} {t.count:>6} {t.baseline_count:>6} {t.velocity:>8.1f}x"
                         f"{'  NEW' if t.is_new else ''}")
    else:
        lines.append("No term reached the minimum count in this window.")
    if report.share_of_voice:
        lines += ["", f"{'Share of voice':<34} {'Count':>6} {'Share':>7} {'Change':>8}"]
        for r in report.share_of_voice:
            lines.append(f"{r['subject'][:34]:<34} {r['count']:>6} {r['share'] * 100:>6.1f}% "
                         f"{r['delta'] * 100:>+7.1f}pp")
    return lines


def brief_lines(brief: dict) -> list[str]:
    """The brief's headline and action cards (no summary tables)."""
    state = "new" if brief.get("created") else "existing"
    lines = [f"{brief['period']} brief #{brief['id']} ({state}, {brief['status']}) for the window from "
             f"{str(brief['window_start'])[:16]} UTC", "", brief["headline"], ""]
    for i, card in enumerate(brief.get("action_cards", []), start=1):
        lines.append(f"{i}. {card['title']}  [{card.get('owner_hint', '')}, {card.get('urgency', '')}]")
    if not brief.get("action_cards"):
        lines.append("No action cards.")
    return lines


def cmd_brief(args):
    """Build the daily/weekly Pulse brief now (one Claude call unless it exists)."""
    from harvey.brain import Brain
    from harvey.briefs import build_brief
    from harvey.config import load_config
    from harvey.notify import SlackNotifier
    from harvey.state import StateManager

    async def _brief():
        config = load_config()
        state = StateManager()
        await state.init_db()
        brief = await build_brief(state, Brain(state, models=config.usage.models), args.period,
                                  config=config, force=args.force,
                                  notifier=SlackNotifier.from_config(config))
        print()
        for line in brief_lines(brief):
            print(f"  {line}")
        print()

    run_async(_brief())


def cmd_trends(args):
    """Print live trends for the last N days (deterministic, no Claude)."""
    from datetime import datetime, timedelta, timezone

    from harvey.config import ConfigFileNotFoundError, PulseConfig, load_config
    from harvey.state import StateManager
    from harvey.trends import bank_language, compute_trends

    if not 1 <= args.days <= 365:
        print("  --days must be between 1 and 365.")
        sys.exit(2)

    async def _trends():
        try:
            config = load_config()
        except ConfigFileNotFoundError:
            config = PulseConfig()
        state = StateManager()
        await state.init_db()
        await bank_language(state)
        end = datetime.now(timezone.utc).replace(tzinfo=None)
        report = await compute_trends(state, end - timedelta(days=args.days), end,
                                      baseline_days=config.pulse.baseline_days,
                                      min_count=config.pulse.min_count, top_n=config.pulse.top_terms)
        print()
        for line in trend_lines(report):
            print(f"  {line}")
        print()

    run_async(_trends())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="WellPeps Pulse: social listening with human-reviewed replies.",
    )
    subparsers = parser.add_subparsers(dest="command")

    sub = subparsers.add_parser("run", help="Start the heartbeat loop")
    sub.set_defaults(func=cmd_run)

    sub = subparsers.add_parser("dashboard", help="Open the web dashboard (default 127.0.0.1)")
    sub.add_argument("--port", type=int, default=5555, help="Port (default: 5555)")
    sub.add_argument(
        "--host", default="127.0.0.1",
        help="Bind address (default 127.0.0.1). Anything else needs an active admin user.",
    )
    sub.set_defaults(func=cmd_dashboard)

    from harvey.auth import ROLES

    user = subparsers.add_parser("user", help="Manage dashboard users")
    user_sub = user.add_subparsers(dest="user_command", required=True)
    sub = user_sub.add_parser("add", help="Create a user (prompts for the password)")
    sub.add_argument("email")
    sub.add_argument("--role", required=True, choices=ROLES)
    sub.add_argument("--name", required=True, help="Display name")
    sub.add_argument("--no-force-change", action="store_true",
                     help="Don't make them choose a new password at first sign-in")
    sub.set_defaults(func=cmd_user_add)
    sub = user_sub.add_parser("reset-password",
                              help="Set a new password (prompts), end their sessions, force a change")
    sub.add_argument("email")
    sub.add_argument("--no-force-change", action="store_true",
                     help="Don't make them choose a new password at next sign-in")
    sub.set_defaults(func=cmd_user_reset_password)
    sub = user_sub.add_parser("list", help="List users")
    sub.set_defaults(func=cmd_user_list)
    sub = user_sub.add_parser("disable", help="Disable a user and end their sessions")
    sub.add_argument("email")
    sub.set_defaults(func=cmd_user_disable)

    sub = subparsers.add_parser("health", help="Healthcheck: database (and worker heartbeat)")
    sub.add_argument("--worker", action="store_true",
                     help="Also require a recent heartbeat from `pulse run`")
    sub.add_argument("--max-age-minutes", type=int, default=None, metavar="N",
                     help="Heartbeat age limit (default: 2 x max(heartbeat, urgent tick) + 10)")
    sub.set_defaults(func=cmd_health)

    sub = subparsers.add_parser("status", help="Show mention counts by status")
    sub.set_defaults(func=cmd_status)

    sub = subparsers.add_parser("ingest", help="Run collectors once and store new mentions")
    sub.add_argument(
        "--fixture", nargs="?", const="", default=None, metavar="DIR",
        help="Replay JSONL fixture posts (default dir: tests/fixtures/mentions)",
    )
    sub.set_defaults(func=cmd_ingest)

    sub = subparsers.add_parser("escalations", help="List open escalations and their SLA status")
    sub.set_defaults(func=cmd_escalations)

    sub = subparsers.add_parser("ack", help="Acknowledge an escalation")
    sub.add_argument("id", type=int, help="Escalation id (see `pulse escalations`)")
    sub.add_argument("--by", required=True, help="Who is taking it")
    sub.set_defaults(func=cmd_ack)

    sub = subparsers.add_parser("brief", help="Build the Pulse brief now (one Claude call)")
    sub.add_argument("--period", choices=("daily", "weekly"), default="daily",
                     help="daily = previous local day, weekly = previous Mon-Sun (default daily)")
    sub.add_argument("--force", action="store_true", help="Rebuild even if this window has a brief")
    sub.set_defaults(func=cmd_brief)

    sub = subparsers.add_parser("trends", help="Print live Pulse trends (no Claude call)")
    sub.add_argument("--days", type=int, default=7, help="Window length in days (default: 7)")
    sub.set_defaults(func=cmd_trends)

    sub = subparsers.add_parser("usage", help="Show Claude usage and quota")
    sub.add_argument("--days", type=int, default=30, help="Breakdown window (default: 30)")
    sub.set_defaults(func=cmd_usage)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\n  Interrupted. Goodbye.")
        sys.exit(130)
    except Exception as e:
        # ConfigError carries actionable messages — show them cleanly
        # instead of a raw traceback.
        from harvey.config import ConfigError

        if isinstance(e, ConfigError):
            print(f"\n  Configuration problem:\n  {e}\n")
        else:
            print(f"\n  Error running '{args.command}': {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
