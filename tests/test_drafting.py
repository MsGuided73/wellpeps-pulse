"""draft_batch: selection, filter -> reviewer pipeline, stored draft fields,
audit order, statuses, budget. Stub drafter/reviewer; no Claude calls."""

from datetime import datetime, timedelta

import pytest
import pytest_asyncio

from harvey.agents.drafter import DraftProposal
from harvey.agents.reviewer import ReviewResult
from harvey.config import DEFAULT_MODELS, PulseConfig, UsageConfig
from harvey.drafting import DraftReport, draft_batch
from harvey.main import QUIET_HOURS_EXEMPT, apply_quiet_hours, decide_next_action
from harvey.models import (
    AuditEventType,
    Category,
    Mention,
    MentionStatus,
    Platform,
    ReviewVerdict,
    Triage,
    Urgency,
)
from harvey.state import StateManager

NOW = datetime(2026, 9, 27, 12, 0)
CLEAN = (
    "Disclosure: I work with WellPeps, so I am not neutral. A few things worth checking with any "
    "provider are whether you are actually reviewed by a licensed clinician, what follow-up is "
    "included, how dose adjustments are handled, and which pharmacy dispenses the medication."
)
CLEAN_IDS = ["CLM-R3-DISCLOSURE", "CLM-R7-PROVIDER-CHECKLIST"]


class StubDrafter:
    def __init__(self, proposals: dict[str, DraftProposal]):
        self.proposals = proposals
        self.calls: list[int] = []

    async def draft(self, mention, triage, feedback=None, max_calls=None):
        self.calls.append(mention.id)
        for key, proposal in self.proposals.items():
            if key in mention.text:
                return proposal
        raise AssertionError(f"no stub proposal for {mention.text!r}")


class StubReviewer:
    def __init__(self, verdict=ReviewVerdict.PASS, reasons=None, boom: Exception | None = None):
        self.verdict, self.reasons, self.boom = verdict, reasons or [], boom
        self.calls: list[dict] = []

    async def review(self, reply, platform, mention, claims):
        self.calls.append({"reply": reply, "platform": platform, "mention": mention.id,
                           "claims": [c.id for c in claims]})
        if self.boom:
            raise self.boom
        return ReviewResult(verdict=self.verdict, reasons=self.reasons, model="sonnet")


def _proposal(reply=CLEAN, claim_ids=CLEAN_IDS, needs_human_reason=None) -> DraftProposal:
    return DraftProposal(reply=reply, claim_ids=list(claim_ids), rationale="r",
                         needs_human_reason=needs_human_reason, model="sonnet",
                         offered_claim_ids=list(CLEAN_IDS), dropped_claim_ids=[])


@pytest_asyncio.fixture
async def state(tmp_path):
    sm = StateManager(str(tmp_path / "pulse.db"))
    await sm.init_db()
    yield sm


async def _triaged(state, text, n, *, reply=True, category=Category.QUESTION,
                   platform=Platform.REDDIT, status=MentionStatus.TRIAGED) -> int:
    mid, _ = await state.upsert_mention(Mention(
        platform=platform, external_id=f"d{n}", url=f"https://www.reddit.com/r/t/comments/d{n}/",
        text=text, collected_at=NOW - timedelta(minutes=100 - n),
    ))
    await state.save_triage(Triage(mention_id=mid, category=category, reply_appropriate=reply,
                                   urgency=Urgency.NORMAL, subject_type="wellpeps"))
    if status is not MentionStatus.NEW:
        await state.set_mention_status(mid, MentionStatus.TRIAGED)
        if status is not MentionStatus.TRIAGED:
            await state.set_mention_status(mid, status)
    return mid


def _events(audit):
    return [e.event for e in audit]


# --- Selection -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_reply_appropriate_non_severe_triaged_mentions_are_drafted(state):
    good = await _triaged(state, "clean question", 1)
    no_reply = await _triaged(state, "just venting", 2, reply=False)
    severe = await _triaged(state, "clean question but adverse", 3, category=Category.ADVERSE_EVENT)
    new = await _triaged(state, "clean question still new", 4, status=MentionStatus.NEW)
    drafter = StubDrafter({"clean question": _proposal()})

    report = await draft_batch(state, drafter, StubReviewer())

    assert drafter.calls == [good]
    assert isinstance(report, DraftReport) and report.processed == 1
    assert (await state.get_mention(no_reply)).status is MentionStatus.TRIAGED
    assert (await state.get_mention(severe)).status is MentionStatus.TRIAGED
    assert (await state.get_mention(new)).status is MentionStatus.NEW
    assert await state.get_latest_draft(no_reply) is None


