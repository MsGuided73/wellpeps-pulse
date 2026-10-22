"""Passing mentions and posts where WellPeps has no useful role (R-31, owner decision 2026-10-21).

Source case: r/snoring "Psychology of separate beds" (a spouse asks how others cope
with sleeping apart; GLP-1 appears once, as a last resort in their ENT plan). Triage
called it general education and today's rules said RESPOND (EDUCATION_FIT).

WellPeps' documents already rule this out: "Do not treat every medication mention
as a prospect" and "A relevant keyword ... is insufficient on its own" (Selection
Module), "Relevance to a medication alone is too broad" (Why and When to Engage),
and "Highly emotional disputes where WellPeps has no useful role" (Operations
Manual §5.3).

The owner's rule: a post that mentions something WellPeps offers in passing is
skipped only when ALL THREE hold:
  1. its main question or topic isn't about anything WellPeps covers
     (``topic_not_covered``);
  2. it does not naturally call for anything WellPeps offers (``no_natural_fit``);
  3. a reply would seem out of touch with the post's main point, as if WellPeps
     were injecting itself for commercial reasons (``reply_would_intrude``).
A request only for emotional, relationship or coping support
(``emotional_support_only``) is skipped as NO_USEFUL_ROLE. Neither applies to a
post about WellPeps or one that asks WellPeps something. Skipped posts are still
collected and counted for insights; they are never drafted.
No network, no Claude.
"""

import pytest

from harvey import engagement, protocol
from harvey.agents.triager import Triager, triage_batch
from harvey.models import Category, Mention, Platform, Triage
from harvey.models.mention import PROTOCOL_INTENTS
from harvey.state import StateManager
from tests.community_helpers import allow_community
from tests.test_triager import FakeBrain, _answer

SEPARATE_BEDS = ("I've been using snorelab for over a year. My husband is a very light sleeper... if all else fails "
                 "maybe glp-1 or other more holistic changes. This is all because I really can't stomach the idea "
                 "of sleeping apart for life. Has anyone else had a really hard time with this and what did you do "
                 "to get over it")
PASSING = ["topic_not_covered", "no_natural_fit", "reply_would_intrude"]


def _m(text: str = "post", key: str = "k1", **kw) -> Mention:
    return Mention(id=kw.pop("id", 1), platform=Platform.REDDIT,
                   url=f"https://www.reddit.com/r/snoring/comments/{key}/", text=text, **kw)


def _t(**kw) -> Triage:
    base = {"mention_id": 1, "relevant": True, "reply_appropriate": True, "category": Category.QUESTION,
            "subtype": "general_education", "subject_type": "category", "drug": "glp-1",
            "intents": ["general_information"], "unmet_need": "other", "need_clarity": 1, "useful_contribution": 1}
    base.update(kw)
    return Triage(**base)


def test_the_new_tags_are_known_intents():
    for tag in (*PASSING, "emotional_support_only"):
        assert tag in PROTOCOL_INTENTS


def test_separate_beds_is_skipped_as_a_passing_mention(monkeypatch):
    allow_community(monkeypatch, "reddit:r/snoring")
    triage = _t(intents=["general_information", "individual_clinical_concern", *PASSING])
    assert engagement.skip_reason(triage) == engagement.SKIP_PASSING_MENTION
    gated = engagement.with_review_gate(triage)
    assert gated.urgency_reason.endswith(f"{engagement.GATE_NOT_WORTH_IT}:{engagement.SKIP_PASSING_MENTION}")
    assert engagement.reply_mode(gated) == "no_reply" and engagement.drafts_reply(gated) is False
    mention = _m(SEPARATE_BEDS)
    record = engagement.selection(protocol.applied(_t(intents=["general_information", *PASSING]), mention), mention)
    assert (record.decision, record.reason_code) == ("SKIP", "PASSING_MENTION")


@pytest.mark.parametrize("missing", PASSING)
def test_two_of_three_is_not_enough(missing):
    """Each criterion matters: a post that still calls for what WellPeps offers, or
    where a reply would fit, keeps its normal selection."""
    triage = _t(intents=["general_information", *[t for t in PASSING if t != missing]])
    assert engagement.skip_reason(triage) == ""


