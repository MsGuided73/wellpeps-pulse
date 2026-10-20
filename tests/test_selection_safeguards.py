"""WellPeps guidance of Oct 8-10 2026 and the session safeguards (docs/REVISIONS-LOG.md R-20).

Sources: WellPeps Engagement Goals and Conversation Selection Module V1
(2026-10-10, "Selection Module"), Derek Goldberg's 2026-10-10 decisions, the
Agent Program Gameplan, and the owner's review session (2026-10-20).

- Selection Module SKIP reasons (satisfied provider, cheapest price only,
  already answered, knowledge gap) and HOLD for an unverified central fact.
- A competitor's own community is listen-only (never a reply candidate).
- A medical question or crisis about someone else: no reply, safety watch.
- Posting is held (approval refused) until a community's rules or a
  moderator's permission are recorded; drafting for review is still allowed.
- Activity targets are warnings only; duplicate participation is rechecked.
- The RESPOND / SKIP / HOLD / ROUTE record, checked against the Module's §6
  evaluation cases.
No network, no Claude.
"""

from datetime import datetime, timedelta

import pytest
import pytest_asyncio

from harvey import engagement, knowledge, review
from harvey.agents.safety_screen import ScreenAnswer, ScreenResult, apply_screen
from harvey.agents.triager import Triager, apply_safety_net, triage_batch
from harvey.config import PulseConfig
from harvey.escalation import SAFETY_WATCH, escalation_kind
from harvey.models import Category, Draft, Mention, MentionStatus, Platform, ReviewVerdict, Triage, Urgency
from harvey.state import StateManager
from tests.community_helpers import allow_community
from tests.test_triager import FakeBrain, _answer

REDDIT = "https://www.reddit.com/r/{sub}/comments/{key}/"
NOW = datetime(2026, 10, 20, 12, 0, 0)


def _m(sub: str = "test", key: str = "k1", text: str = "post", **kw) -> Mention:
    return Mention(id=kw.pop("id", 1), platform=Platform.REDDIT, url=REDDIT.format(sub=sub, key=key), text=text,
                   **kw)


def _t(**kw) -> Triage:
    base = {"mention_id": 1, "relevant": True, "reply_appropriate": True, "category": Category.QUESTION}
    base.update(kw)
    return Triage(**base)


@pytest_asyncio.fixture
async def state(tmp_path):
    sm = StateManager(str(tmp_path / "pulse.db"))
    await sm.init_db()
    yield sm


# --- Selection Module SKIP reasons (§2 steps 3-6, §3) ------------------------------------------------


@pytest.mark.parametrize("triage, code", [
    (_t(category=Category.PRAISE, subject_type="competitor", competitor="Ro", intents=["satisfied_provider"]),
     engagement.SKIP_SATISFIED_PROVIDER),
    (_t(category=Category.PRAISE, subtype="competitor_praise", subject_type="competitor", competitor="Ro"),
     engagement.SKIP_SATISFIED_PROVIDER),
    (_t(category=Category.PURCHASE_INTENT, subject_type="category", intents=["price_only", "alternatives_requested"]),
     engagement.SKIP_PRICE_ONLY),
    (_t(subtype="general_education", subject_type="category", intents=["already_answered"]),
     engagement.SKIP_NO_ADDED_VALUE),
    (_t(category=Category.PURCHASE_INTENT, subject_type="category", useful_contribution=0),
     engagement.SKIP_KNOWLEDGE_GAP),
])
def test_selection_skip_reasons_gate_the_draft(triage, code):
    assert engagement.skip_reason(triage) == code
    gated = engagement.with_review_gate(triage)
    assert gated.urgency_reason.endswith(f"{engagement.GATE_NOT_WORTH_IT}:{code}")
    assert engagement.gated(gated) == engagement.GATE_NOT_WORTH_IT
    assert engagement.skip_code(gated) == code
    assert engagement.reply_mode(gated) == "no_reply" and engagement.drafts_reply(gated) is False


@pytest.mark.parametrize("triage", [
    # "I use Ro. What does titration mean in general?": the educational exception.
    _t(subtype="general_education", subject_type="competitor", competitor="Ro",
       intents=["satisfied_provider", "general_information"]),
    # "Money is tight. What extra costs should I check?": affordability, not price-only.
    _t(category=Category.PURCHASE_INTENT, subtype="pricing_question", subject_type="category",
       unmet_need="price_clarity", intents=["general_information"]),
    # Naming a provider says nothing about satisfaction.
    _t(category=Category.PURCHASE_INTENT, subject_type="competitor", competitor="Ro"),
    # A question about WellPeps is never a knowledge-gap skip.
    _t(subtype="process_question", subject_type="wellpeps", useful_contribution=0),
])
def test_openings_are_not_skipped(triage):
    assert engagement.skip_reason(triage) == ""


