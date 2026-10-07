"""draft_batch under the rules of engagement: approved boundary replies,
community rules, stop rules, and the guidance handed to the drafter.
Stub drafter/reviewer; no Claude calls."""

import shutil
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
import yaml

from harvey import knowledge
from harvey.agents.drafter import DraftProposal
from harvey.agents.reviewer import ReviewResult
from harvey.drafting import draft_batch
from harvey.models import (
    AuditEvent,
    AuditEventType,
    Category,
    Draft,
    Mention,
    MentionStatus,
    Platform,
    ReviewVerdict,
    Triage,
)
from harvey.paths import PROJECT_ROOT
from harvey.state import StateManager

NOW = datetime.now().replace(microsecond=0) - timedelta(hours=1)
WP = "wellpeps"
CLAIMS = knowledge.claims_by_id


class StubDrafter:
    def __init__(self, reply="", claim_ids=()):
        self.reply, self.claim_ids = reply, list(claim_ids)
        self.calls: list[dict] = []

    async def draft(self, mention, triage, feedback=None, max_calls=None, guidance=None):
        self.calls.append({"mention": mention.id, "guidance": guidance})
        return DraftProposal(reply=self.reply, claim_ids=self.claim_ids, model="sonnet",
                             offered_claim_ids=self.claim_ids,
                             needs_human_reason=None if self.reply else "no claim fits")


class StubReviewer:
    def __init__(self):
        self.calls = []

    async def review(self, reply, platform, mention, claims, guidance=None):
        self.calls.append({"mention": mention.id, "guidance": guidance})
        return ReviewResult(verdict=ReviewVerdict.PASS, reasons=[], model="sonnet")


@pytest_asyncio.fixture
async def state(tmp_path):
    sm = StateManager(str(tmp_path / "pulse.db"))
    await sm.init_db()
    yield sm


_N = iter(range(1, 10_000))


async def _mention(state, *, category=Category.QUESTION, subtype="", subject_type=WP, reply=True,
                   url=None, author="poster", text="a question", product="", drug="") -> int:
    n = next(_N)
    mid, _ = await state.upsert_mention(Mention(
        platform=Platform.REDDIT, external_id=f"e{n}", author_handle=author,
        url=url or f"https://www.reddit.com/r/testsub{n}/comments/p{n}/", text=text,
        collected_at=NOW - timedelta(minutes=500 - n),
    ))
    await state.save_triage(Triage(mention_id=mid, relevant=True, category=category, subtype=subtype,
                                   subject_type=subject_type, reply_appropriate=reply, product=product,
                                   drug=drug))
    await state.set_mention_status(mid, MentionStatus.TRIAGED)
    return mid


async def _approved_reply(state, *, url, author="poster", text="I work with WellPeps. Hello.",
                          claim_ids=("CLM-AMG-04-WORK-WITH",), when=None) -> int:
    """A past WellPeps reply that a human approved (the reply history)."""
    mid = await _mention(state, url=url, author=author)
    did = await state.add_draft(Draft(mention_id=mid, text=text, claim_ids=list(claim_ids)))
    for status in (MentionStatus.DRAFTED, MentionStatus.IN_REVIEW, MentionStatus.APPROVED):
        await state.set_mention_status(mid, status)
    await state.append_audit(AuditEvent(mention_id=mid, draft_id=did, event=AuditEventType.APPROVED,
                                        actor="reviewer@x", at=when or NOW))
    return mid


def _boundary(situation_claim: str, disclosure: str = "CLM-AMG-04-WORK-WITH") -> str:
    return f"{CLAIMS()[disclosure].text} {CLAIMS()[situation_claim].text}"


