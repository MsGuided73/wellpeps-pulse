"""Seed a throwaway database with DEMO DATA for the review dashboard.

    PULSE_DB_PATH=data/demo.db .venv/Scripts/python scripts/seed_demo.py

Ingests tests/fixtures/mentions/sample.jsonl plus ~35 days of synthetic
DEMO posts (fictional demo_user_* handles, r/DemoData links) with a few
spiking terms, then runs the real triage -> escalation -> draft -> compliance
filter -> review pipeline with a deterministic fake brain (keyword rules,
canned replies), banks the language, and builds one daily and one weekly
Pulse brief with a deterministic brief writer. No `claude` calls, no
network, no Slack (escalations show as "not paged").

DEMO DATA: every classification and draft here is fabricated by keyword
rules, not by a model. Never point this at the real data/pulse.db; the
script refuses to run unless PULSE_DB_PATH is set to a file other than it.
"""

import asyncio
import json
import os
import random
import re
import sys
from datetime import datetime, timedelta, timezone
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
# Tried only when no rule above matches (synthetic trend posts):
# (pattern, category, sentiment score)
TREND_RULES = [
    (r"shipping delay|price hike|shortage|stuck|took two days|plateau|rough|creeping", "complaint", -0.6),
    (r"finally quiet|best part|answered in a day|adjusted the plan|clear update|on time", "praise", 0.6),
    (r"comparing|talk about|switching", "purchase_intent", 0.1),
]
DRUGS = ["semaglutide", "tirzepatide", "tadalafil", "minoxidil", "BPC-157"]
DRUG_ALIASES = {"tirz": "tirzepatide", "wegovy": "semaglutide"}
COMPETITORS = [(r"\bhims\b", "Hims & Hers"), (r"\bro\b", "Ro"), (r"\bnoom\b", "Noom Med"),
               (r"\bhenry meds\b", "Henry Meds"), (r"\bmochi\b", "Mochi Health")]
# Verbatim phrases the demo triage "extracts" when they appear in a post.
PHRASES = ["food noise is finally quiet", "my provider adjusted the plan", "plateau at month four",
           "shedding phase is rough", "took two days to reply", "shipping delay", "stuck in transit",
           "price hike", "compounded tirz shortage", "oral wegovy", "gave a clear update",
           "best part of the program", "answered in a day"]

# Synthetic DEMO posts. STEADY runs through all 35 days; SPIKES cluster in
# the latest daily and weekly brief windows so the trends have something to show.
STEADY = [
    "Week 6 on semaglutide and the food noise is finally quiet.",
    "Hims refill arrived on time, support answered in a day.",
    "Comparing Ro and Noom for GLP-1 coverage, both want a consult first.",
    "WellPeps check-in was quick and my provider adjusted the plan.",
    "Tirzepatide plateau at month four, trying to stay patient.",
    "Minoxidil shedding phase is rough but hanging in there.",
    "Henry Meds support took two days to reply about my refill.",
    "Mochi dietitian sessions are the best part of the program.",
]
SPIKES = [
    "Another shipping delay from Hims, my refill is stuck in transit.",
    "Ro just announced a price hike on the weight program, not happy.",
    "Pharmacy says compounded tirz shortage again, my dose is on hold.",
    "Seeing lots of talk about oral wegovy replacing the weekly shots.",
    "WellPeps had a shipping delay this week but support gave a clear update.",
]
DEMO_REPLY = (
    "Disclosure: I work with WellPeps, so I am not neutral. A few things worth checking with any "
    "provider are whether you are actually reviewed by a licensed clinician, what follow-up is "
    "included, how dose adjustments are handled, and which pharmacy dispenses the medication."
)
DEMO_CLAIMS = ["CLM-R3-DISCLOSURE", "CLM-R7-PROVIDER-CHECKLIST"]


def _mention_text(prompt: str) -> str:
    block = re.search(r"BEGIN_UNTRUSTED_MENTION (\w+)\n(.*)\nEND_UNTRUSTED_MENTION \1", prompt, re.S)
    return block.group(2) if block else ""


