"""WellPeps Pulse CLI: run, dashboard, status, ingest, usage.

Installed as both `pulse` and `harvey` (same entry point).
"""

import argparse
import asyncio
import sys


def cmd_run(args):
    """Start the heartbeat loop."""
    from harvey.main import main

    main()


def cmd_dashboard(args):
    """Launch the local web dashboard (loopback only)."""
    from harvey.dashboard import start_dashboard

    if not 1 <= args.port <= 65535:
        print(f"  Invalid port: {args.port}. Must be 1-65535.")
        sys.exit(2)

    start_dashboard(port=args.port)


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

    asyncio.run(_status())


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

    asyncio.run(_usage())


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

    asyncio.run(_ingest())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="WellPeps Pulse: social listening with human-reviewed replies.",
    )
    subparsers = parser.add_subparsers(dest="command")

    sub = subparsers.add_parser("run", help="Start the heartbeat loop")
    sub.set_defaults(func=cmd_run)

    sub = subparsers.add_parser("dashboard", help="Open the local web dashboard (127.0.0.1)")
    sub.add_argument("--port", type=int, default=5555, help="Port (default: 5555)")
    sub.set_defaults(func=cmd_dashboard)

    sub = subparsers.add_parser("status", help="Show mention counts by status")
    sub.set_defaults(func=cmd_status)

    sub = subparsers.add_parser("ingest", help="Run collectors once and store new mentions")
    sub.add_argument(
        "--fixture", nargs="?", const="", default=None, metavar="DIR",
        help="Replay JSONL fixture posts (default dir: tests/fixtures/mentions)",
    )
    sub.set_defaults(func=cmd_ingest)

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