# --- HOLD an unverified central fact (§2 step 5; §6 "What does WellPeps charge?") ---------------------


def test_a_wellpeps_price_question_holds_while_pricing_is_unverified():
    triage = _t(subtype="pricing_question", subject_type="wellpeps", intents=["wellpeps_question"],
                unmet_need="price_clarity")
    # The real config: pricing is a FINALIZE item, so no direct price fact is publishable.
    assert engagement.unverified_central_fact(triage) == "price_clarity"
    gated = engagement.with_review_gate(triage)
    assert engagement.gated(gated) == engagement.GATE_HOLD_FACT
    assert engagement.drafts_reply(gated) is False
    # Once a direct price fact is approved, the same question is answered.
    direct = knowledge.engagement_guide().switching_protocol.needs["price_clarity"].direct
    assert engagement.unverified_central_fact(triage, publishable=set(direct)) == ""


def test_a_need_with_only_partial_facts_is_answered_not_held():
    triage = _t(subtype="competitor_comparison", subject_type="wellpeps", intents=["wellpeps_question"],
                unmet_need="provider_access")
    assert engagement.unverified_central_fact(triage) == ""


def test_a_general_price_question_not_about_wellpeps_is_not_held():
    triage = _t(category=Category.PURCHASE_INTENT, subtype="pricing_question", subject_type="category",
                unmet_need="price_clarity")
    assert engagement.unverified_central_fact(triage) == ""


# --- A competitor's own community: listen only --------------------------------------------------------


@pytest.mark.parametrize("sub, owner", [("IVIMHEALTH", "Ivím Health"), ("Noom", "Noom Med"),
                                        ("yourmdonline", "YourMD Online"), ("hims", "Hims & Hers")])
def test_a_competitor_community_is_listen_only(sub, owner):
    mention = _m(sub=sub)
    assert engagement.competitor_community(mention) == owner
    triage = _t(category=Category.PURCHASE_INTENT, subject_type="competitor", competitor=owner,
                intents=["alternatives_requested"])
    gated = engagement.with_review_gate(triage, mention)
    assert engagement.gated(gated) == engagement.GATE_COMPETITOR_COMMUNITY
    assert engagement.drafts_reply(gated) is False
    assert owner in engagement.posting_hold(mention)
    assert engagement.selection(triage, mention).reason_code == "COMPETITOR_COMMUNITY"


def test_a_competitor_community_still_routes_safety():
    # Listen-only never suppresses an incident (Selection Module §1).
    triage = _t(category=Category.ADVERSE_EVENT, subject_type="wellpeps", urgency=Urgency.URGENT)
    gated = engagement.with_review_gate(triage, _m(sub="Noom"))
    assert escalation_kind(gated) == "adverse_event"


def test_a_topic_community_is_not_a_competitor_community():
    assert engagement.competitor_community(_m(sub="Semaglutide")) == ""
    assert engagement.competitor_community(None) == ""


# --- A medical question or crisis about someone else --------------------------------------------------


FATHER = "My father is on Ozempic and drinks heavily every night. Is that dangerous?"


def test_triage_tag_blocks_any_reply_and_goes_on_the_safety_watch():
    triage = Triage(mention_id=1, relevant=True, category=Category.QUESTION, subtype="safety_question",
                    subject_type="category", intents=["other_person_medical"], reply_appropriate=True,
                    urgency=Urgency.NORMAL, urgency_reason="model text")
    out = apply_safety_net(triage, FATHER)
    assert out.reply_appropriate is False and out.urgency is Urgency.HIGH
    assert out.urgency_reason == "model text; safety:other_person"
    assert engagement.safety_blocked(out) and engagement.reply_mode(out) == "no_reply"
    assert escalation_kind(out) == SAFETY_WATCH
    # About a WellPeps patient: the clinical owner, still no public reply.
    wellpeps = out.model_copy(update={"subject_type": "wellpeps"})
    assert escalation_kind(wellpeps) == "adverse_event"
    assert engagement.drafts_reply(wellpeps) is False
    assert engagement.selection(out, _m()).decision == engagement.ROUTE