@pytest.mark.parametrize("fields", [
    {"subject_type": "wellpeps"},
    {"intents": ["wellpeps_question", *PASSING]},
])
def test_never_skips_a_post_about_or_asking_wellpeps(fields):
    triage = _t(**{"intents": ["general_information", *PASSING], **fields})
    assert engagement.skip_reason(triage) not in (engagement.SKIP_PASSING_MENTION, engagement.SKIP_NO_USEFUL_ROLE)


def test_emotional_support_only_is_no_useful_role(monkeypatch):
    allow_community(monkeypatch, "reddit:r/snoring")
    triage = _t(intents=["emotional_support_only"])
    assert engagement.skip_reason(triage) == engagement.SKIP_NO_USEFUL_ROLE
    mention = _m(SEPARATE_BEDS)
    record = engagement.selection(protocol.applied(triage, mention), mention)
    assert (record.decision, record.reason_code) == ("SKIP", "NO_USEFUL_ROLE")


def test_a_real_medication_question_in_a_personal_post_still_gets_an_answer(monkeypatch):
    """Naming a medication AND asking about it is an opening, not a passing mention."""
    allow_community(monkeypatch, "reddit:r/snoring")
    mention = _m("My partner and I sleep apart because of my snoring. Does GLP-1 weight loss usually help snoring?")
    triage = protocol.applied(_t(unmet_need="treatment_education", need_clarity=2, useful_contribution=2), mention)
    assert engagement.skip_reason(triage) == ""
    assert engagement.selection(triage, mention).decision == "RESPOND"


def test_triage_prompt_asks_the_three_questions():
    from harvey.agents.triager import build_prompt

    prompt = build_prompt(_m(SEPARATE_BEDS), nonce="n")
    for tag in (*PASSING, "emotional_support_only"):
        assert f"`{tag}`" in prompt
    assert "all three" in prompt.lower()


@pytest.mark.asyncio
async def test_end_to_end_the_post_is_kept_but_never_drafted(tmp_path):
    state = StateManager(str(tmp_path / "pulse.db"))
    await state.init_db()
    mid, _ = await state.upsert_mention(_m(SEPARATE_BEDS, key="beds", id=None))
    brain = FakeBrain({"snorelab": [_answer(category="question", subtype="general_education", subject_type="category",
                                            drug="glp-1", intents=["general_information", *PASSING],
                                            unmet_need="other", need_clarity=1, useful_contribution=1,
                                            urgency="normal", sentiment=0.0, sentiment_label="neutral")]})
    report = await triage_batch(state, Triager(brain))
    assert report.errors == 0
    triage = await state.get_triage(mid)
    assert triage.relevant is True                      # still collected and counted for insights
    assert engagement.skip_code(triage) == engagement.SKIP_PASSING_MENTION
    assert await state.list_draftable_mentions() == []


# --- The topic gate reads the title too (bug found while checking R-31, 2026-10-21) -------------------


@pytest.mark.asyncio
async def test_a_post_whose_topic_is_only_in_the_title_stays_relevant():
    """r/GLP1Discussion "Trying to find the cheapest legit compounded tirzepatide, how do you
    compare its total cost?": the body never repeats the drug name. The topic gate read only
    the body and dropped the post as off-topic."""
    mention = Mention(id=17, platform=Platform.REDDIT, url="https://www.reddit.com/r/GLP1Discussion/comments/t1/",
                      title="Trying to find the cheapest legit compounded tirzepatide, how do you compare its total cost?",
                      text="Another has a membership fee and then charges for the meds on top. Shipping is extra on "
                           "some too. What fees surprised you after you signed up?")
    brain = FakeBrain({"membership fee": [_answer(category="question", subtype="pricing_question",
                                                  subject_type="category", drug="tirzepatide",
                                                  intents=["general_information"], unmet_need="price_clarity",
                                                  urgency="normal", sentiment=0.0, sentiment_label="neutral")]})
    triage = await Triager(brain).triage(mention)
    assert triage.relevant is True
