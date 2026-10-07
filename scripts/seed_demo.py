"""Seed a throwaway database with DEMO DATA for the review dashboard.

    PULSE_DB_PATH=data/demo.db .venv/Scripts/python scripts/seed_demo.py

Ingests tests/fixtures/mentions/sample.jsonl plus ~35 days of synthetic
DEMO posts (fictional demo_user_* handles, r/DemoData links) with a few
spiking terms, across several platforms and competitors, plus a month of
urgent posts. Then it runs the real triage -> escalation -> draft ->
compliance filter -> review pipeline with a deterministic fake brain
(keyword rules, canned replies, jittered sentiment), back-dates the
urgent-history escalations and acknowledges them after scripted delays (one
misses the SLA), banks the language, and builds one daily and one weekly
Pulse brief with a deterministic brief writer. No `claude` calls, no
network, no Slack (escalations show as "not paged"). SQLite only.

DEMO DATA: every classification and draft here is fabricated by keyword
rules, not by a model. Never point this at the real data/pulse.db; the
script refuses to run unless PULSE_DB_PATH is set to a file other than it.

Demo sandbox (``--sandbox``, the default when PULSE_DEMO_SANDBOX is on):

    PULSE_DB_PATH=data/demo.db .venv/Scripts/python scripts/seed_demo.py --sandbox

also rebuilds the local DEMO sandbox (harvey/sandbox; its own SQLite file,
PULSE_SANDBOX_DB_PATH or data/sandbox.db) so every demo mention is a post or
comment in a fictional thread, and its permalink is that sandbox page
(``--base-url``, default http://127.0.0.1:5555; the dashboard rebases it to
whatever host/port it is opened on). It also writes a DEMO config copy
(``--config-dir``, default data/demo-config/) whose claims are marked
approved for demonstration only; run the dashboard with PULSE_CONFIG_DIR
pointing at it to demo the full approve -> post loop. See README "Demo sandbox".
"""