def test_safety_screen_flag_blocks_any_reply():
    triage = Triage(mention_id=1, relevant=True, category=Category.QUESTION, subtype="general_education",
                    reply_appropriate=True, urgency=Urgency.LOW)
    out = apply_screen(triage, ScreenResult(other_person=True, evidence="My father is on Ozempic"))
    assert out.urgency_reason.startswith("safety_screen:other_person")
    assert out.reply_appropriate is False and out.urgency is Urgency.HIGH
    assert engagement.safety_blocked(out) and escalation_kind(out) == SAFETY_WATCH
    # An older screen answer without the field still validates (defaults to False).
    assert ScreenAnswer.model_validate({"adverse_event": False, "self_harm": False, "minor": False}).other_person \
        is False


@pytest.mark.asyncio
async def test_other_person_question_is_never_drafted_end_to_end(state):
    mid, _ = await state.upsert_mention(_m(key="dad", text=FATHER, id=None))
    brain = FakeBrain({"father": [_answer(category="question", subtype="safety_question", subject_type="category",
                                          drug="semaglutide", intents=["other_person_medical"],
                                          urgency="normal", sentiment=-0.4, sentiment_label="negative")]})
    report = await triage_batch(state, Triager(brain))
    assert report.errors == 0
    triage = await state.get_triage(mid)
    assert escalation_kind(triage) == SAFETY_WATCH
    assert (await state.get_mention(mid)).status is MentionStatus.ESCALATED
    assert await state.list_draftable_mentions() == []


# --- Posting permission: a firm block; activity targets: warnings -------------------------------------


@pytest.mark.parametrize("url, held", [
    ("https://www.reddit.com/r/test/comments/k1/", True),                 # unregistered: rules unknown
    ("https://www.reddit.com/r/semaglutide/comments/k1/", True),          # registered, still unknown
    ("https://www.instagram.com/p/abc/", False),                          # no community: platform rules only
])
def test_posting_hold_follows_community_permission(url, held):
    mention = Mention(id=1, platform=Platform.REDDIT, url=url, text="x")
    hold = engagement.posting_hold(mention)
    assert bool(hold) is held
    if held:
        assert engagement.POSTING_HOLD_LABEL in hold


def test_recorded_permission_lifts_the_posting_hold(monkeypatch):
    mention = _m()
    assert engagement.posting_hold(mention)
    allow_community(monkeypatch)
    assert engagement.posting_hold(mention) == ""
    assert engagement.public_permission(mention) == "allowed"


def test_with_permission_and_prohibited_communities_hold_posting(monkeypatch):
    from datetime import date

    from harvey.models.knowledge import CommunityEntry

    original = knowledge.communities
    entries = (
        CommunityEntry(id="reddit:r/perm", platform="reddit", name="r/perm", brand_participation="with_permission",
                       rules_checked_at=date(2026, 10, 1)),
        CommunityEntry(id="reddit:r/nope", platform="reddit", name="r/nope", brand_participation="prohibited"),
    )
    monkeypatch.setattr(knowledge, "communities", lambda: (*original(), *entries))
    assert "moderator/admin permission" in engagement.posting_hold(_m(sub="perm"))
    assert engagement.public_permission(_m(sub="perm")) == "permission_missing"
    assert "prohibits" in engagement.posting_hold(_m(sub="nope"))


async def _review_item(state, sub="test", key="r1", triage=None, text=None, claim_ids=None) -> int:
    claims = knowledge.claims_by_id()
    text = text or f"{claims['CLM-AMG-04-WORK-WITH'].text} {claims['CLM-AMG-26-GENERAL-INFO'].text}"
    mid, _ = await state.upsert_mention(Mention(platform=Platform.REDDIT, url=REDDIT.format(sub=sub, key=key),
                                                external_id=key, text="What should I ask a telehealth provider?"))
    await state.save_triage(triage or Triage(mention_id=mid, relevant=True, category=Category.QUESTION,
                                             subtype="general_education", subject_type="category",
                                             reply_appropriate=True))
    for status in (MentionStatus.TRIAGED, MentionStatus.DRAFTED, MentionStatus.IN_REVIEW):
        await state.set_mention_status(mid, status)
    await state.add_draft(Draft(mention_id=mid, text=text, claim_ids=claim_ids or ["CLM-AMG-04-WORK-WITH",
                                                                                   "CLM-AMG-26-GENERAL-INFO"],
                                model="sonnet", filter_ok=True, tier="yellow",
                                review_verdict=ReviewVerdict.PASS))
    return mid