# --- Approved boundary replies (no model call) --------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("triage, claim, disclosure, clinical", [
    ({"subtype": "dose_question"}, "CLM-AMG-06-DOSE", "CLM-AMG-04-WORK-WITH", False),
    ({"subtype": "lab_question"}, "CLM-AMG-06-LABS", "CLM-AMG-04-WORK-WITH", False),
    ({"subtype": "medication_change"}, "CLM-AMG-06-STOP-CHANGE", "CLM-AMG-04-WORK-WITH", False),
    ({"subtype": "individual_treatment"}, "CLM-AMG-25-TEMPLATE-B", "CLM-AMG-04-WORK-WITH", False),
    ({"subtype": "personal_medical_info"}, "CLM-AMG-08-POSTED-DETAILS", "CLM-AMG-04-WORK-WITH", False),
    ({"category": Category.ADVERSE_EVENT}, "CLM-AMG-10-REACTION", "CLM-AMG-04-WORK-WITH", True),
    ({"category": Category.ADVERSE_EVENT, "subtype": "medication_change"}, "CLM-AMG-10-DOSE-CHANGE",
     "CLM-AMG-04-WORK-WITH", True),
    ({"subtype": "emergency"}, "CLM-AMG-11-EMERGENCY", "CLM-AMG-04-WORK-WITH", True),
    ({"subtype": "media_inquiry"}, "CLM-AMG-23-MEDIA", "CLM-AMG-04-WORK-WITH", False),
    ({"category": Category.COMPLAINT}, "CLM-AMG-APPX-COMPLAINT", "CLM-AMG-04-TEAM", False),
    ({"category": Category.BILLING_FRAUD}, "CLM-AMG-09-BILLING", "CLM-AMG-04-TEAM", False),
    ({"subtype": "competitor_comparison"}, "CLM-AMG-13-BETTER", None, False),
    ({"subtype": "results_question", "product": "Compounded Semaglutide"}, "CLM-AMG-07-WEIGHT",
     "CLM-AMG-04-WORK-WITH", False),
])
async def test_boundary_situations_get_the_verbatim_approved_reply(state, triage, claim, disclosure, clinical):
    mid = await _mention(state, reply=False, **triage)
    drafter, reviewer = StubDrafter(), StubReviewer()

    report = await draft_batch(state, drafter, reviewer)

    assert drafter.calls == [] and reviewer.calls == []          # no model calls at all
    assert report.processed == 1 and report.drafted == 1
    draft = await state.get_latest_draft(mid)
    if disclosure is None:  # the approved response discloses itself ("I work with WellPeps, so...")
        assert draft.text == CLAIMS()[claim].text and draft.claim_ids == [claim]
    else:
        assert draft.text == _boundary(claim, disclosure)
        assert draft.claim_ids == [disclosure, claim]
    assert draft.model == "approved-response"
    assert draft.review_verdict is ReviewVerdict.NEEDS_HUMAN
    assert any("approved response" in r for r in draft.review_reasons)
    assert any("clinical" in r.lower() for r in draft.review_reasons) is clinical
    assert draft.tier != "red"
    assert (await state.get_mention(mid)).status is MentionStatus.IN_REVIEW


@pytest.mark.asyncio
async def test_complaint_reply_carries_the_finalize_warning(state):
    mid = await _mention(state, category=Category.COMPLAINT, reply=False)
    await draft_batch(state, StubDrafter(), StubReviewer())
    draft = await state.get_latest_draft(mid)
    assert draft.tier == "yellow"
    assert any(h.startswith("R42") and "support" in h for h in draft.filter_hits)


# --- Community rules ----------------------------------------------------------------------------


@pytest.fixture
def config_copy(tmp_path, monkeypatch):
    """A writable copy of config/ for one test (``edit(name, fn)`` changes a file)."""
    target = tmp_path / "cfg"
    shutil.copytree(PROJECT_ROOT / "config", target)

    def edit(name, fn):
        path = target / name
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        fn(data)
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        knowledge.reload()

    monkeypatch.setenv("PULSE_CONFIG_DIR", str(target))
    knowledge.reload()
    yield edit
    monkeypatch.delenv("PULSE_CONFIG_DIR")
    knowledge.reload()


def _community(cid, **kw):
    platform, name = cid.split(":", 1)
    entry = {"id": cid, "platform": platform, "name": name, "brand_participation": "unknown",
             "links_allowed": "unknown", "permission_obtained": False, "rules_checked_at": None}
    entry.update(kw)
    return entry


