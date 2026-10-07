"""Answer-first drafting (Approved Messaging & Response Guide §1-§3) and the
program-status claims the drafter offers so availability questions get a
direct answer. No Claude calls."""

from datetime import date

import pytest

from harvey import engagement, knowledge
from harvey.agents.drafter import (
    ANSWER_FIRST,
    Drafter,
    build_prompt,
    candidate_claims,
    engagement_block,
    programs_for,
)
from harvey.compliance import compliance_filter
from harvey.models import Category, Mention, Platform, Triage
from harvey.models.knowledge import LIVE_REFERENCE_APPROVER
from harvey.paths import PROJECT_ROOT
from tests.pulse_helpers import FakeBrain

HAIR_POST = "Thinking about oral minoxidil through a telehealth service. Is WellPeps hair restoration live yet or still a waitlist?"
FOLLOWUP_POST = ("anyone tried compounded tirzepatide via telehealth? Trying to figure out which providers "
                 "include the follow-up visits in the price.")
DRAFT_MD = (PROJECT_ROOT / "prompts" / "draft.md").read_text(encoding="utf-8")
REVIEW_MD = (PROJECT_ROOT / "prompts" / "review.md").read_text(encoding="utf-8")
STATUS = {"Weight Loss": "CLM-LR-03-STATUS-WEIGHT", "Sexual Wellness": "CLM-LR-03-STATUS-SEXUAL",
          "Healthy Aging & Vitality": "CLM-LR-03-STATUS-PEPTIDES", "Hair Restoration": "CLM-STATUS-HAIR",
          "Hormone Optimization": "CLM-STATUS-HORMONE", "Mental Wellness": "CLM-STATUS-MENTAL"}


def _mention(text: str) -> Mention:
    return Mention(id=7, platform=Platform.REDDIT, text=text, url="https://www.reddit.com/r/test/comments/a7/")


def _triage(category=Category.QUESTION, product="", drug="") -> Triage:
    return Triage(mention_id=7, category=category, product=product, drug=drug, reply_appropriate=True,
                  subject_type="wellpeps")


# --- Prompts ----------------------------------------------------------------------------------


def test_draft_prompt_puts_the_answer_first_and_never_opens_with_a_disclaimer():
    section = DRAFT_MD.split("## Answer first")[1].split("\n## ")[0]
    assert "answers what can be answered, states\nthe boundary when necessary, gives a useful next step" in section
    assert "directly answers what the poster actually asked" in section
    assert "never as the opener" in section
    assert "One useful next step" in section
    assert "No generic checklist when the post asks something specific" in section
    flat = " ".join(section.split())
    assert "status claim" in flat and "Never reply \"I can't speak to that\"" in flat
    assert "can't give a customer's experience" in flat and "never add your own detail" in flat
    assert "acknowledge" in section.lower() or "specific situation" in section
    # Before the claims and the playbook, so the model reads it first.
    assert DRAFT_MD.index("## Answer first") < DRAFT_MD.index("## Approved claims")
    assert "Never open with a disclaimer." in DRAFT_MD.split("## Playbook")[1]


def test_engagement_guidance_carries_the_answer_first_line():
    assert ANSWER_FIRST in engagement_block(None)
    situation = engagement.situation_for("question")
    assert ANSWER_FIRST in engagement_block(engagement.guidance_for(situation))
    assert "Answer the actual question first" in situation.notes


def test_reviewer_flags_non_responsive_generic_replies_as_needs_human():
    assert "NON_RESPONSIVE" in REVIEW_MD
    block = REVIEW_MD.split("NON_RESPONSIVE")[1].split("\n8.")[0]
    assert "does not\n     answer the specific question asked" in block
    assert "`needs_human`" in block and "reason" in block


# --- Program-status claims ---------------------------------------------------------------------


