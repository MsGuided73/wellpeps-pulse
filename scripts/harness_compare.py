"""Compare drafts with the context harness off and on (docs/HARNESS-HANDOFF.md §5).

For each mention id: draft with the harness off (today's first-6,000-characters
reference block) and on (the selected library sections), run the compliance
filter (one redraft when red) and the reviewer, and print the selected
sections, both replies, the filter tiers, the verdicts and the VOICE notes.

Safety:
- It works on a COPY of the database, made with sqlite3's backup API from a
  read-only connection. It never opens ``data/live.db`` for writing, and it
  refuses a copy path that is the source or ``data/live.db``.
- It records nothing: it calls ``Drafter.draft``, ``drafting._redraft_if_red``
  and ``drafting._review`` directly (no draft rows, no audit events; the brain
  may count its calls in the copy).
- Real Claude calls go through harvey/brain.py on the owner's subscription
  only: it refuses to start unless ``PULSE_CLAUDE_BILLING=subscription``.

Run it from PowerShell (where claude.exe is on PATH), signed in to `claude`:

    $env:PULSE_CLAUDE_BILLING = "subscription"
    .venv\\Scripts\\python.exe scripts\\harness_compare.py 412 415 418

Options: ``--source`` (default data/live.db), ``--copy-to`` (default a new
temp file). Each mention costs up to 4 drafter and 2 reviewer calls.
"""

import argparse
import asyncio
import os
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harvey import communities, drafting, engagement  # noqa: E402
from harvey.agents.drafter import Drafter  # noqa: E402
from harvey.agents.reviewer import Reviewer  # noqa: E402
from harvey.config import HarnessConfig, load_config  # noqa: E402
from harvey.paths import PROJECT_ROOT  # noqa: E402
from harvey.state import StateManager  # noqa: E402

LIVE_DB = PROJECT_ROOT / "data" / "live.db"
BILLING_ENV = "PULSE_CLAUDE_BILLING"
VOICE_RULE = "VOICE"


class CompareError(Exception):
    """A setting or path that makes the comparison unsafe to run."""


def _same(a: Path, b: Path) -> bool:
    return a.resolve() == b.resolve()


def copy_database(source: Path, target: Path) -> Path:
    """A consistent copy of ``source`` at ``target`` (sqlite3 backup API; the source
    is opened read-only). Refuses to write to the source or to data/live.db."""
    source, target = Path(source), Path(target)
    if not source.is_file():
        raise CompareError(f"source database not found: {source}")
    if _same(target, source) or _same(target, LIVE_DB):
        raise CompareError("refusing to write the copy over the source or data/live.db")
    if target.exists():
        raise CompareError(f"the copy path already exists: {target} (pick a new --copy-to)")
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return target


def default_copy_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(tempfile.mkdtemp(prefix="pulse-harness-")) / f"harness-compare-{stamp}.db"


def _voice_notes(raw: list[dict]) -> list[str]:
    return [r.get("explanation", "") for r in raw or [] if r.get("rule_id") == VOICE_RULE]


async def _one_mode(brain, harness: HarnessConfig, mention, triage, context, guidance) -> dict:
    drafter = Drafter(brain, harness=harness)
    reviewer = Reviewer(brain, harness=harness)
    first = await drafter.draft(mention, triage, guidance=guidance)
    proposal, gate, superseded = await drafting._redraft_if_red(
        drafter, mention, triage, first, drafting.DraftReport(), context, guidance)
    out = {
        "sections": [f"{r['doc_name']} > {r['heading']}" if r["heading"] else r["doc_name"]
                     for r in proposal.context_refs],
        "reply": proposal.reply, "needs_human_reason": proposal.needs_human_reason,
        "redrafted": superseded is not None, "tier": gate.tier if gate else "",
        "hits": [drafting._hit_line(h) for h in gate.hits] if gate else [],
        "verdict": "", "reasons": [], "voice": [],
    }
    if proposal.reply and gate is not None:
        verdict, lines, _, raw = await drafting._review(reviewer, proposal, mention, gate, guidance)
        out.update(verdict=verdict.value, reasons=lines, voice=_voice_notes(raw))
    return out


