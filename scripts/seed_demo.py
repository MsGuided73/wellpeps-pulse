"""Seed a throwaway database with DEMO DATA for the review dashboard.

    PULSE_DB_PATH=data/demo.db .venv/Scripts/python scripts/seed_demo.py

Ingests tests/fixtures/mentions/sample.jsonl, then runs the real triage ->
escalation -> draft -> compliance filter -> review pipeline with a
deterministic fake brain (keyword rules, canned replies). No `claude` calls,
no network, no Slack (escalations show as "not paged").

DEMO DATA: every classification and draft here is fabricated by keyword
rules, not by a model. Never point this at the real data/pulse.db; the
script refuses to run unless PULSE_DB_PATH is set to a file other than it.
"""

import asyncio
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BANNER = "DEMO DATA - fabricated by keyword rules, not a model"

# (pattern, category, urgency, subject_type, reply_appropriate)
RULES = [
    (r"emergency room|vomiting", "adverse_event", "urgent", "category", False),
    (r"lawyer|\bAG\b", "legal_regulatory", "urgent", "wellpeps", False),
    (r"health info|HIPAA", "privacy", "urgent", "wellpeps", False),
    (r"unauthorized charges|fraud", "billing_fraud", "urgent", "wellpeps", False),
    (r"U10 squad|token launching", "other", "low", "none", False),
    (r"Do NOT sign up", "complaint", "urgent", "wellpeps", False),
    (r"BPC-157", "misinformation", "normal", "category", False),
    (r"\?", "question", "normal", "wellpeps", True),
    (r"love|legit how it feels|simple", "praise", "low", "wellpeps", False),
]
DRUGS = ["semaglutide", "tirzepatide", "tadalafil", "minoxidil", "BPC-157"]
DEMO_REPLY = (
    "Disclosure: I work with WellPeps, so I am not neutral. A few things worth checking with any "
    "provider are whether you are actually reviewed by a licensed clinician, what follow-up is "
    "included, how dose adjustments are handled, and which pharmacy dispenses the medication."
)
DEMO_CLAIMS = ["CLM-R3-DISCLOSURE", "CLM-R7-PROVIDER-CHECKLIST"]


def _mention_text(prompt: str) -> str:
    block = re.search(r"BEGIN_UNTRUSTED_MENTION (\w+)\n(.*)\nEND_UNTRUSTED_MENTION \1", prompt, re.S)
    return block.group(2) if block else ""


def _triage_answer(text: str) -> dict:
    category, urgency, subject_type, reply = "other", "normal", "competitor", False
    for pattern, cat, urg, subj, rep in RULES:
        if re.search(pattern, text, re.I):
            category, urgency, subject_type, reply = cat, urg, subj, rep
            break
    drug = next((d for d in DRUGS if d.lower() in text.lower()), None)
    return {
        "relevant": subject_type != "none", "subject_type": subject_type, "subject": BANNER,
        "competitor": None, "product": None, "drug": drug, "category": category,
        "sentiment": -0.6 if category in ("complaint", "adverse_event") else 0.0,
        "sentiment_label": "negative" if category in ("complaint", "adverse_event") else "neutral",
        "urgency": urgency, "urgency_reason": BANNER, "reply_appropriate": reply, "phrases": [],
    }


class DemoBrain:
    """Deterministic stand-in for harvey.brain.Brain (think_json only)."""

    def model_for(self, agent: str, task: str) -> str:
        return "demo-fake"

    async def think_json(self, prompt, session_id=None, agent="", task=""):
        text = _mention_text(prompt)
        if agent == "triager":
            return _triage_answer(text)
        if agent == "safety":
            return {"adverse_event": False, "self_harm": False, "minor": bool(re.search(r"\bim 1[0-7]\b", text)),
                    "evidence": ""}
        if agent == "drafter":
            return {"reply": DEMO_REPLY, "claim_ids": DEMO_CLAIMS, "rationale": BANNER,
                    "needs_human_reason": None}
        if agent == "reviewer":
            return {"verdict": "pass", "reasons": []}
        raise ValueError(f"demo brain has no rule for agent {agent!r}")


def _check_target() -> str:
    target = os.environ.get("PULSE_DB_PATH", "").strip()
    real = (ROOT / "data" / "pulse.db").resolve()
    if not target or Path(target).resolve() == real:
        sys.exit("Refusing to seed: set PULSE_DB_PATH to a throwaway file (not data/pulse.db).")
    return target


async def seed(db_path: str) -> None:
    from harvey.agents.drafter import Drafter
    from harvey.agents.reviewer import Reviewer
    from harvey.agents.safety_screen import SafetyScreen
    from harvey.agents.triager import Triager, triage_batch
    from harvey.collectors import get_collector
    from harvey.config import PulseConfig
    from harvey.drafting import draft_batch
    from harvey.escalation import escalate
    from harvey.ingest import run_collectors
    from harvey.notify import SlackNotifier
    from harvey.state import StateManager

    state = StateManager(db_path)
    await state.init_db()
    config = PulseConfig()
    notifier = SlackNotifier(None)  # no webhook: escalations stay "not paged"
    brain = DemoBrain()

    ingest = await run_collectors(state, [get_collector("fixture")])
    triage = await triage_batch(
        state, Triager(brain), limit=100, screen=SafetyScreen(brain),
        escalate=lambda m, t: escalate(state, notifier, m, t, config),
    )
    drafts = await draft_batch(state, Drafter(brain), Reviewer(brain), limit=100)
    print(f"  {BANNER}")
    print(f"  db: {db_path}")
    print(f"  ingested {ingest.created} mention(s); triaged {triage.processed} "
          f"({triage.escalated} escalated, {triage.dropped} dropped); drafted {drafts.processed}")


def main() -> None:
    asyncio.run(seed(_check_target()))


if __name__ == "__main__":
    main()