@pytest.mark.asyncio
async def test_unknown_community_draft_is_held_at_approval_not_at_drafting(state):
    mid = await _review_item(state)
    mention = await state.get_mention(mid)
    # Drafting for review stays allowed: the gate does not stop it.
    assert engagement.review_gate(await state.get_triage(mid), mention) == ""
    with pytest.raises(review.ReviewError) as err:
        await review.approve(state, mid, "reviewer@x", PulseConfig(), role="reviewer")
    assert err.value.status == 409 and engagement.POSTING_HOLD_LABEL in err.value.detail
    detail = await review.detail(state, mid, PulseConfig(), role="reviewer")
    assert any("posting held" in b for b in detail["approval"]["blockers"])
    assert detail["engagement"]["selection"]["public_permission"] == "unknown"
    assert detail["engagement"]["posting_hold"]


@pytest.mark.asyncio
async def test_approval_proceeds_once_the_community_is_allowed(state, monkeypatch):
    allow_community(monkeypatch)
    mid = await _review_item(state)
    await review.approve(state, mid, "reviewer@x", PulseConfig(), role="reviewer")
    assert (await state.get_mention(mid)).status is MentionStatus.APPROVED


@pytest.mark.asyncio
async def test_a_competitor_community_draft_can_never_be_approved(state, monkeypatch):
    allow_community(monkeypatch, "reddit:r/noom")      # even a recorded "allowed" is overridden
    mid = await _review_item(state, sub="noom", key="n1")
    with pytest.raises(review.ReviewError) as err:
        await review.approve(state, mid, "reviewer@x", PulseConfig(), role="reviewer")
    assert "listen-only" in err.value.detail


@pytest.mark.asyncio
async def test_duplicate_participation_is_rechecked_at_approval(state, monkeypatch):
    allow_community(monkeypatch)
    limit = knowledge.engagement_guide().stop_rules.max_wellpeps_replies_per_thread
    # Earlier WellPeps replies in the same thread (same post id, different comments).
    for n in range(limit):
        other = await _review_item(state, key=f"same/c{n}")
        await review.approve(state, other, "reviewer@x", PulseConfig(), role="reviewer")
    mid = await _review_item(state, key="same/c9")
    with pytest.raises(review.ReviewError) as err:
        await review.approve(state, mid, "reviewer@x", PulseConfig(), role="reviewer")
    assert "no pile-ons" in err.value.detail


def _record(n: int, community: str = "reddit:r/test", approved_at=NOW, promo_text="") -> engagement.ReplyRecord:
    return engagement.ReplyRecord(mention_id=n, platform="reddit", status="approved", author=f"a{n}",
                                  text=promo_text or "I work with WellPeps. General info.", claim_ids=(),
                                  link_id="", approved_at=approved_at, community=community, thread=f"t{n}")


def test_activity_targets_are_warnings_not_blockers(monkeypatch):
    records = [_record(n, approved_at=NOW - timedelta(days=1)) for n in range(1, 5)]
    warnings = engagement.activity_warnings(records, "reddit:r/test", exclude=99, now=NOW)
    assert any(w.startswith("activity:") for w in warnings)
    assert engagement.activity_warnings(records, "reddit:r/test", exclude=99, now=NOW + timedelta(days=30)) == []
    assert engagement.activity_warnings(records, "", now=NOW) == []
    # Never a reason to refuse approval.
    allow_community(monkeypatch)
    mention = _m(key="fresh")
    assert review._selection_blockers(mention, _t(), records) == []


# --- The selection record vs the Selection Module §6 evaluation cases -----------------------------------
# "Cases below assume authorized access, verified participation permission and an
# approved answer unless a blocker is stated."


