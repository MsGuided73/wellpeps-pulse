"""Draft prompt "never write" list and the one-shot redraft after a red
compliance filter. Stub/Fake brains only; no Claude calls."""

import pytest
import pytest_asyncio
from pydantic import ValidationError

from harvey import knowledge
from harvey.agents.drafter import (
    MAX_ATTEMPTS,
    DraftProposal,
    Drafter,
    build_prompt,
    never_write_phrases,
)
from harvey.compliance import compliance_filter
from harvey.drafting import MAX_DRAFTER_CALLS, draft_batch
from harvey.models import AuditEventType, MentionStatus, ReviewVerdict
from harvey.models.knowledge import PatternRule
from tests.test_drafter import _mention, _reply, _triage
from tests.test_drafting import CLEAN, CLEAN_IDS, StubReviewer, _events, _proposal, _triaged
from tests.test_drafting import state  # noqa: F401  (fixture)
from tests.test_triager import FakeBrain

RED = "Disclosure: I work with WellPeps, so I am not neutral. We will look into your account."


# --- (a) never-write list -------------------------------------------------------------


def test_pattern_rule_examples_must_match_their_pattern():
    PatternRule(id="R31", pattern=r"\byour\s+account\b", reason="r", source="s",
                examples=["look into your account"])
    with pytest.raises(ValidationError):
        PatternRule(id="R31", pattern=r"\byour\s+account\b", reason="r", source="s",
                    examples=["totally unrelated"])


def test_every_patient_confirmation_rule_has_examples():
    for rule in knowledge.compliance_rules().patient_confirmation:
        assert rule.examples, rule.pattern


def test_config_examples_are_red_in_the_filter():
    rules = knowledge.compliance_rules()
    for rule in (*rules.patient_confirmation, *rules.prohibited):
        for example in rule.examples:
            gate = compliance_filter(example, "facebook", ["CLM-R3-DISCLOSURE"])
            assert gate.tier == "red", example
            assert any(h.rule_id == rule.id for h in gate.hits), example


def test_never_write_list_has_readable_phrases_not_regex():
    phrases = never_write_phrases()

    assert "look into your account" in " | ".join(phrases)
    assert len(phrases) >= 8
    for phrase in phrases:
        assert "\\" not in phrase and "(?" not in phrase and "[" not in phrase


def test_draft_prompt_includes_the_never_write_list():
    prompt = build_prompt(_mention(), _triage(), [])

    assert "never write" in prompt.lower()
    for phrase in never_write_phrases():
        assert phrase in prompt
    assert "{{never_write}}" not in prompt


# --- Drafter feedback + call cap ------------------------------------------------------


@pytest.mark.asyncio
async def test_drafter_counts_calls():
    brain = FakeBrain({"WellPeps": ["bad", _reply()]})

    proposal = await Drafter(brain).draft(_mention(), _triage())

    assert proposal.calls == 2 and proposal.reply


@pytest.mark.asyncio
async def test_drafter_feedback_is_put_in_the_prompt_and_max_calls_is_honoured():
    brain = FakeBrain({"WellPeps": ["bad"]})

    proposal = await Drafter(brain).draft(
        _mention(), _triage(), feedback=["R31: references the person's account [your account]"],
        max_calls=1)

    assert len(brain.calls) == 1 and proposal.calls == 1
    prompt = brain.calls[0]["prompt"]
    assert "previous draft was blocked because" in prompt
    assert "R31: references the person's account" in prompt
    assert prompt.index("previous draft was blocked") > prompt.index("END_UNTRUSTED_MENTION")


def test_call_cap_is_two():
    assert MAX_DRAFTER_CALLS == 2 == MAX_ATTEMPTS


# --- (b) redraft in draft_batch -----------------------------------------------------------


class SeqDrafter:
    """Returns proposals in order; records the feedback of each call."""

    def __init__(self, *proposals: DraftProposal):
        self.proposals = list(proposals)
        self.feedback: list = []
        self.max_calls: list = []

    async def draft(self, mention, triage, feedback=None, max_calls=None):
        self.feedback.append(feedback)
        self.max_calls.append(max_calls)
        return self.proposals.pop(0)