import argparse
import asyncio
import json
import os
import random
import re
import sys
import tempfile
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
    (r"shipping delay|price hike|shortage|stuck|took two days|plateau|rough|creeping|tracking never|"
     r"charged me twice|never answered|backorder", "complaint", -0.6),
    (r"finally quiet|best part|answered in a day|adjusted the plan|clear update|on time|energy is better|"
     r"works fine", "praise", 0.6),
    (r"comparing|talk about|switching", "purchase_intent", 0.1),
]
DRUGS = ["semaglutide", "tirzepatide", "tadalafil", "minoxidil", "BPC-157", "NAD+"]
DRUG_ALIASES = {"tirz": "tirzepatide", "wegovy": "semaglutide"}
COMPETITORS = [(r"\bhims\b", "Hims & Hers"), (r"\bro\b", "Ro"), (r"\bnoom\b", "Noom Med"),
               (r"\bhenry meds\b", "Henry Meds"), (r"\bmochi\b", "Mochi Health"), (r"\beden\b", "Eden"),
               (r"\blifemd\b", "LifeMD")]
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
    "Eden shipping took a week again and the tracking never updated.",
    "Ro charged me twice this month for the same refill, billing is a mess.",
    "Started NAD+ injections through LifeMD, energy is better than expected.",
    "Tadalafil daily from Hims works fine, no side effects so far.",
    "Noom Med support never answered my message about the dose.",
    "Henry Meds refill on backorder at the pharmacy again.",
]
# Urgent DEMO posts spread over the month: their escalations are acknowledged
# after varied delays (one late) so the SLA chart has a history.
URGENT_HISTORY = [  # (days ago, text, minutes to acknowledge)
    (3, "DEMO: vomiting all night after the semaglutide dose increase from WellPeps.", 4),
    (6, "DEMO: my lawyer is reviewing the WellPeps cancellation terms.", 9),
    (9, "DEMO: ended up in the emergency room after my third tirzepatide dose.", 12),
    (13, "DEMO: WellPeps billing looks like fraud to me, three charges this month.", 7),
    (17, "DEMO: vomiting and dizzy since switching pharmacies through WellPeps.", 22),
    (21, "DEMO: talking to a lawyer about the WellPeps refund.", 3),
    (26, "DEMO: emergency room visit after the new tirzepatide vial.", 14),
    (30, "DEMO: bank flagged WellPeps charges as fraud.", 6),
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


_JITTER = random.Random(21)  # deterministic spread around each rule's score

# DEMO triage inputs for the competitor / switching protocol (harvey/protocol.py):
# keyword guesses standing in for the model's intent tags and need.
_INTENTS = (
    (r"\b(?:anyone (?:recommend|have a provider)|alternatives?|switch(?:ing)? (?:from|providers?)|"
     r"looking for (?:somewhere|a (?:new |different )?provider)|suggestions\?)", "alternatives_requested"),
    (r"\b(?:cheaper|better than|vs\.?|compared? to)\b", "comparison_request"),
    (r"\b(?:just (?:needed|wanted) to vent|so frustrating|fed up|sick of)\b", "venting_only"),
    (r"\b(?:severe|emergency|hospital|ER\b|can.t keep (?:water|food) down|chest pain)", "possible_serious_harm"),
    (r"\bwellpeps\b", "wellpeps_question"),
)
_NEEDS = (
    (r"\b(?:respond|responding|answer|messages?|follow[- ]?up|ghost)", "provider_access"),
    (r"\b(?:price|cost|charged|billing|cancel)", "price_clarity"),
    (r"\b(?:shipping|shipment|late|delivery)", "fulfillment"),
)


def _protocol_inputs(text: str) -> dict:
    intents = [tag for pattern, tag in _INTENTS if re.search(pattern, text, re.I)]
    if "wellpeps_question" in intents and "?" not in text:
        intents.remove("wellpeps_question")
    need = next((n for pattern, n in _NEEDS if re.search(pattern, text, re.I)), None)
    if need is None and "alternatives_requested" in intents:
        need = "continuity"
    venting = intents == ["venting_only"]
    return {"intents": intents, "unmet_need": need or "other", "need_clarity": 2 if need else 1,
            "useful_contribution": 0 if venting else (2 if "alternatives_requested" in intents else 1)}


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
    score = round(max(-1.0, min(1.0, score + _JITTER.uniform(-0.3, 0.3))), 2)
    label = "negative" if score < 0 else "positive" if score > 0 else "neutral"
    return {
        "relevant": subject_type != "none", "subject_type": subject_type, "subject": BANNER,
        "competitor": competitor, "product": None, "drug": _drug(text), "category": category,
        "sentiment": score, "sentiment_label": label,
        "urgency": urgency, "urgency_reason": BANNER, "reply_appropriate": reply, "phrases": _phrases(text),
        **_protocol_inputs(text),
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


_GUIDE_CLAIM_RE = re.compile(r"^- \[(CLM-[A-Z0-9-]+)\] (.+)\n  Link for this claim: (\S+)$", re.M)


def _guide_reply(prompt: str) -> dict | None:
    """DEMO sandbox only: the canned reply plus the first offered guide claim
    and its tracked link (the conversion playbook), when one is offered."""
    found = _GUIDE_CLAIM_RE.search(prompt)
    if not found:
        return None
    claim_id, text, url = found.groups()
    return {"reply": f"{DEMO_REPLY} {text} {url}", "claim_ids": [*DEMO_CLAIMS, claim_id], "rationale": BANNER,
            "needs_human_reason": None}


class DemoBrain:
    """Deterministic stand-in for harvey.brain.Brain (think_json only).

    ``guide_links``: questions get the guide claim + link too (sandbox demos,
    where the DEMO config marks every link live)."""

    def __init__(self, guide_links: bool = False):
        self.guide_links = guide_links

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
            guided = _guide_reply(prompt) if self.guide_links and "?" in text else None
            if guided:
                return guided
            return {"reply": DEMO_REPLY, "claim_ids": DEMO_CLAIMS, "rationale": BANNER,
                    "needs_human_reason": None}
        if agent == "reviewer":
            return {"verdict": "pass", "reasons": []}
        if agent == "pulse":
            return _brief_answer(_payload(prompt))
        raise ValueError(f"demo brain has no rule for agent {agent!r}")


DEFAULT_BASE_URL = "http://127.0.0.1:5555"


def _wants_sandbox(argv: list[str]) -> bool:
    """--sandbox, or PULSE_DEMO_SANDBOX on in the environment / .env."""
    from harvey.config import env_setting, parse_bool

    if "--sandbox" in argv:
        return True
    raw = env_setting("PULSE_DEMO_SANDBOX")
    return bool(raw) and parse_bool(raw, "PULSE_DEMO_SANDBOX")


def _check_sandbox_target(sandbox_db, pulse_db: str) -> Path:
    """The sandbox file must not be the real database or the demo Pulse DB."""
    target = Path(sandbox_db).resolve()
    if target in ((ROOT / "data" / "pulse.db").resolve(), Path(pulse_db).resolve()):
        sys.exit("Refusing to seed the sandbox into a Pulse database; use its own file (data/sandbox.db).")
    return target


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
        for text in rng.sample(STEADY, 7):
            posts.append((text, now - timedelta(days=day, hours=rng.uniform(0, 20))))
    posts.append((SPIKES[0], now - timedelta(days=12)))  # one old shipping delay: not "new"
    for start, end, per_spike in ((daily[0], daily[1], 4), (weekly[1] - timedelta(days=3), weekly[1], 3)):
        span = (end - start).total_seconds()
        for text in SPIKES:
            for _ in range(per_spike):
                posts.append((text, start + timedelta(seconds=rng.uniform(0.05, 0.95) * span)))
    mentions = []
    for i, (text, at) in enumerate(sorted(posts, key=lambda p: p[1])):
        platform, url = _demo_platform(i)
        mentions.append(Mention(
            platform=platform, external_id=f"demo-syn-{i:04d}", url=url,
            author_handle=f"demo_user_{rng.randint(1, 400):03d}",
            text=text, posted_at=at, collected_at=min(at + timedelta(minutes=20), now),
        ))
    for i, (days_ago, text, _) in enumerate(URGENT_HISTORY):
        at = now - timedelta(days=days_ago, hours=rng.uniform(1, 8))
        mentions.append(Mention(
            platform=Platform.REDDIT, external_id=f"demo-urg-{i:02d}",
            url=f"https://www.reddit.com/r/DemoData/comments/demourg{i:02d}/",
            author_handle=f"demo_user_{rng.randint(1, 400):03d}", text=text, posted_at=at,
            collected_at=at + timedelta(minutes=10),
        ))
    return mentions


def _demo_platform(i: int):
    """A DEMO platform mix with a matching fake permalink."""
    from harvey.models import Platform

    slot = i % 10
    if slot in (0, 5):
        return Platform.TRUSTPILOT, f"https://www.trustpilot.com/reviews/demo-{i:04d}"
    if slot in (2, 7):
        return Platform.X, f"https://x.com/demo_user/status/{900000 + i}"
    if slot == 4:
        return Platform.INSTAGRAM, f"https://www.instagram.com/p/demo{i:04d}/"
    if slot == 9:
        return Platform.TIKTOK, f"https://www.tiktok.com/@demo_user/video/{700000 + i}"
    return Platform.REDDIT, f"https://www.reddit.com/r/DemoData/comments/demo{i:04d}/"


def _iso(value) -> datetime:
    return datetime.fromisoformat(str(value).replace(" ", "T"))


async def _backdate_escalations(state) -> int:
    """DEMO: move the URGENT_HISTORY escalations back to their post time and
    acknowledge them after the scripted delay (one misses the SLA). Only the
    throwaway demo DB is touched; the audit log is left as written."""
    from harvey.escalation import ack

    delays = {f"demo-urg-{i:02d}": minutes for i, (_, _, minutes) in enumerate(URGENT_HISTORY)}
    async with state.connect() as db:
        async with db.execute(
            "SELECT e.id, e.created_at, e.sla_due_at, m.external_id, m.collected_at FROM escalations e "
            "JOIN mentions m ON m.id = e.mention_id WHERE m.external_id LIKE 'demo-urg-%'"
        ) as cursor:
            rows = [dict(r) for r in await cursor.fetchall()]
    for row in rows:
        created = _iso(row["collected_at"]) + timedelta(minutes=2)
        sla = _iso(row["sla_due_at"]) - _iso(row["created_at"])
        acked = created + timedelta(minutes=delays[row["external_id"]])
        await ack(state, row["id"], "demo.reviewer@wellpeps.test")
        async with state.connect() as db:
            await db.execute(
                "UPDATE escalations SET created_at = ?, sla_due_at = ?, acked_at = ?, breached = ? WHERE id = ?",
                (created.isoformat(), (created + sla).isoformat(), acked.isoformat(), acked > created + sla,
                 row["id"]))
            await db.commit()
    return len(rows)


def _sandbox_fixture_dir(seeder, workdir: Path) -> Path:
    """The fixture JSONL rewritten so each permalink is its sandbox page."""
    from harvey.collectors.fixture import DEFAULT_FIXTURE_DIR

    rows, raw_lines = [], []
    for path in sorted(DEFAULT_FIXTURE_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                raw_lines.append(line)  # the collector skips it, as before
    placed = seeder.place_fixture(rows)
    target = workdir / "sandbox-fixture.jsonl"
    target.write_text("\n".join([json.dumps(r) for r in placed] + raw_lines) + "\n", encoding="utf-8")
    return workdir


def _require_fresh_db(db_path: str) -> None:
    """Existing mentions keep their old permalinks (dedupe), so the sandbox
    seed needs a database without mentions."""
    import sqlite3

    if not Path(db_path).is_file():
        return
    db = sqlite3.connect(db_path)
    try:
        count = db.execute("SELECT COUNT(*) FROM mentions").fetchone()[0]
    except sqlite3.OperationalError:
        count = 0
    finally:
        db.close()
    if count:
        sys.exit(f"Refusing to seed the sandbox into {db_path}: it already has {count} mention(s), which would "
                 "keep their old permalinks. Move it aside (or pick a new PULSE_DB_PATH) and run again.")


class _DemoConfigEnv:
    """Point PULSE_CONFIG_DIR at the DEMO config while the pipeline runs."""

    def __init__(self, directory: Path | None):
        self.directory, self.previous = directory, None

    def __enter__(self):
        from harvey import knowledge

        if self.directory is not None:
            self.previous = os.environ.get("PULSE_CONFIG_DIR")
            os.environ["PULSE_CONFIG_DIR"] = str(self.directory)
            knowledge.reload()
        return self

    def __exit__(self, *exc):
        from harvey import knowledge

        if self.directory is not None:
            if self.previous is None:
                os.environ.pop("PULSE_CONFIG_DIR", None)
            else:
                os.environ["PULSE_CONFIG_DIR"] = self.previous
            knowledge.reload()


async def seed(db_path: str, *, sandbox_db: str | Path | None = None, config_dir: str | Path | None = None,
               base_url: str = DEFAULT_BASE_URL) -> None:
    """Seed ``db_path``; with ``sandbox_db`` also build the DEMO sandbox and DEMO config."""
    if sandbox_db is None:
        await _seed(db_path)
        return
    from harvey.sandbox.demo_config import DEFAULT_DIR, write_demo_config
    from harvey.sandbox.seeding import SandboxSeeder
    from harvey.sandbox.store import SandboxStore

    _require_fresh_db(db_path)
    directory = write_demo_config(config_dir or DEFAULT_DIR)
    store = SandboxStore(sandbox_db)
    store.reset()
    seeder = SandboxSeeder(store, base_url)
    with _DemoConfigEnv(directory), tempfile.TemporaryDirectory() as workdir:
        await _seed(db_path, seeder=seeder, fixture_dir=_sandbox_fixture_dir(seeder, Path(workdir)))
    print(f"  sandbox: {Path(sandbox_db)} ({store.count_threads()} threads); demo config: {directory}")


async def _seed(db_path: str, seeder=None, fixture_dir: Path | None = None) -> None:
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
    brain = DemoBrain(guide_links=seeder is not None)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    tz = config.usage.quiet_hours.timezone

    ingest = await run_collectors(state, [get_collector("fixture", directory=fixture_dir) if fixture_dir
                                          else get_collector("fixture")])
    synthetic = 0
    posts = _synthetic_posts(now, window_for("daily", now, tz), window_for("weekly", now, tz))
    if seeder is not None:  # DEMO sandbox: each post's permalink is its sandbox comment/post
        placed = seeder.place_synthetic(posts)
        posts = [m.model_copy(update={"url": placed[m.external_id]}) for m in posts]
    for mention in posts:
        synthetic += (await state.upsert_mention(mention))[1]
    triage = await triage_batch(
        state, Triager(brain), limit=1000, screen=SafetyScreen(brain),
        escalate=lambda m, t: escalate(state, notifier, m, t, config),
    )
    drafts = await draft_batch(state, Drafter(brain), Reviewer(brain), limit=100)
    history = await _backdate_escalations(state)
    banked = await bank_language(state)
    built = [await build_brief(state, brain, period, config=config, now=now) for period in ("daily", "weekly")]
    print(f"  {BANNER}")
    print(f"  db: {db_path}")
    print(f"  ingested {ingest.created} fixture + {synthetic} synthetic mention(s); triaged "
          f"{triage.processed} ({triage.escalated} escalated, {triage.dropped} dropped); "
          f"drafted {drafts.processed}; banked language from {banked}; "
          f"{history} escalation(s) acknowledged in the history")
    for brief in built:
        print(f"  {brief['period']} brief #{brief['id']}: {brief['headline']}")


def main(argv: list[str] | None = None) -> None:
    from harvey.sandbox import db_path as sandbox_db_path

    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description="Seed DEMO DATA into a throwaway database.")
    parser.add_argument("--sandbox", action="store_true",
                        help="also build the local DEMO sandbox and DEMO config (default with PULSE_DEMO_SANDBOX)")
    parser.add_argument("--sandbox-db", help="sandbox SQLite file (default PULSE_SANDBOX_DB_PATH or data/sandbox.db)")
    parser.add_argument("--config-dir", help="DEMO config copy (default data/demo-config)")
    parser.add_argument("--base-url", default=os.environ.get("PULSE_DEMO_BASE_URL", "").strip() or DEFAULT_BASE_URL,
                        help=f"dashboard base URL for sandbox permalinks (default {DEFAULT_BASE_URL})")
    args = parser.parse_args(argv)
    target = _check_target()
    if not _wants_sandbox(argv):
        asyncio.run(seed(target))
        return
    sandbox_db = _check_sandbox_target(args.sandbox_db or sandbox_db_path(), target)
    asyncio.run(seed(target, sandbox_db=sandbox_db, config_dir=args.config_dir, base_url=args.base_url))


if __name__ == "__main__":
    main()
