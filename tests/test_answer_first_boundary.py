"""Answer first, then the boundary, for mixed posts (memo to Derek, 2026-10-22, item 2).

Built but switched OFF (``switching_protocol.answer_first_boundary: false`` in
config/engagement_guide.yaml) until Derek approves it. When on: a post with a
real general question and only a minor personal element (no symptoms, no
harm, not a dose / labs / eligibility / medication question) is answered, and
the draft adds ONE approved boundary sentence for the personal part. Posts
that are mainly clinical, or describe symptoms, keep the boundary-only reply.
Sources: Operations Manual §18.1 ("Answer the general question first... State
the boundary if individualized medical judgment is involved"); Compliance
Training, General-to-Individual Boundary.
No network, no Claude.
"""

import pytest

from harvey import engagement, knowledge, protocol
from harvey.models import Category, Mention, Platform, Triage
from tests.community_helpers import allow_community

BOUNDARY = "CLM-AMG-APPX-WHICH-MEDICATION"
PHARMACY = ("My insurance won't cover Zepbound so I'm looking at compounded. Which pharmacies compound "
            "semaglutide, and what should I ask before my prescription is sent?")


def _m(text: str = PHARMACY) -> Mention:
    return Mention(id=1, platform=Platform.REDDIT, url="https://www.reddit.com/r/test/comments/k1/", text=text)


def _t(**kw) -> Triage:
    base = {"mention_id": 1, "relevant": True, "reply_appropriate": True, "category": Category.QUESTION,
            "subtype": "process_question", "subject_type": "category", "drug": "semaglutide",
            "intents": ["general_information", "comparison_request", "individual_clinical_concern"],
            "unmet_need": "care_process", "need_clarity": 2, "useful_contribution": 2}
    base.update(kw)
    return Triage(**base)


@pytest.fixture
def switched_on(monkeypatch):
    original = protocol.config
    monkeypatch.setattr(protocol, "config", lambda: original().model_copy(update={"answer_first_boundary": True}))


def test_it_ships_switched_off():
    assert knowledge.engagement_guide().switching_protocol.answer_first_boundary is False


def test_off_a_mixed_post_still_gets_the_boundary_only_reply(monkeypatch):
    allow_community(monkeypatch)
    triage = protocol.applied(_t(), _m())
    assert triage.protocol_decision == protocol.CLINICAL_CAUTION
    assert protocol.answer_first_applies(triage) is False


def test_on_a_mixed_post_is_answered_with_one_boundary_line(monkeypatch, switched_on):
    allow_community(monkeypatch)
    triage = protocol.applied(_t(), _m())
    assert triage.protocol_decision != protocol.CLINICAL_CAUTION
    assert protocol.answer_first_applies(triage) is True
    guidance = engagement.guidance_for(engagement.situation_of(triage), triage=triage, mention=_m())
    assert BOUNDARY in guidance.preferred_claims
    assert "answer the general question first" in guidance.notes.lower()
    assert knowledge.claims_by_id()[BOUNDARY].approved_by != "PENDING"


@pytest.mark.parametrize("fields", [
    {"intents": ["general_information", "individual_clinical_concern", "possible_serious_harm"]},  # harm
    {"subtype": "dose_question"},                                                                    # clinical type
    {"subtype": "symptom_report"},
    {"subtype": "lab_question"},
    {"category": Category.ADVERSE_EVENT},
    {"intents": ["individual_clinical_concern"]},                                     # no general question at all
])
def test_on_clinical_posts_keep_the_boundary_only_reply(monkeypatch, switched_on, fields):
    allow_community(monkeypatch)
    triage = protocol.applied(_t(**fields), _m())
    assert protocol.answer_first_applies(triage) is False
    # Never an answering decision: step 2 / 3 routes it, or (a lone clinical tag) it is outside the
    # protocol's scope and gets no protocol decision at all.
    assert triage.protocol_decision in (protocol.CLINICAL_CAUTION, protocol.ESCALATE, "")


def test_on_a_post_without_the_clinical_tag_is_unchanged(monkeypatch, switched_on):
    allow_community(monkeypatch)
    triage = protocol.applied(_t(intents=["general_information", "comparison_request"]), _m())
    assert protocol.answer_first_applies(triage) is False
    guidance = engagement.guidance_for(engagement.situation_of(triage), triage=triage, mention=_m())
    assert BOUNDARY not in guidance.preferred_claims


# --- The cost-comparison checklist (memo item 3): added, pending Derek ----------------------------------


def test_the_cost_comparison_checklist_waits_for_derek():
    claim = knowledge.claims_by_id()["CLM-CHECKLIST-COST-COMPARE"]
    assert claim.approved_by == "PENDING" and claim.approved_at is None
    assert "price changes as the dose goes up" in claim.text and "labs cost extra" in claim.text
    assert "CLM-CHECKLIST-COST-COMPARE" not in knowledge.publishable_claim_ids()