@pytest.mark.asyncio
async def test_oldest_first_up_to_limit(state):
    a = await _triaged(state, "clean question a", 1)
    b = await _triaged(state, "clean question b", 2)
    c = await _triaged(state, "clean question c", 3)
    drafter = StubDrafter({"clean question": _proposal()})

    await draft_batch(state, drafter, StubReviewer(), limit=2)

    assert drafter.calls == [a, b]
    assert (await state.get_mention(c)).status is MentionStatus.TRIAGED


@pytest.mark.asyncio
async def test_count_draftable_matches_selection(state):
    await _triaged(state, "clean question", 1)
    await _triaged(state, "venting", 2, reply=False)
    await _triaged(state, "fraud", 3, category=Category.BILLING_FRAUD)

    summary = await state.get_state_summary()

    assert summary["draftable"] == 1


# --- Pipeline ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clean_draft_is_filtered_reviewed_and_queued(state):
    mid = await _triaged(state, "clean question", 1)
    reviewer = StubReviewer(ReviewVerdict.PASS)

    report = await draft_batch(state, StubDrafter({"clean": _proposal()}), reviewer)

    draft = await state.get_latest_draft(mid)
    assert draft.text == CLEAN
    assert draft.claim_ids == CLEAN_IDS
    assert draft.filter_ok is True and draft.tier == "green"
    assert draft.review_verdict is ReviewVerdict.PASS
    assert draft.model == "sonnet"
    assert reviewer.calls[0]["claims"] == CLEAN_IDS
    assert reviewer.calls[0]["platform"] == "reddit"
    assert (await state.get_mention(mid)).status is MentionStatus.IN_REVIEW
    audit = await state.list_audit(mid)
    assert _events(audit) == [AuditEventType.DRAFTED, AuditEventType.FILTERED, AuditEventType.REVIEWED]
    assert all(e.draft_id == draft.id for e in audit)
    assert audit[0].final_text == CLEAN and audit[0].claim_ids == CLEAN_IDS
    assert audit[1].filter_result["tier"] == "green"
    assert audit[2].verdict["verdict"] == "pass"
    assert (report.drafted, report.passed) == (1, 1)


@pytest.mark.asyncio
async def test_red_filter_skips_reviewer_and_rejects(state):
    mid = await _triaged(state, "hype please", 1)
    reviewer = StubReviewer(ReviewVerdict.PASS)
    proposal = _proposal(reply="This program is clinically proven.")

    report = await draft_batch(state, StubDrafter({"hype": proposal}), reviewer)

    assert reviewer.calls == []
    draft = await state.get_latest_draft(mid)
    assert draft.tier == "red" and draft.filter_ok is False
    assert draft.review_verdict is ReviewVerdict.REJECT
    assert any("R14" in hit for hit in draft.filter_hits)
    assert any("R14" in reason for reason in draft.review_reasons)
    audit = await state.list_audit(mid)
    # Red once -> one redraft (the stub repeats itself) -> still red.
    assert _events(audit) == [AuditEventType.DRAFTED, AuditEventType.FILTERED,
                              AuditEventType.DRAFTED, AuditEventType.FILTERED, AuditEventType.REVIEWED]
    assert audit[4].verdict["verdict"] == "reject"
    assert audit[4].verdict["reviewer"] == "skipped: filter red"
    assert audit[1].filter_result["hits"][0]["rule_id"]
    assert (await state.get_mention(mid)).status is MentionStatus.IN_REVIEW
    assert report.filtered_red == 1 and report.rejected == 1


@pytest.mark.asyncio
async def test_no_claim_ids_is_red(state):
    mid = await _triaged(state, "no claims", 1)

    await draft_batch(state, StubDrafter({"no claims": _proposal(claim_ids=[])}), StubReviewer())

    draft = await state.get_latest_draft(mid)
    assert draft.tier == "red"
    assert any("CLAIMS" in hit for hit in draft.filter_hits)


@pytest.mark.asyncio
async def test_yellow_draft_still_goes_to_reviewer(state):
    mid = await _triaged(state, "compare", 1)
    text = CLEAN + " Some people compare providers versus local clinics."
    reviewer = StubReviewer(ReviewVerdict.NEEDS_HUMAN, [{"rule_id": "R10", "explanation": "comparison"}])

    await draft_batch(state, StubDrafter({"compare": _proposal(reply=text)}), reviewer)

    draft = await state.get_latest_draft(mid)
    assert draft.tier == "yellow" and draft.filter_ok is True
    assert len(reviewer.calls) == 1
    assert draft.review_verdict is ReviewVerdict.NEEDS_HUMAN
    assert draft.review_reasons == ["R10: comparison"]