@pytest.mark.asyncio
async def test_prohibited_community_gets_no_reply_and_stays_triaged(state, config_copy):
    config_copy("communities.yaml", lambda d: d["communities"].append(
        _community("reddit:r/nobrands", brand_participation="prohibited", rules_checked_at="2026-10-01")))
    mid = await _mention(state, subtype="general_education",
                         url="https://www.reddit.com/r/NoBrands/comments/abc/")
    drafter = StubDrafter("I work with WellPeps. Hello there.", ["CLM-AMG-04-WORK-WITH"])

    report = await draft_batch(state, drafter, StubReviewer())

    assert drafter.calls == [] and report.skipped == 1
    assert (await state.get_mention(mid)).status is MentionStatus.TRIAGED
    assert await state.get_latest_draft(mid) is None
    skipped = [e for e in await state.list_audit(mid) if e.event is AuditEventType.SKIPPED]
    assert len(skipped) == 1 and "prohibited" in skipped[0].verdict["reason"]
    assert await state.list_draftable_mentions() == []            # not retried every cycle


@pytest.mark.asyncio
async def test_unknown_community_draft_is_yellow_and_the_drafter_is_told_no_link(state):
    reply = ("I work with WellPeps. In general, telehealth programs use licensed healthcare providers to "
             "review a person's health information and determine whether treatment is appropriate.")
    mid = await _mention(state, subtype="general_education",
                         url="https://www.reddit.com/r/SomeNewSub/comments/xyz/")
    drafter = StubDrafter(reply, ["CLM-AMG-04-WORK-WITH", "CLM-AMG-17-TELEHEALTH"])

    await draft_batch(state, drafter, StubReviewer())

    guidance = drafter.calls[0]["guidance"]
    assert guidance.allow_link is False and "unverified" in guidance.link_note
    assert guidance.template == "A" and guidance.situation_id == "general_education"
    draft = await state.get_latest_draft(mid)
    assert draft.tier == "yellow"
    assert any("community rules unverified" in h for h in draft.filter_hits)


@pytest.mark.asyncio
async def test_allowed_community_with_links_gets_a_clean_context(state, config_copy):
    config_copy("communities.yaml", lambda d: d["communities"].append(
        _community("reddit:r/friendly", brand_participation="allowed", links_allowed=True,
                   rules_checked_at="2026-10-01")))
    await _mention(state, subtype="general_education", url="https://www.reddit.com/r/friendly/comments/q1/")
    drafter = StubDrafter("I work with WellPeps. Thanks for asking, happy to explain how it works in "
                          "general terms.", ["CLM-AMG-04-WORK-WITH"])
    await draft_batch(state, drafter, StubReviewer())
    guidance = drafter.calls[0]["guidance"]
    assert guidance.allow_link is True and guidance.education_only is False


# --- 80/20 -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_over_eighty_twenty_is_not_a_per_reply_quota(state, config_copy):
    """Protocol §1 / Operations Manual §2.1: the 80/20 guideline is a planning
    principle, not a per-reply quota. A community over the share neither
    narrows the drafter nor flags the draft; the review desk shows the share."""
    config_copy("communities.yaml", lambda d: d["communities"].append(
        _community("reddit:r/busy", brand_participation="allowed", links_allowed=True,
                   rules_checked_at="2026-10-01")))
    for i in range(3):  # 1 promotional of 3 = 33% > 20%
        await _approved_reply(state, url=f"https://www.reddit.com/r/busy/comments/old{i}/",
                              author=f"a{i}",
                              text=("I work with WellPeps. Get started with a free guide." if i == 0
                                    else "I work with WellPeps. Results vary by individual."))
    promo = "I work with WellPeps. You can get started with our free guide, it explains the general process."
    mid = await _mention(state, subtype="general_education", url="https://www.reddit.com/r/busy/comments/new1/")
    drafter = StubDrafter(promo, ["CLM-AMG-04-WORK-WITH"])

    await draft_batch(state, drafter, StubReviewer())

    guidance = drafter.calls[0]["guidance"]
    assert guidance.education_only is False and guidance.allow_link is True
    draft = await state.get_latest_draft(mid)
    assert not any(h.startswith("R45") or "80/20" in h for h in draft.filter_hits)
    from harvey import engagement

    info = await engagement.engagement_info(state, await state.get_mention(mid), await state.get_triage(mid), draft)
    assert info["mix"]["over"] is True and info["mix"]["promotional"] == 1