def test_every_program_has_exactly_one_status_claim():
    programs = {c.name for c in knowledge.products().categories}
    by_program = {}
    for claim in knowledge.claims():
        if claim.program:
            by_program.setdefault(claim.program, []).append(claim.id)
    assert {p: ids[0] for p, ids in by_program.items()} == STATUS
    assert set(STATUS) == programs and all(len(ids) == 1 for ids in by_program.values())


def test_status_claims_follow_the_sources():
    by_id = knowledge.claims_by_id()
    # Live Reference §3 APPROVED rows that agree with the site: publishable via the guide toggle.
    for cid in ("CLM-LR-03-STATUS-WEIGHT", "CLM-LR-03-STATUS-SEXUAL", "CLM-LR-03-STATUS-PEPTIDES"):
        claim = by_id[cid]
        assert claim.guide and claim.approved_by == LIVE_REFERENCE_APPROVER
        assert claim.source.startswith("Approved Messaging Live Reference v1.0 §3") and "APPROVED" in claim.source
        assert "available now" in claim.text
    # Site-only, conflicting or CONFIRM: pending.
    for cid in ("CLM-STATUS-HAIR", "CLM-STATUS-HORMONE", "CLM-STATUS-MENTAL"):
        claim = by_id[cid]
        assert claim.approved_by == "PENDING" and not claim.guide
        assert "coming soon" in claim.text
    assert "waitlist" in by_id["CLM-STATUS-HORMONE"].text
    assert "CONFIRM" in by_id["CLM-STATUS-MENTAL"].source
    assert "§3" in by_id["CLM-STATUS-HAIR"].note
    publishable = knowledge.publishable_claim_ids(date.today())
    assert {"CLM-LR-03-STATUS-WEIGHT", "CLM-LR-03-STATUS-SEXUAL", "CLM-LR-03-STATUS-PEPTIDES"} <= publishable
    assert not {"CLM-STATUS-HAIR", "CLM-STATUS-HORMONE", "CLM-STATUS-MENTAL"} & publishable


def test_status_claims_pass_the_filter_and_never_call_hair_available():
    for cid in STATUS.values():
        result = compliance_filter(f"I work with WellPeps. {knowledge.claims_by_id()[cid].text}", "reddit", [cid])
        assert result.tier != "red", (cid, result.red_hits)
    hair = compliance_filter("I work with WellPeps. Hair restoration is now available.", "reddit",
                             ["CLM-AMG-04-WORK-WITH"])
    assert any(h.rule_id == "R21" for h in hair.red_hits)


@pytest.mark.parametrize("product, drug, text, program", [
    ("", "", HAIR_POST, "Hair Restoration"),                          # words only
    ("", "minoxidil", "is it live yet?", "Hair Restoration"),        # drug only
    ("Compounded Tirzepatide", "", "", "Weight Loss"),               # product only
    ("", "tadalafil", "anyone use an online service?", "Sexual Wellness"),
    ("", "", "When does WellPeps hormone care open?", "Hormone Optimization"),
    ("", "", "Is there anything for mental health yet?", "Mental Wellness"),
])
def test_programs_come_from_product_drug_or_words(product, drug, text, program):
    assert program in programs_for(product, drug, text)


def test_unrelated_posts_map_to_no_program():
    assert programs_for("", "", "Huge win for the U10 squad this weekend!") == []


def test_hair_question_offers_the_hair_status_claim_right_after_the_disclosure():
    triage = _triage(drug="minoxidil")
    claims = candidate_claims("", drug="minoxidil", category=Category.QUESTION,
                              programs=programs_for("", "minoxidil", HAIR_POST))
    ids = [c.id for c in claims]
    assert ids[0] == "CLM-AMG-04-WORK-WITH" and ids[1] == "CLM-STATUS-HAIR"
    prompt = build_prompt(_mention(HAIR_POST), triage, claims)
    assert "[CLM-STATUS-HAIR]" in prompt and "Program (if known): Hair Restoration" in prompt