@pytest.mark.asyncio
async def test_reviewer_reject_is_recorded(state):
    mid = await _triaged(state, "clean question", 1)
    reviewer = StubReviewer(ReviewVerdict.REJECT, [{"rule_id": "R5", "explanation": "hostile thread"}])

    report = await draft_batch(state, StubDrafter({"clean": _proposal()}), reviewer)

    draft = await state.get_latest_draft(mid)
    assert draft.review_verdict is ReviewVerdict.REJECT
    assert draft.tier == "green"  # the filter tier is kept; the verdict says reject
    assert report.rejected == 1
    assert (await state.get_mention(mid)).status is MentionStatus.IN_REVIEW


@pytest.mark.asyncio
async def test_empty_reply_is_saved_as_needs_human(state):
    mid = await _triaged(state, "nothing fits", 1)
    reviewer = StubReviewer()
    proposal = _proposal(reply="", claim_ids=[], needs_human_reason="no approved claim fits")

    report = await draft_batch(state, StubDrafter({"nothing fits": proposal}), reviewer)

    assert reviewer.calls == []
    draft = await state.get_latest_draft(mid)
    assert draft.text == ""
    assert draft.review_verdict is ReviewVerdict.NEEDS_HUMAN
    assert draft.review_reasons == ["no approved claim fits"]
    assert (await state.get_mention(mid)).status is MentionStatus.IN_REVIEW
    events = _events(await state.list_audit(mid))
    assert events == [AuditEventType.DRAFTED, AuditEventType.REVIEWED]
    assert report.needs_human == 1


@pytest.mark.asyncio
async def test_reviewer_crash_leaves_mention_triaged_for_retry(state):
    mid = await _triaged(state, "clean question", 1)

    report = await draft_batch(state, StubDrafter({"clean": _proposal()}), StubReviewer(boom=RuntimeError("x")))

    assert report.errors == 1
    assert (await state.get_mention(mid)).status is MentionStatus.TRIAGED
    assert await state.get_latest_draft(mid) is None


@pytest.mark.asyncio
async def test_budget_exhausted_drafts_nothing(state):
    mid = await _triaged(state, "clean question", 1)
    drafter = StubDrafter({"clean": _proposal()})

    report = await draft_batch(state, drafter, StubReviewer(), budget_ok=lambda: False)

    assert report.budget_exhausted is True and drafter.calls == []
    assert (await state.get_mention(mid)).status is MentionStatus.TRIAGED


@pytest.mark.asyncio
async def test_drafting_never_approves_or_posts(state):
    mid = await _triaged(state, "clean question", 1)

    await draft_batch(state, StubDrafter({"clean": _proposal()}), StubReviewer(ReviewVerdict.PASS))

    assert (await state.get_mention(mid)).status is MentionStatus.IN_REVIEW
    events = _events(await state.list_audit(mid))
    assert AuditEventType.APPROVED not in events and AuditEventType.POSTED not in events


# --- Models + heartbeat decision ---------------------------------------------------------


def test_default_models_route_drafter_and_reviewer_to_sonnet():
    assert DEFAULT_MODELS["drafter"] == "sonnet"
    assert DEFAULT_MODELS["reviewer"] == "sonnet"
    assert DEFAULT_MODELS["triager"] == "haiku"


@pytest.mark.parametrize("key", ["reviewer", "reviewer.adversarial"])
def test_reviewer_may_not_run_on_haiku(key):
    with pytest.raises(ValueError):
        UsageConfig(models={key: "claude-haiku-4-5"})


@pytest.mark.asyncio
async def test_decide_prefers_triage_then_draft_then_idle():
    config = PulseConfig()

    both = {"mentions": {"new": 2}, "draftable": 3}
    only_draft = {"mentions": {"new": 0}, "draftable": 3}
    nothing = {"mentions": {"new": 0}, "draftable": 0}

    assert await decide_next_action(None, config, summary=both) == "triage"
    assert await decide_next_action(None, config, summary=only_draft) == "draft"
    assert await decide_next_action(None, config, summary=nothing) == "idle"


def test_draft_respects_quiet_hours():
    assert "draft" not in QUIET_HOURS_EXEMPT
    assert apply_quiet_hours("draft", quiet=True) == "idle"


def test_no_draft_categories_match_triager_severe_set():
    from harvey.agents.triager import SEVERE_CATEGORIES
    from harvey.state import NO_DRAFT_CATEGORIES

    assert set(NO_DRAFT_CATEGORIES) == {c.value for c in SEVERE_CATEGORIES}


@pytest.mark.parametrize("name", ["triage.md", "draft.md", "review.md", "reply_rules.md", "safety_screen.md"])
def test_prompt_files_have_no_cost_or_supplier_data(name):
    import re

    from harvey.paths import PROJECT_ROOT
    from tests.test_knowledge import leak_hits

    text = (PROJECT_ROOT / "prompts" / name).read_text(encoding="utf-8")

    assert leak_hits(text) == []
    assert not re.search(r"(?i)\bcosts?\b|supplier|margin|wholesale|\$\d", text)