@pytest.mark.asyncio
async def test_green_first_time_is_not_redrafted(state):
    mid = await _triaged(state, "clean question", 1)
    drafter = SeqDrafter(_proposal())

    await draft_batch(state, drafter, StubReviewer(ReviewVerdict.PASS))

    assert drafter.feedback == [None]
    audit = await state.list_audit(mid)
    assert _events(audit) == [AuditEventType.DRAFTED, AuditEventType.FILTERED, AuditEventType.REVIEWED]
    assert audit[0].verdict["attempt"] == 1 and audit[1].filter_result["attempt"] == 1


@pytest.mark.asyncio
async def test_red_then_green_is_redrafted_once_and_reviewed(state):
    mid = await _triaged(state, "clean question", 1)
    drafter = SeqDrafter(_proposal(reply=RED), _proposal())
    reviewer = StubReviewer(ReviewVerdict.PASS)

    report = await draft_batch(state, drafter, reviewer)

    assert len(drafter.feedback) == 2
    assert drafter.feedback[0] is None
    assert any("R31" in line for line in drafter.feedback[1])
    assert drafter.max_calls[1] == 1
    assert len(reviewer.calls) == 1 and reviewer.calls[0]["reply"] == CLEAN
    latest = await state.get_latest_draft(mid)
    assert latest.version == 2 and latest.text == CLEAN and latest.tier == "green"
    audit = await state.list_audit(mid)
    assert _events(audit) == [
        AuditEventType.DRAFTED, AuditEventType.FILTERED,
        AuditEventType.DRAFTED, AuditEventType.FILTERED, AuditEventType.REVIEWED,
    ]
    assert [audit[0].verdict["attempt"], audit[2].verdict["attempt"]] == [1, 2]
    assert [audit[1].filter_result["attempt"], audit[3].filter_result["attempt"]] == [1, 2]
    assert audit[1].filter_result["tier"] == "red" and audit[3].filter_result["tier"] == "green"
    assert audit[0].final_text == RED and audit[2].final_text == CLEAN
    assert audit[4].draft_id == latest.id
    assert (await state.get_mention(mid)).status is MentionStatus.IN_REVIEW
    assert report.redrafted == 1 and report.passed == 1 and report.filtered_red == 0


@pytest.mark.asyncio
async def test_red_then_red_skips_the_reviewer(state):
    mid = await _triaged(state, "clean question", 1)
    drafter = SeqDrafter(_proposal(reply=RED), _proposal(reply="This is clinically proven."))
    reviewer = StubReviewer(ReviewVerdict.PASS)

    report = await draft_batch(state, drafter, reviewer)

    assert len(drafter.feedback) == 2
    assert reviewer.calls == []
    latest = await state.get_latest_draft(mid)
    assert latest.version == 2 and latest.tier == "red"
    assert latest.review_verdict is ReviewVerdict.REJECT
    audit = await state.list_audit(mid)
    assert _events(audit) == [
        AuditEventType.DRAFTED, AuditEventType.FILTERED,
        AuditEventType.DRAFTED, AuditEventType.FILTERED, AuditEventType.REVIEWED,
    ]
    assert audit[4].verdict["reviewer"] == "skipped: filter red"
    assert (await state.get_mention(mid)).status is MentionStatus.IN_REVIEW
    assert report.redrafted == 1 and report.filtered_red == 1 and report.rejected == 1


@pytest.mark.asyncio
async def test_no_redraft_when_the_first_draft_already_used_both_calls(state):
    await _triaged(state, "clean question", 1)
    first = DraftProposal(reply=RED, claim_ids=list(CLEAN_IDS), model="sonnet", calls=2)
    drafter = SeqDrafter(first)

    await draft_batch(state, drafter, StubReviewer())

    assert len(drafter.feedback) == 1


@pytest.mark.asyncio
async def test_redraft_that_gives_up_goes_to_a_human(state):
    mid = await _triaged(state, "clean question", 1)
    drafter = SeqDrafter(_proposal(reply=RED),
                         _proposal(reply="", claim_ids=[], needs_human_reason="no claim fits"))

    report = await draft_batch(state, drafter, StubReviewer())

    latest = await state.get_latest_draft(mid)
    assert latest.version == 2 and latest.text == ""
    assert latest.review_verdict is ReviewVerdict.NEEDS_HUMAN
    audit = await state.list_audit(mid)
    assert _events(audit) == [
        AuditEventType.DRAFTED, AuditEventType.FILTERED,
        AuditEventType.DRAFTED, AuditEventType.REVIEWED,
    ]
    assert audit[2].verdict["attempt"] == 2
    assert (await state.get_mention(mid)).status is MentionStatus.IN_REVIEW
    assert report.needs_human == 1
