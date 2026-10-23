"""Price-comparison posts get the approved cost-comparison checklist (R-43, 2026-10-23).

Found on the live post "how are you all comparing the first charge vs what you pay each month
after?": CLM-CHECKLIST-COST-COMPARE (approved by Derek 2026-10-22, R-39) answers exactly that,
but the drafter never saw it (12-claim cap; a general claim far down the list), so every draft
fell back to the clinician checklist and the reviewer called it non-responsive. Now a post
whose need is price clarity, or a pricing question, offers the checklist first.
No network, no Claude.
"""

from harvey import engagement, knowledge
from harvey.agents.drafter import _post_text, candidate_claims, programs_for
from harvey.models import Category, Mention, Platform, Triage

POST = ("My insurance stopped covering Wegovy, so I'm going the compounded route. One provider charges $79 for "
        "meds plus a $99 membership. So how are you all comparing the first charge vs what you pay each month "
        "after? Any terms that make a first-month deal look better than it is?")
MENTION = Mention(id=16, platform=Platform.REDDIT, text=POST,
                  url="https://www.reddit.com/r/SemaglutideCompound/comments/x/t/")


def _triage(**kw) -> Triage:
    base = dict(mention_id=16, category=Category.PURCHASE_INTENT, subtype="pricing_question",
                subject_type="category", drug="semaglutide", reply_appropriate=True, relevant=True,
                intents=["general_information", "comparison_request"], unmet_need="price_clarity",
                need_clarity=2, useful_contribution=2)
    base.update(kw)
    return Triage(**base)


def _offered(triage) -> list[str]:
    guidance = engagement.guidance_for(engagement.situation_of(triage), triage=triage, mention=MENTION)
    claims = candidate_claims(triage.product, knowledge.claims(), drug=triage.drug, category=triage.category,
                              preferred=tuple(guidance.preferred_claims),
                              programs=programs_for(triage.product, triage.drug, _post_text(MENTION)),
                              guide_claim_id=guidance.guide_claim_id if guidance.guide_required else "")
    return [c.id for c in claims]


def test_the_checklist_is_approved():
    assert engagement.COST_COMPARE_CLAIM in knowledge.publishable_claim_ids()


def test_a_price_comparison_post_is_offered_the_checklist():
    assert engagement.COST_COMPARE_CLAIM in _offered(_triage())


def test_a_pricing_question_without_the_need_tag_gets_it_too():
    assert engagement.COST_COMPARE_CLAIM in _offered(_triage(unmet_need=""))


def test_the_guidance_tells_the_drafter_to_use_it():
    triage = _triage()
    guidance = engagement.guidance_for(engagement.situation_of(triage), triage=triage, mention=MENTION)
    assert guidance.preferred_claims[0] == engagement.COST_COMPARE_CLAIM
    assert "cost-comparison checklist" in guidance.notes


def test_an_unrelated_post_is_not_offered_it_first():
    triage = _triage(category=Category.QUESTION, subtype="process_question", unmet_need="fulfillment")
    guidance = engagement.guidance_for(engagement.situation_of(triage), triage=triage, mention=MENTION)
    assert engagement.COST_COMPARE_CLAIM not in guidance.preferred_claims