def _competitor(text: str) -> str | None:
    return next((name for pattern, name in COMPETITORS if re.search(pattern, text, re.I)), None)


def _drug(text: str) -> str | None:
    low = text.lower()
    drug = next((d for d in DRUGS if d.lower() in low), None)
    return drug or next((canon for alias, canon in DRUG_ALIASES.items() if re.search(rf"\b{alias}\b", low)), None)


def _phrases(text: str) -> list[str]:
    found = (re.search(re.escape(p), text, re.I) for p in PHRASES)
    return [m.group(0) for m in found if m][:5]


def _triage_answer(text: str) -> dict:
    category, urgency, subject_type, reply = "other", "normal", "", False
    score = None
    for pattern, cat, urg, subj, rep in RULES:
        if re.search(pattern, text, re.I):
            category, urgency, subject_type, reply = cat, urg, subj, rep
            break
    else:
        for pattern, cat, sentiment in TREND_RULES:
            if re.search(pattern, text, re.I):
                category, score = cat, sentiment
                break
    competitor = _competitor(text)
    if not subject_type:
        subject_type = ("wellpeps" if "wellpeps" in text.lower() else
                        "competitor" if competitor else "category")
    if score is None:
        score = -0.6 if category in ("complaint", "adverse_event") else 0.6 if category == "praise" else 0.0
    label = "negative" if score < 0 else "positive" if score > 0 else "neutral"
    return {
        "relevant": subject_type != "none", "subject_type": subject_type, "subject": BANNER,
        "competitor": competitor, "product": None, "drug": _drug(text), "category": category,
        "sentiment": score, "sentiment_label": label,
        "urgency": urgency, "urgency_reason": BANNER, "reply_appropriate": reply, "phrases": _phrases(text),
    }


def _payload(prompt: str) -> dict:
    block = re.search(r"BEGIN_UNTRUSTED_AGGREGATES (\w+)\n(.*)\nEND_UNTRUSTED_AGGREGATES \1", prompt, re.S)
    return json.loads(block.group(2)) if block else {}


def _owner(term: str) -> str:
    if "price" in term:
        return "marketing"
    if re.search(r"shortage|compounded|wegovy", term):
        return "compliance"
    return "support" if re.search(r"shipping|transit|refill|support", term) else "product"