CASES = [
    ("What is the difference between oral and injectable options?",
     dict(subtype="general_education", subject_type="category", unmet_need="treatment_education", need_clarity=2,
          intents=["general_information"]), "RESPOND", "EDUCATION_FIT"),
    ("Happy with Ro. Month three is going great.",
     dict(category=Category.PRAISE, subtype="competitor_praise", subject_type="competitor", competitor="Ro",
          intents=["satisfied_provider"], need_clarity=0, useful_contribution=1), "SKIP", "SATISFIED_PROVIDER"),
    ("I use Ro. What does titration mean in general?",
     dict(subtype="general_education", subject_type="competitor", competitor="Ro",
          intents=["satisfied_provider", "general_information"], unmet_need="treatment_education",
          need_clarity=1, useful_contribution=2),
     "RESPOND", "EDUCATION_FIT"),
    ("Who can beat $99? Coupons only.",
     dict(category=Category.PURCHASE_INTENT, subject_type="category", intents=["price_only"]), "SKIP", "PRICE_ONLY"),
    ("Money is tight. What extra costs should I check?",
     dict(category=Category.PURCHASE_INTENT, subtype="pricing_question", subject_type="category",
          unmet_need="price_clarity", intents=["general_information"]), "RESPOND", "PROVIDER_SELECTION"),
    ("What does WellPeps charge? (price unverified)",
     dict(subtype="pricing_question", subject_type="wellpeps", intents=["wellpeps_question"],
          unmet_need="price_clarity"), "HOLD", "UNVERIFIED_FACT"),
    ("Can WellPeps keep my current dose?",
     dict(subtype="medication_change", subject_type="wellpeps", intents=["wellpeps_question",
                                                                       "individual_clinical_concern"]),
     "ROUTE", "CLINICAL_ROUTE"),
    ("Severe pain on my medication. Who is cheaper?",
     dict(category=Category.ADVERSE_EVENT, subject_type="category", intents=["possible_serious_harm",
                                                                           "price_only"]),
     "ROUTE", "CLINICAL_ROUTE"),
    ("Question has already received a complete accurate answer.",
     dict(subtype="general_education", subject_type="category", intents=["already_answered"]),
     "SKIP", "NO_ADDED_VALUE"),
    ("Question outside approved knowledge and service scope.",
     dict(category=Category.PURCHASE_INTENT, subject_type="category", useful_contribution=0),
     "SKIP", "KNOWLEDGE_GAP"),
    ("WellPeps charged me incorrectly.",
     dict(category=Category.BILLING_FRAUD, subject_type="wellpeps", intents=["wellpeps_complaint"]),
     "ROUTE", "SUPPORT_ROUTE"),
]


@pytest.mark.parametrize("post, fields, decision, code", CASES, ids=[c[0][:40] for c in CASES])
def test_selection_module_evaluation_cases(post, fields, decision, code, monkeypatch):
    from harvey import protocol

    allow_community(monkeypatch)        # "authorized access, verified participation permission"
    mention = _m(text=post)
    triage = protocol.applied(_t(**fields), mention)
    record = engagement.selection(triage, mention)
    assert (record.decision, record.reason_code) == (decision, code)
    assert (record.priority is not None) is (decision == "RESPOND")


def test_selection_holds_posting_when_rules_are_unknown():
    # "Any provider recommendations?" with community rules unknown: HOLD.
    from harvey import protocol

    mention = _m(sub="semaglutide", text="Any provider recommendations?")
    triage = protocol.applied(_t(category=Category.PURCHASE_INTENT, subtype="clinic_recommendation",
                                 subject_type="category", intents=["alternatives_requested"],
                                 unmet_need="continuity", need_clarity=1, useful_contribution=1), mention)
    record = engagement.selection(triage, mention)
    assert (record.decision, record.reason_code, record.public_permission) == ("HOLD", "RULES_UNKNOWN", "unknown")
    # Outside the protocol (a general question), drafting is allowed but posting is held.
    plain = _t(subtype="general_education", subject_type="category", unmet_need="treatment_education")
    assert engagement.review_gate(plain, mention) == ""
    assert engagement.selection(plain, mention).decision == "HOLD"


def test_selection_skips_in_a_prohibited_community(monkeypatch):
    from datetime import date  # noqa: F401  (kept local like the other permission test)

    from harvey.models.knowledge import CommunityEntry

    original = knowledge.communities
    entry = CommunityEntry(id="reddit:r/nope", platform="reddit", name="r/nope", brand_participation="prohibited")
    monkeypatch.setattr(knowledge, "communities", lambda: (*original(), entry))
    # "Explain GLP-1s." Employee replies prohibited: SKIP, no guide-by-name workaround.
    record = engagement.selection(_t(subtype="general_education", subject_type="category"), _m(sub="nope"))
    assert (record.decision, record.reason_code) == ("SKIP", "PARTICIPATION_PROHIBITED")
