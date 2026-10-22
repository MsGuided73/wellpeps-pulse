"""Out of scope: Pulse does not weigh in (R-36; Derek Goldberg via the owner, 2026-10-22).

"We do not want to weigh in on anything that is outside of our scope of
services, even if it is an urgent medical issue." A serious health report that
is not about WellPeps (an urgent keyword such as "seizure", a self-harm or
emergency post, a medical crisis about someone else) gets no escalation, no
Urgent item, no review deadline and no reply of any kind. Posts about WellPeps
still go to the clinical owner as before.
No network, no Claude.
"""

import pytest

from harvey import engagement
from harvey.agents.triager import Triager, apply_safety_net, route_status, triage_batch
from harvey.escalation import SAFETY_WATCH, escalation_kind
from harvey.models import Category, Mention, MentionStatus, Platform, Triage, Urgency
from harvey.state import StateManager
from tests.test_triager import FakeBrain, _answer

PHONE = ("Is there a paper white phone that can do Telehealth? I have a lot of doctor's appointments "
         "due to my vision and seizure disorders.")


def _t(**kw) -> Triage:
    base = {"mention_id": 1, "relevant": True, "category": Category.QUESTION, "subject_type": "category",
            "reply_appropriate": True}
    base.update(kw)
    return Triage(**base)


@pytest.mark.parametrize("triage", [
    apply_safety_net(_t(), PHONE),                                              # keyword: seizure
    _t(category=Category.ADVERSE_EVENT, urgency=Urgency.URGENT, subtype="emergency"),
    _t(category=Category.ADVERSE_EVENT, urgency=Urgency.URGENT, subtype="self_harm"),
    _t(subtype="safety_question", intents=["other_person_medical"]),            # someone else's crisis
    _t(category=Category.ADVERSE_EVENT, subject_type="competitor", competitor="Hims",
       urgency=Urgency.URGENT, urgency_reason="override:pancreatitis"),
])
def test_out_of_scope_health_reports_are_not_escalated_or_answered(triage):
    triage = engagement.with_review_gate(triage)
    assert escalation_kind(triage) is None
    assert escalation_kind(triage) != SAFETY_WATCH
    assert route_status(triage) is MentionStatus.TRIAGED
    assert engagement.reply_mode(triage) == "no_reply" and engagement.drafts_reply(triage) is False


@pytest.mark.parametrize("triage, kind", [
    (_t(category=Category.ADVERSE_EVENT, subject_type="wellpeps", urgency=Urgency.URGENT), "adverse_event"),
    (_t(subject_type="wellpeps", subtype="safety_question", intents=["other_person_medical"]), "adverse_event"),
])
def test_posts_about_wellpeps_still_go_to_the_clinical_owner(triage, kind):
    assert escalation_kind(triage) == kind


@pytest.mark.asyncio
async def test_end_to_end_the_phone_post_is_kept_quietly(tmp_path):
    state = StateManager(str(tmp_path / "pulse.db"))
    await state.init_db()
    mid, _ = await state.upsert_mention(Mention(platform=Platform.REDDIT, external_id="p1", text=PHONE,
                                                url="https://www.reddit.com/r/telehealth/comments/p1/"))
    brain = FakeBrain({"paper white": [_answer(category="question", subject_type="category", drug=None,
                                               intents=["topic_not_covered", "no_natural_fit", "reply_would_intrude"],
                                               urgency="normal", sentiment=0.0, sentiment_label="neutral")]})
    report = await triage_batch(state, Triager(brain))
    assert report.errors == 0 and report.escalated == 0
    assert (await state.get_mention(mid)).status is MentionStatus.TRIAGED
    assert await state.list_open_escalations() == []
    assert await state.list_draftable_mentions() == []
