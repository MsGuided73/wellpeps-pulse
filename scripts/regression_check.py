"""Re-check the regression set with the real triage (memo to Derek, 2026-10-22, item 4).

Run before any change to Pulse's instructions (prompts/), model choices or
selection rules, and after it, then compare:

    $env:PULSE_CLAUDE_BILLING = "subscription"
    .venv\\Scripts\\python.exe scripts\\regression_check.py            # every post
    .venv\\Scripts\\python.exe scripts\\regression_check.py R01 R06    # some posts

Each post goes through the same steps as the worker (triage, safety screen when it
applies, the competitor / switching protocol, review gates) on a temporary
database: nothing is written to data/live.db and nothing is posted or sent.
Exit code 1 when any post's handling differs from what the set expects.
Subscription billing only: it refuses to start otherwise.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BILLING_ENV = "PULSE_CLAUDE_BILLING"


async def _observe(state, triager, screen, case, n: int):
    from harvey import engagement, protocol, regression
    from harvey.agents.safety_screen import apply_screen
    from harvey.agents.triager import needs_screen
    from harvey.escalation import escalation_kind
    from harvey.models import Mention, Platform

    mention = Mention(platform=Platform.REDDIT, external_id=f"reg-{n}", title=case.title, text=case.text,
                      url=f"https://www.reddit.com/r/test/comments/reg{n}/")
    mid, _ = await state.upsert_mention(mention)
    mention = await state.get_mention(mid)
    triage = await triager.triage(mention)
    if screen is not None and needs_screen(triage, f"{mention.title}\n{mention.text}"):
        triage = apply_screen(triage, await screen.screen(mention))
    triage = await protocol.apply(state, mention, triage)
    triage = engagement.with_review_gate(triage, mention)
    record = engagement.selection(triage, mention)
    return regression.Observed(decision=record.decision, drafts=engagement.drafts_reply(triage),
                               escalates=escalation_kind(triage) is not None,
                               intents=tuple(triage.intents or ())), record.reason_code


async def main(wanted: list[str]) -> int:
    from harvey import regression
    from harvey.agents.triager import Triager
    from harvey.brain import Brain
    from harvey.config import load_config
    from harvey.main import build_safety_screen
    from harvey.state import StateManager

    cases = [c for c in regression.load() if not wanted or any(c.id.startswith(w) for w in wanted)]
    config = load_config()
    with tempfile.TemporaryDirectory() as tmp:
        state = StateManager(str(Path(tmp) / "regression.db"))
        await state.init_db()
        brain = Brain(state, models=config.usage.models)
        triager, screen = Triager(brain), build_safety_screen(brain, config)
        sem = asyncio.Semaphore(4)

        async def run(n, case):
            async with sem:
                return case, *(await _observe(state, triager, screen, case, n))

        results = await asyncio.gather(*(run(n, c) for n, c in enumerate(cases, 1)))
    failed = 0
    print(f"\n  Regression set: {len(results)} posts\n")
    for case, observed, code in results:
        problems = regression.mismatches(case.expect, observed)
        failed += bool(problems)
        mark = "ok  " if not problems else "DIFF"
        print(f"  {mark} {case.id:<26} {observed.decision:<8} {code:<20} drafts={observed.drafts!s:<5} "
              f"escalates={observed.escalates!s:<5}")
        for p in problems:
            print(f"         - {p}")
    print(f"\n  {len(results) - failed} as expected, {failed} changed.\n")
    return 1 if failed else 0


if __name__ == "__main__":
    if os.environ.get(BILLING_ENV, "").strip().lower() != "subscription":
        print(f"Set {BILLING_ENV}=subscription first: this script never bills the API key.")
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1:])))
