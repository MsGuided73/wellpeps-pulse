"""A post is "about WellPeps" only when it names WellPeps (owner decision 2026-10-23, R-42).

The model labelled posts about oral minoxidil and compounded tirzepatide "about WellPeps"
because WellPeps sells those drugs, which paged the clinical owner for strangers' side effects
(R-36 says only WellPeps' own incidents page anyone). Now a top-level post that doesn't name
WellPeps is a general drug post. Two exceptions keep the model's call: a reply inside a thread
(it may answer a WellPeps comment) and WellPeps' own channels. No network, no Claude.
"""

import asyncio

import pytest

from harvey.agents.triager import Triager
from harvey.escalation import escalation_kind
from harvey.models import Mention, Platform
from tests.test_triager import FakeBrain, _answer

SIDE_EFFECT = ("Oral minoxidil for 2 years, can't handle it anymore. The shedding and the heart racing are too much. "
               "Should I switch to topical?")
NAMED = "Got oral minoxidil through WellPeps two months ago and my heart has been racing since."
AE = dict(subject_type="wellpeps", subject="oral minoxidil side effects", product="Oral Minoxidil",
          category="adverse_event", subtype="symptom_report", urgency="urgent", sentiment=-0.7,
          sentiment_label="negative", reply_appropriate=False)


def _triage(text, **mention_fields):
    brain = FakeBrain({text[:20]: [_answer(**AE)]})
    mention = Mention(id=1, platform=Platform.REDDIT, text=text,
                      url="https://www.reddit.com/r/tressless/comments/a1/t/", **mention_fields)
    return asyncio.run(Triager(brain).triage(mention))


def test_a_post_that_never_names_wellpeps_is_not_about_wellpeps():
    triage = _triage(SIDE_EFFECT)
    assert triage.subject_type == "category"
    assert triage.product == ""
    assert escalation_kind(triage) is None          # no page for a stranger's side effect (R-36)


def test_a_post_that_names_wellpeps_still_pages():
    triage = _triage(NAMED)
    assert triage.subject_type == "wellpeps" and triage.product == "Oral Minoxidil"
    assert escalation_kind(triage) == "adverse_event"


def test_a_reply_inside_a_thread_keeps_the_model_call():
    triage = _triage(SIDE_EFFECT, parent_external_id="t1_abc")
    assert triage.subject_type == "wellpeps"
    assert escalation_kind(triage) == "adverse_event"


def test_wellpeps_own_channels_keep_the_model_call():
    triage = _triage(SIDE_EFFECT, owned_channel=True)
    assert triage.subject_type == "wellpeps"
    assert escalation_kind(triage) == "adverse_event"


def test_a_named_competitor_becomes_the_subject():
    brain = FakeBrain({"Hims": [_answer(**{**AE, "competitor": "Hims"})]})
    mention = Mention(id=2, platform=Platform.REDDIT, text="Hims oral minoxidil gave me heart palpitations",
                      url="https://www.reddit.com/r/tressless/comments/a2/t/")
    triage = asyncio.run(Triager(brain).triage(mention))
    assert triage.subject_type == "competitor"
    assert escalation_kind(triage) is None
