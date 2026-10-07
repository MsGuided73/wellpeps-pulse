"""Escalation routing has one source of truth: a severe category always
escalates (status + escalation row), whatever urgency the model gave it.
Also: status transitions for re-escalation, and escalation kinds vs config.
No real Claude calls."""

import pytest
import pytest_asyncio

from harvey.agents.triager import (
    SEVERE_CATEGORIES,
    Triager,
    apply_safety_net,
    route_status,
    triage_batch,
)
from harvey.config import ESCALATION_KINDS
from harvey.escalation import SEVERE_KINDS, all_kinds, escalation_kind
from harvey.models import (
    AuditEventType,
    Category,
    Mention,
    MentionStatus,
    Platform,
    Triage,
    Urgency,
)
from harvey.state import ALLOWED_TRANSITIONS, StateManager
from tests.test_triager import FakeBrain, _answer

# No urgent-override keyword appears in this text.
QUIET_TEXT = "Something about my order from WellPeps worries me a little"


@pytest_asyncio.fixture
async def state(tmp_path):
    sm = StateManager(str(tmp_path / "pulse.db"))
    await sm.init_db()
    yield sm


async def _seed(state, text: str, n: int = 1) -> int:
    mid, created = await state.upsert_mention(Mention(
        platform=Platform.REDDIT, external_id=f"r{n}",
        url=f"https://www.reddit.com/r/test/comments/r{n}/", text=text,
    ))
    assert created
    return mid


# --- Fix 1: severe categories always escalate ----------------------------------------


@pytest.mark.parametrize("category", sorted(c.value for c in SEVERE_CATEGORIES))
@pytest.mark.parametrize("urgency", ["high", "normal", "low"])
def test_safety_net_forces_urgent_and_no_reply_for_severe_categories(category, urgency):
    triage = Triage(mention_id=1, category=Category(category), urgency=Urgency(urgency),
                    urgency_reason="model says so", reply_appropriate=True)

    out = apply_safety_net(triage, QUIET_TEXT)

    assert out.urgency is Urgency.URGENT
    assert out.reply_appropriate is False
    assert out.urgency_reason.startswith("severe_category")
    assert "model says so" in out.urgency_reason
    # An adverse event keeps ``triaged`` for the guide's approved boundary reply
    # (drafted for a clinical approver) and is paged all the same.
    expected = MentionStatus.TRIAGED if category == "adverse_event" else MentionStatus.ESCALATED
    assert route_status(out) is expected


@pytest.mark.parametrize("category", sorted(c.value for c in SEVERE_CATEGORIES))
def test_route_status_escalates_any_severe_kind_even_if_not_urgent(category):
    triage = Triage(mention_id=1, category=Category(category), urgency=Urgency.NORMAL)

    assert escalation_kind(triage) in SEVERE_KINDS
    expected = MentionStatus.TRIAGED if category == "adverse_event" else MentionStatus.ESCALATED
    assert route_status(triage) is expected


def test_viral_negative_stays_triaged():
    triage = Triage(mention_id=1, category=Category.COMPLAINT, urgency=Urgency.URGENT,
                    subject_type="wellpeps")

    assert escalation_kind(triage) == "viral_negative"
    assert route_status(triage) is MentionStatus.TRIAGED


@pytest.mark.asyncio
@pytest.mark.parametrize("category,kind", [
    ("adverse_event", "adverse_event"), ("legal_regulatory", "legal"),
    ("privacy", "privacy"), ("billing_fraud", "billing_fraud"),
])
@pytest.mark.parametrize("urgency", ["high", "normal"])
async def test_batch_severe_category_without_keyword_is_escalated_with_row(state, category, kind, urgency):
    mid = await _seed(state, QUIET_TEXT)
    brain = FakeBrain({"worries me": [_answer(category=category, urgency=urgency,
                                              reply_appropriate=True)]})
    calls = []

    async def escalate(mention, triage):
        from harvey.config import PulseConfig
        from harvey.escalation import escalate as real_escalate

        class Quiet:
            async def send(self, text, blocks=None):
                return True

        calls.append(triage)
        return await real_escalate(state, Quiet(), mention, triage, PulseConfig())

    report = await triage_batch(state, Triager(brain), escalate=escalate)

    assert report.escalated == 1
    # Rules of engagement: an adverse event or billing complaint about
    # WellPeps (subject wellpeps here) is paged AND stays triaged so the
    # guide's approved boundary reply is drafted; legal and privacy leave the
    # reply queue.
    expected = MentionStatus.TRIAGED if kind in ("adverse_event", "billing_fraud") else MentionStatus.ESCALATED
    assert (await state.get_mention(mid)).status is expected
    esc = await state.get_open_escalation(mid)
    assert esc is not None and esc.kind == kind
    saved = await state.get_triage(mid)
    assert saved.urgency is Urgency.URGENT and saved.reply_appropriate is False
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_batch_severe_category_without_hook_still_escalates_and_audits(state):
    mid = await _seed(state, QUIET_TEXT)
    brain = FakeBrain({"worries me": [_answer(category="privacy", urgency="normal")]})

    await triage_batch(state, Triager(brain))

    assert (await state.get_mention(mid)).status is MentionStatus.ESCALATED
    events = [e.event for e in await state.list_audit(mid)]
    assert AuditEventType.ESCALATED in events


# --- Fix 7: re-escalation transitions -----------------------------------------------------


@pytest.mark.parametrize("current", [MentionStatus.TRIAGED, MentionStatus.DRAFTED, MentionStatus.IN_REVIEW])
def test_escalation_is_allowed_from_triaged_drafted_and_in_review(current):
    assert MentionStatus.ESCALATED in ALLOWED_TRANSITIONS[current]


@pytest.mark.asyncio
async def test_triaged_mention_can_be_re_escalated(state):
    mid = await _seed(state, QUIET_TEXT)
    await state.set_mention_status(mid, MentionStatus.TRIAGED)

    await state.set_mention_status(mid, MentionStatus.ESCALATED)

    assert (await state.get_mention(mid)).status is MentionStatus.ESCALATED


@pytest.mark.asyncio
async def test_in_review_mention_can_be_escalated_from_the_review_desk(state):
    mid = await _seed(state, QUIET_TEXT)
    for status in (MentionStatus.TRIAGED, MentionStatus.DRAFTED, MentionStatus.IN_REVIEW):
        await state.set_mention_status(mid, status)

    await state.set_mention_status(mid, MentionStatus.ESCALATED)

    assert (await state.get_mention(mid)).status is MentionStatus.ESCALATED


# --- Fix 8: every produced kind is a configured kind ----------------------------------------


def test_every_escalation_kind_is_in_config():
    assert set(all_kinds()) <= set(ESCALATION_KINDS)
    assert "viral_negative" in all_kinds()
    assert SEVERE_KINDS <= set(all_kinds())


def test_every_category_kind_produced_is_configured():
    for category in Category:
        for urgency in Urgency:
            for subject_type in ("wellpeps", "competitor", ""):
                kind = escalation_kind(Triage(mention_id=1, category=category, urgency=urgency,
                                              subject_type=subject_type))
                assert kind is None or kind in ESCALATION_KINDS