def test_status_claim_is_offered_with_situation_preferred_claims_too():
    situation = engagement.situation_for("question")
    claims = candidate_claims("", category=Category.QUESTION, preferred=tuple(situation.preferred_claims),
                              programs=["Hair Restoration"])
    assert [c.id for c in claims][:2] == ["CLM-AMG-04-WORK-WITH", "CLM-STATUS-HAIR"]


def test_no_status_claim_without_a_program():
    ids = [c.id for c in candidate_claims("", category=Category.QUESTION)]
    assert not set(STATUS.values()) & set(ids)


@pytest.mark.asyncio
async def test_drafter_offers_the_status_claim_for_the_real_hair_post():
    brain = FakeBrain([{"reply": "I work with WellPeps. Not live yet.", "claim_ids": ["CLM-AMG-04-WORK-WITH",
                                                                                   "CLM-STATUS-HAIR"]}])
    proposal = await Drafter(brain).draft(_mention(HAIR_POST), _triage())
    assert "CLM-STATUS-HAIR" in proposal.offered_claim_ids
    assert proposal.claim_ids == ["CLM-AMG-04-WORK-WITH", "CLM-STATUS-HAIR"]
    assert "[CLM-STATUS-HAIR]" in brain.prompts[0]


# --- Few-shot examples -----------------------------------------------------------------------


def test_examples_cover_availability_and_follow_up_pricing_answer_first():
    examples = {e.post: e for e in knowledge.reply_examples().examples}
    hair = examples[HAIR_POST]
    assert "CLM-STATUS-HAIR" in hair.claim_ids
    assert hair.reply.split(". ", 1)[1].startswith("Not live yet")              # the answer comes second
    followup = examples[FOLLOWUP_POST]
    assert followup.reply.split(". ", 1)[1].startswith("It varies by provider")
    for example in examples.values():
        second = example.reply.split(". ", 1)[1].lower()
        assert not second.startswith(("compounded", "a licensed healthcare provider determines")), example.id


# --- Protocol: a publishable program-status claim partly answers availability ----------------------


def _availability_input(**overrides):
    from harvey import protocol

    fields = dict(category="question", subtype="process_question", subject_type="wellpeps",
                  intents=frozenset({"wellpeps_question"}), unmet_need="medication_availability",
                  need_clarity=2, useful_contribution=1, participation="allowed",
                  program_status=("CLM-STATUS-HAIR",))
    fields.update(overrides)
    return protocol.ProtocolInput(**fields)


def test_availability_question_holds_while_the_status_claim_is_pending():
    from harvey import protocol

    record = protocol.decide(_availability_input())                      # real config: CLM-STATUS-HAIR pending
    assert record.decision == protocol.HOLD


def test_availability_question_drafts_once_the_status_claim_is_publishable():
    from harvey import protocol

    publishable = knowledge.publishable_claim_ids() | {"CLM-STATUS-HAIR"}
    record = protocol.decide(_availability_input(), publishable)
    assert record.decision != protocol.HOLD
    assert record.components["capability_fit"] == 1 and "CLM-STATUS-HAIR" in record.evidence


def test_status_claims_never_answer_an_unrelated_medication():
    from harvey import protocol

    publishable = knowledge.publishable_claim_ids() | {"CLM-STATUS-HAIR"}
    assert protocol.decide(_availability_input(program_status=()), publishable).decision == protocol.HOLD


def test_protocol_input_carries_the_posts_program_status_claims():
    from harvey import protocol

    triage = _triage(drug="minoxidil")
    inp = protocol.input_for(triage, text=HAIR_POST)
    assert inp.program_status == ("CLM-STATUS-HAIR",)


def test_reviewer_does_not_flag_an_answer_that_states_the_competitor_boundary():
    flat = " ".join(REVIEW_MD.split())
    assert "answers as far as the rules allow" in flat and "Do not flag that." in flat


def test_educational_only_replies_do_not_prefer_the_service_and_pricing_line():
    situation = next(s for s in knowledge.engagement_guide().situations if s.id == "protocol_educational_only")
    assert "CLM-AMG-APPX-COMPETITOR" not in situation.preferred_claims
