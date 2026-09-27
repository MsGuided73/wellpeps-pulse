"""End-to-end: fixture ingest -> triage -> escalation (Slack via MockTransport)
-> draft -> compliance filter -> adversarial review. FakeBrains stand in for
Claude; nothing touches the network or the `claude` CLI."""

import json
from datetime import datetime

import httpx
import pytest
import pytest_asyncio

from harvey.agents.drafter import Drafter
from harvey.agents.reviewer import Reviewer
from harvey.agents.triager import Triager, triage_batch
from harvey.collectors.fixture import FixtureCollector
from harvey.config import PulseConfig
from harvey.drafting import draft_batch
from harvey.escalation import escalate
from harvey.ingest import run_collectors
from harvey.models import AuditEventType, MentionStatus, ReviewVerdict
from harvey.notify import SlackNotifier
from harvey.state import StateManager
from tests.test_triager import FakeBrain, _answer

NOW = datetime(2026, 9, 27, 12, 0)

# Markers that identify fixture posts (tests/fixtures/mentions/sample.jsonl).
ADVERSE = "emergency room last night"
LEGAL = "talking to my lawyer"
PRIVACY = "shared my health info"
FRAUD = "Two unauthorized charges"
VIRAL = "Do NOT sign up for WellPeps"
CLEAN_Q = "Is WellPeps legit?"            # drafted cleanly, reviewer passes
HYPE_Q = "anyone tried compounded tirzepatide"  # draft says "clinically proven"
NOCLAIM_Q = "Switching from Henry Meds"   # draft cites no claim ids
REJECT_Q = "daily tadalafil through an online service"  # reviewer rejects
SEVERE = {ADVERSE: "adverse_event", LEGAL: "legal", PRIVACY: "privacy", FRAUD: "billing_fraud"}
DRAFTED = {CLEAN_Q, HYPE_Q, NOCLAIM_Q, REJECT_Q}

CLEAN_REPLY = (
    "Disclosure: I work with WellPeps, so I am not neutral. A few things worth checking with any "
    "provider are whether you are actually reviewed by a licensed clinician, what follow-up is "
    "included, how dose adjustments are handled, and which pharmacy dispenses the medication."
)
CLEAN_IDS = ["CLM-R3-DISCLOSURE", "CLM-R7-PROVIDER-CHECKLIST"]


def _triage_brain() -> FakeBrain:
    neg = {"sentiment": -0.8, "sentiment_label": "negative"}
    no = {"reply_appropriate": False}
    return FakeBrain({
        ADVERSE: [_answer(category="adverse_event", urgency="urgent", subject_type="product", **neg, **no)],
        LEGAL: [_answer(category="legal_regulatory", urgency="urgent", **neg, **no)],
        PRIVACY: [_answer(category="privacy", urgency="urgent", **neg, **no)],
        FRAUD: [_answer(category="billing_fraud", urgency="urgent", **neg, **no)],
        VIRAL: [_answer(category="complaint", urgency="urgent", urgency_reason="viral", **neg, **no)],
        "U10 squad": [_answer(relevant=False, subject_type="none", category="other", **no)],
        "WELLPEPS token": [_answer(relevant=False, subject_type="none", category="other", **no)],
        CLEAN_Q: [_answer(category="question", urgency="normal", sentiment=0.0, sentiment_label="neutral")],
        HYPE_Q: [_answer(category="purchase_intent", subject_type="category", urgency="normal",
                         product="compounded tirzepatide", sentiment=0.0, sentiment_label="neutral")],
        NOCLAIM_Q: [_answer(category="purchase_intent", subject_type="competitor", competitor="Henry Meds",
                            urgency="normal", sentiment=-0.2, sentiment_label="negative")],
        REJECT_Q: [_answer(category="question", urgency="normal", sentiment=0.0, sentiment_label="neutral")],
        # Everything else: relevant chatter the team files but doesn't reply to.
        "": [_answer(category="other", urgency="normal", subject_type="competitor", **no)],
    })


def _drafter_brain() -> FakeBrain:
    return FakeBrain({
        CLEAN_Q: [{"reply": CLEAN_REPLY, "claim_ids": CLEAN_IDS, "rationale": "education",
                   "needs_human_reason": None}],
        HYPE_Q: [{"reply": "Compounded options through licensed providers are clinically proven.",
                  "claim_ids": ["CLM-R15-COMPOUNDED-DISCLOSURE"], "rationale": "x",
                  "needs_human_reason": None}],
        NOCLAIM_Q: [{"reply": "Happy to share what to look for in a provider.", "claim_ids": [],
                     "rationale": "x", "needs_human_reason": None}],
        REJECT_Q: [{"reply": CLEAN_REPLY, "claim_ids": CLEAN_IDS, "rationale": "education",
                    "needs_human_reason": None}],
    }, model="sonnet")


def _reviewer_brain() -> FakeBrain:
    return FakeBrain({
        CLEAN_Q: [{"verdict": "pass", "reasons": []}],
        REJECT_Q: [{"verdict": "reject", "reasons": [
            {"rule_id": "R5", "explanation": "reply adds nothing to a medication-specific thread"}]}],
    }, model="sonnet")


class Slack:
    def __init__(self):
        self.payloads: list[dict] = []
        self.notifier = SlackNotifier("https://hooks.slack.example.invalid/services/E2E",
                                      transport=httpx.MockTransport(self._handle))

    def _handle(self, request):
        self.payloads.append(json.loads(request.content))
        return httpx.Response(200)


@pytest_asyncio.fixture
async def state(tmp_path):
    sm = StateManager(str(tmp_path / "pulse.db"))
    await sm.init_db()
    yield sm