async def compare(state, brain, mention_ids, harness: HarnessConfig) -> list[dict]:
    """Per mention: {"mention_id", "skipped"} or {"mention_id", "situation", "off", "on"}."""
    await communities.ensure_overlay(state)
    records = await engagement.recent_replies(state)
    off = harness.model_copy(update={"enabled": False})
    on = harness.model_copy(update={"enabled": True})
    results = []
    for mid in mention_ids:
        mention, triage = await state.get_mention(mid), await state.get_triage(mid)
        if mention is None or triage is None:
            results.append({"mention_id": mid, "skipped": "no such mention, or it is not triaged"})
            continue
        situation = engagement.situation_of(triage)
        context = engagement.context_from(mention, records)
        if context.participation == "prohibited":
            results.append({"mention_id": mid, "skipped": "the community prohibits brand participation"})
            continue
        if situation.reply == "boundary_only":
            results.append({"mention_id": mid, "skipped": "boundary-only situation: the approved response is "
                                                          "used verbatim, no drafter"})
            continue
        guidance = engagement.guidance_for(situation, context, triage, mention)
        results.append({
            "mention_id": mid, "platform": mention.platform.value, "situation": situation.label,
            "off": await _one_mode(brain, off, mention, triage, context, guidance),
            "on": await _one_mode(brain, on, mention, triage, context, guidance),
        })
    return results


def _mode_lines(name: str, run: dict) -> list[str]:
    lines = [f"--- harness {name} ---"]
    if name == "ON":
        lines.append(f"sections ({len(run['sections'])}): " + ("; ".join(run["sections"]) or "(none)"))
    else:
        lines.append("reference block: today's (first 6,000 characters of the library)")
    lines.append("reply: " + (run["reply"] or f"(empty: {run['needs_human_reason']})"))
    if run["reply"]:
        lines.append(f"filter: {run['tier'] or '-'}" + (" (after one redraft)" if run["redrafted"] else "")
                     + ("; " + "; ".join(run["hits"]) if run["hits"] else ""))
        lines.append(f"reviewer: {run['verdict'] or '-'}")
        lines.extend(f"  - {r}" for r in run["reasons"])
        lines.append("VOICE notes: " + ("; ".join(run["voice"]) or "(none)"))
    return lines


def format_report(results: list[dict]) -> str:
    out: list[str] = []
    for r in results:
        if "skipped" in r:
            out.append(f"=== Mention {r['mention_id']}: skipped ({r['skipped']}) ===")
            continue
        out.append(f"=== Mention {r['mention_id']} ({r['platform']}; {r['situation']}) ===")
        out.extend(_mode_lines("OFF", r["off"]))
        out.extend(_mode_lines("ON", r["on"]))
        out.append("")
    return "\n".join(out)


def _parse(argv):
    parser = argparse.ArgumentParser(description="Compare drafts with the context harness off and on, "
                                                 "on a copy of the database. Records nothing.")
    parser.add_argument("mention_ids", nargs="+", type=int)
    parser.add_argument("--source", type=Path, default=LIVE_DB, help="database to copy (default data/live.db)")
    parser.add_argument("--copy-to", type=Path, default=None, help="where to put the copy (default: a temp file)")
    return parser.parse_args(argv)


async def _run(args, brain_factory) -> str:
    target = copy_database(args.source, args.copy_to or default_copy_path())
    print(f"Working on a copy: {target}", file=sys.stderr)
    previous = os.environ.get("PULSE_DB_PATH")
    os.environ["PULSE_DB_PATH"] = str(target)     # anything that reads the default path sees the copy
    try:
        state = StateManager(str(target))           # always SQLite at this path, never Postgres
        await state.init_db()
        config = load_config()
        if brain_factory is None:
            from harvey.brain import Brain

            brain = Brain(state, models=config.usage.models)
        else:
            brain = brain_factory(state)
        results = await compare(state, brain, args.mention_ids, config.harness)
        await state.close()
        return format_report(results)
    finally:
        if previous is None:
            os.environ.pop("PULSE_DB_PATH", None)
        else:
            os.environ["PULSE_DB_PATH"] = previous


def main(argv: list[str] | None = None, brain_factory=None) -> int:
    """``brain_factory(state)``: a stand-in brain (tests); None uses Claude on the subscription."""
    args = _parse(argv)
    if brain_factory is None and os.environ.get(BILLING_ENV, "").strip() != "subscription":
        print(f"Refusing to run: set {BILLING_ENV}=subscription so Claude bills the owner's subscription, "
              "never the API key in .env.", file=sys.stderr)
        return 2
    try:
        print(asyncio.run(_run(args, brain_factory)))
    except CompareError as exc:
        print(f"Refusing to run: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