def _brief_answer(payload: dict) -> dict:
    """A DEMO brief that only cites numbers copied from the payload."""
    terms = payload.get("emerging_terms", [])
    cards = [{
        "title": f"Look into '{t['term']}'",
        "why": f"'{t['term']}' came up in {t['count']} mentions against {t['baseline_count']} in the "
               f"baseline (velocity {t['velocity']}).",
        "action": "DEMO: read the matching mentions in the Feed and decide whether the FAQ needs a line.",
        "owner_hint": _owner(t["term"]), "urgency": "this_week" if t["new"] else "watch",
        "evidence_terms": [t["term"]],
    } for t in terms[:4]]
    for row in payload.get("share_of_voice", [])[:1]:
        cards.append({
            "title": f"Track {row['subject']} share of voice",
            "why": f"{row['subject']} holds {row['share_pct']}% of brand mentions ({row['delta_pp']} pp change).",
            "action": "DEMO: compare their public messaging with ours; no public comparison copy.",
            "owner_hint": "marketing", "urgency": "this_month", "evidence_terms": [row["subject"]],
        })
    while len(cards) < 3:
        cards.append({"title": "Keep the FAQ current", "why": "DEMO card: thin data this period.",
                      "action": "DEMO: review the top FAQ entries.", "owner_hint": "support",
                      "urgency": "watch", "evidence_terms": []})
    top = terms[0]["term"] if terms else "nothing new"
    return {
        "headline": f"DEMO — '{top}' leads the emerging terms",
        "summary_md": ("**DEMO DATA.** This brief was written by keyword rules, not a model, from "
                       "fabricated posts.\n\n- Emerging terms and share of voice are computed for real "
                       "from the demo mentions\n- Action cards cite only numbers from the tables"),
        "action_cards": cards[:7],
        "watchlist": [t["term"] for t in terms if t["new"]][:5],
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
        if agent == "pulse":
            return _brief_answer(_payload(prompt))
        raise ValueError(f"demo brain has no rule for agent {agent!r}")


def _check_target() -> str:
    target = os.environ.get("PULSE_DB_PATH", "").strip()
    real = (ROOT / "data" / "pulse.db").resolve()
    if not target or Path(target).resolve() == real:
        sys.exit("Refusing to seed: set PULSE_DB_PATH to a throwaway file (not data/pulse.db).")
    return target


def _synthetic_posts(now: datetime, daily: tuple, weekly: tuple) -> list:
    """~35 days of DEMO posts: steady chatter plus spikes in the brief windows."""
    from harvey.models import Mention, Platform

    rng = random.Random(8)  # deterministic
    posts: list[tuple[str, datetime]] = []
    for day in range(1, 36):
        for text in rng.sample(STEADY, 4):
            posts.append((text, now - timedelta(days=day, hours=rng.uniform(0, 20))))
    posts.append((SPIKES[0], now - timedelta(days=12)))  # one old shipping delay: not "new"
    for start, end, per_spike in ((daily[0], daily[1], 4), (weekly[1] - timedelta(days=3), weekly[1], 3)):
        span = (end - start).total_seconds()
        for text in SPIKES:
            for _ in range(per_spike):
                posts.append((text, start + timedelta(seconds=rng.uniform(0.05, 0.95) * span)))
    mentions = []
    for i, (text, at) in enumerate(sorted(posts, key=lambda p: p[1])):
        review = i % 5 == 0
        mentions.append(Mention(
            platform=Platform.TRUSTPILOT if review else Platform.REDDIT,
            external_id=f"demo-syn-{i:04d}",
            url=(f"https://www.trustpilot.com/reviews/demo-{i:04d}" if review
                 else f"https://www.reddit.com/r/DemoData/comments/demo{i:04d}/"),
            author_handle=f"demo_user_{rng.randint(1, 400):03d}",
            text=text, posted_at=at, collected_at=min(at + timedelta(minutes=20), now),
        ))
    return mentions


async def seed(db_path: str) -> None:
    from harvey.agents.drafter import Drafter
    from harvey.agents.reviewer import Reviewer
    from harvey.agents.safety_screen import SafetyScreen
    from harvey.agents.triager import Triager, triage_batch
    from harvey.briefs import build_brief, window_for
    from harvey.collectors import get_collector
    from harvey.config import PulseConfig
    from harvey.drafting import draft_batch
    from harvey.escalation import escalate
    from harvey.ingest import run_collectors
    from harvey.notify import SlackNotifier
    from harvey.state import StateManager
    from harvey.trends import bank_language

    state = StateManager(db_path)
    await state.init_db()
    config = PulseConfig()
    notifier = SlackNotifier(None)  # no webhook: escalations stay "not paged"
    brain = DemoBrain()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    tz = config.usage.quiet_hours.timezone

    ingest = await run_collectors(state, [get_collector("fixture")])
    synthetic = 0
    for mention in _synthetic_posts(now, window_for("daily", now, tz), window_for("weekly", now, tz)):
        synthetic += (await state.upsert_mention(mention))[1]
    triage = await triage_batch(
        state, Triager(brain), limit=1000, screen=SafetyScreen(brain),
        escalate=lambda m, t: escalate(state, notifier, m, t, config),
    )
    drafts = await draft_batch(state, Drafter(brain), Reviewer(brain), limit=100)
    banked = await bank_language(state)
    built = [await build_brief(state, brain, period, config=config, now=now) for period in ("daily", "weekly")]
    print(f"  {BANNER}")
    print(f"  db: {db_path}")
    print(f"  ingested {ingest.created} fixture + {synthetic} synthetic mention(s); triaged "
          f"{triage.processed} ({triage.escalated} escalated, {triage.dropped} dropped); "
          f"drafted {drafts.processed}; banked language from {banked}")
    for brief in built:
        print(f"  {brief['period']} brief #{brief['id']}: {brief['headline']}")


def main() -> None:
    asyncio.run(seed(_check_target()))


if __name__ == "__main__":
    main()