async def _by_marker(state) -> dict[str, object]:
    mentions = []
    for status in MentionStatus:
        mentions += await state.list_mentions(status=status, limit=500)
    return {m.text: m for m in mentions}


def _find(mentions: dict, marker: str):
    hits = [m for text, m in mentions.items() if marker in text]
    assert len(hits) == 1, marker
    return hits[0]


@pytest.mark.asyncio
async def test_full_pipeline_on_the_sample_fixture(state):
    config = PulseConfig(escalation={"clinical_owner": "Clinical Lead", "backup_owner": "Backup Lead"})
    slack = Slack()

    async def hook(mention, triage):
        return await escalate(state, slack.notifier, mention, triage, config, now=NOW)

    # 1. Ingest
    ingest = await run_collectors(state, [FixtureCollector()])
    assert ingest.created > 0

    # 2. Triage + escalation
    triage_brain = _triage_brain()
    t_report = await triage_batch(state, Triager(triage_brain), limit=500, escalate=hook)
    assert t_report.errors == 0
    assert t_report.processed == ingest.created

    # 3. Draft -> filter -> review
    reviewer_brain = _reviewer_brain()
    d_report = await draft_batch(state, Drafter(_drafter_brain()), Reviewer(reviewer_brain), limit=50)
    assert d_report.errors == 0
    assert d_report.processed == len(DRAFTED)

    mentions = await _by_marker(state)

    # Statuses
    for marker in SEVERE:
        assert _find(mentions, marker).status is MentionStatus.ESCALATED, marker
    assert _find(mentions, VIRAL).status is MentionStatus.TRIAGED
    for marker in DRAFTED:
        assert _find(mentions, marker).status is MentionStatus.IN_REVIEW, marker
    assert _find(mentions, "U10 squad").status is MentionStatus.DROPPED
    statuses = {m.status for m in mentions.values()}
    assert MentionStatus.APPROVED not in statuses and MentionStatus.POSTED not in statuses
    assert MentionStatus.NEW not in statuses

    # Escalations: one per severe post (plus the viral negative), all paged
    open_escalations = await state.list_open_escalations()
    by_mention = {e.mention_id: e for e in open_escalations}
    for marker, kind in SEVERE.items():
        esc = by_mention[_find(mentions, marker).id]
        assert esc.kind == kind and esc.notified_at == NOW
    assert by_mention[_find(mentions, ADVERSE).id].owner == "Clinical Lead"
    assert by_mention[_find(mentions, VIRAL).id].kind == "viral_negative"
    assert len(open_escalations) == len(SEVERE) + 1
    assert len(slack.payloads) == len(open_escalations)

    # Slack payloads: no post text, no handles
    raw = json.dumps(slack.payloads)
    for mention in mentions.values():
        if mention.id in by_mention:
            assert mention.url in raw
            assert mention.author_handle.lstrip("@u/") not in raw
            for chunk in (mention.text[:40], mention.text[-40:]):
                assert chunk not in raw
    for fragment in ("emergency room", "lawyer", "health info", "unauthorized", "ghosted"):
        assert fragment not in raw

    # Drafts: tiers and verdicts
    clean = await state.get_latest_draft(_find(mentions, CLEAN_Q).id)
    assert (clean.tier, clean.review_verdict, clean.claim_ids) == ("green", ReviewVerdict.PASS, CLEAN_IDS)
    hype = await state.get_latest_draft(_find(mentions, HYPE_Q).id)
    assert (hype.tier, hype.review_verdict) == ("red", ReviewVerdict.REJECT)
    assert any("R14" in h for h in hype.filter_hits)
    noclaim = await state.get_latest_draft(_find(mentions, NOCLAIM_Q).id)
    assert (noclaim.tier, noclaim.review_verdict) == ("red", ReviewVerdict.REJECT)
    assert any("CLAIMS" in h for h in noclaim.filter_hits)
    rejected = await state.get_latest_draft(_find(mentions, REJECT_Q).id)
    assert (rejected.tier, rejected.review_verdict) == ("green", ReviewVerdict.REJECT)
    assert rejected.review_reasons == ["R5: reply adds nothing to a medication-specific thread"]

    # Reviewer only saw the drafts that passed the filter
    reviewed_texts = " ".join(c["prompt"] for c in reviewer_brain.calls)
    assert len(reviewer_brain.calls) == 2
    assert "licensed providers are clinically proven" not in reviewed_texts
    assert "Happy to share what to look for" not in reviewed_texts

    # No drafts for anything else
    for text, mention in mentions.items():
        if not any(marker in text for marker in DRAFTED):
            assert await state.get_latest_draft(mention.id) is None, text

    # Audit trail per mention, in order
    E = AuditEventType
    for text, mention in mentions.items():
        events = [e.event for e in await state.list_audit(mention.id)]
        assert events[:2] == [E.COLLECTED, E.TRIAGED], text
        if mention.id in by_mention:
            assert events == [E.COLLECTED, E.TRIAGED, E.ESCALATED], text
        elif any(marker in text for marker in (HYPE_Q, NOCLAIM_Q)):
            # Red first draft -> one redraft (the fake repeats itself) -> still red.
            assert events == [E.COLLECTED, E.TRIAGED, E.DRAFTED, E.FILTERED,
                              E.DRAFTED, E.FILTERED, E.REVIEWED], text
        elif any(marker in text for marker in DRAFTED):
            assert events == [E.COLLECTED, E.TRIAGED, E.DRAFTED, E.FILTERED, E.REVIEWED], text
        else:
            assert events == [E.COLLECTED, E.TRIAGED], text
        assert E.APPROVED not in events and E.POSTED not in events