# --- Stop rules (Guide §22) -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_prior_wellpeps_replies_in_a_thread_stop_the_conversation(state):
    thread = "https://www.reddit.com/r/glp/comments/t1/"
    await _approved_reply(state, url=thread + "c1/", author="u1")
    await _approved_reply(state, url=thread + "c2/", author="u2")
    mid = await _mention(state, subtype="general_education", url=thread + "c3/", author="u3")
    drafter = StubDrafter("I work with WellPeps. Hello.", ["CLM-AMG-04-WORK-WITH"])

    await draft_batch(state, drafter, StubReviewer())

    assert drafter.calls == []
    draft = await state.get_latest_draft(mid)
    assert draft.text == "" and draft.review_verdict is ReviewVerdict.NEEDS_HUMAN
    assert any("already replied 2 times" in r for r in draft.review_reasons)
    assert (await state.get_mention(mid)).status is MentionStatus.IN_REVIEW


@pytest.mark.asyncio
async def test_repeated_dose_question_after_a_boundary_reply_gets_the_graceful_close(state):
    thread = "https://www.reddit.com/r/glp/comments/t2/"
    await _approved_reply(state, url=thread + "c1/", author="asker",
                          text=_boundary("CLM-AMG-06-DOSE"),
                          claim_ids=("CLM-AMG-04-WORK-WITH", "CLM-AMG-06-DOSE"))
    mid = await _mention(state, subtype="dose_question", url=thread + "c2/", author="Asker", reply=False)

    await draft_batch(state, StubDrafter(), StubReviewer())

    draft = await state.get_latest_draft(mid)
    assert draft.text == _boundary("CLM-AMG-22-CLOSE")
    assert draft.review_verdict is ReviewVerdict.NEEDS_HUMAN
    assert any("graceful close" in r for r in draft.review_reasons)


@pytest.mark.asyncio
async def test_abusive_thread_gets_no_reply_and_needs_a_human(state):
    mid = await _mention(state, category=Category.COMPLAINT, subtype="abusive")
    drafter = StubDrafter("I work with WellPeps. Sorry.", ["CLM-AMG-04-WORK-WITH"])
    await draft_batch(state, drafter, StubReviewer())
    assert drafter.calls == []
    draft = await state.get_latest_draft(mid)
    assert draft.text == "" and any("stop responding" in r for r in draft.review_reasons)


# --- Persona -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_identified_employee_persona_opens_with_the_name(state, config_copy):
    config_copy("engagement_guide.yaml", lambda d: d["engagement"].update(
        persona="identified_employee", display_name="Dana"))
    mid = await _mention(state, subtype="dose_question", reply=False)
    await draft_batch(state, StubDrafter(), StubReviewer())
    draft = await state.get_latest_draft(mid)
    assert draft.text.startswith("Hi, I'm Dana — I work with WellPeps. ")
    assert draft.tier != "red"


@pytest.mark.asyncio
async def test_model_drafts_get_situation_guidance_and_the_reviewer_sees_it(state):
    mid = await _mention(state, category=Category.MISINFORMATION, subtype="misinformation_about_wellpeps")
    reply = CLAIMS()["CLM-AMG-21-NOT-AUTOMATIC"].text
    drafter, reviewer = StubDrafter(reply, ["CLM-AMG-21-NOT-AUTOMATIC"]), StubReviewer()

    await draft_batch(state, drafter, reviewer)

    guidance = drafter.calls[0]["guidance"]
    assert guidance.template == "D" and "CLM-AMG-21-NOT-AUTOMATIC" in guidance.preferred_claims
    assert guidance.disclosure == "I work with WellPeps."
    assert reviewer.calls[0]["guidance"].situation_id == "misinformation_about_wellpeps"
    assert (await state.get_latest_draft(mid)).review_verdict is ReviewVerdict.PASS
