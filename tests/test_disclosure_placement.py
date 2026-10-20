"""Disclosure placement (owner decision 2026-10-15): the WellPeps affiliation
must be obvious, not necessarily first. It has to appear within the first two
sentences and before any other mention of WellPeps, its programs or a
wellpeps.com link. A reply may open by responding to the person."""

import pytest

from harvey import knowledge
from harvey.compliance import (
    compliance_filter,
    first_sentence,
    has_disclosure,
    naked_link,
    opening_sentences,
    sentence_spans,
)

IDS = ["CLM-AMG-04-WORK-WITH"]
GUIDE_URL = "https://wellpeps.com/smart-patient-guides/glp-1-weight-loss"
GUIDE_IDS = [*IDS, "CLM-R7-PROVIDER-CHECKLIST", "CLM-EDU-GUIDE-GLP-1-WEIGHT-LOSS"]
ANSWER = ("Ask each provider what follow-up with a licensed clinician is included and how dose adjustments "
          "are handled, and compare the same things everywhere.")


def _disclosure_hits(text, ids=IDS):
    return [h for h in compliance_filter(text, "reddit", ids).hits if h.kind == "disclosure"]


def _red_r3(text, ids=IDS):
    return [h for h in compliance_filter(text, "reddit", ids).red_hits if h.rule_id == "R3"]


# --- Where the window is -------------------------------------------------------------------------


def test_sentence_helpers_split_on_sentence_ends_and_lines():
    text = "Ugh, that is rough. I work with WellPeps.\nHere is the answer! Really?"
    assert first_sentence(text) == "Ugh, that is rough."
    assert opening_sentences(text, 2) == "Ugh, that is rough. I work with WellPeps."
    assert len(sentence_spans(text)) == 4
    assert opening_sentences("Only one sentence", 2) == "Only one sentence"


def test_abbreviations_do_not_end_a_sentence():
    text = "Oral vs. topical is a fair thing to ask. I work with WellPeps, so I'm biased."
    assert opening_sentences(text, 2) == text
    assert has_disclosure(text)


def test_the_window_is_configured_in_the_rules_file():
    assert knowledge.compliance_rules().disclosure.within_sentences == 2


# --- Passing placements --------------------------------------------------------------------------


def test_disclosure_in_sentence_one_passes():
    assert _disclosure_hits(f"I work with WellPeps. {ANSWER}") == []


def test_disclosure_in_sentence_two_passes():
    text = f"Follow-up visits are exactly the thing to pin down. I work with WellPeps. {ANSWER}"
    assert has_disclosure(text)
    assert _disclosure_hits(text) == []
    assert compliance_filter(text, "reddit", IDS).tier == "green"


@pytest.mark.parametrize("text", [
    "Ugh, that pricing maze is the worst. I'm part of the WellPeps team, so I'm biased, but here's what I'd "
    "check: what follow-up is included and how dose changes are handled.",
    "Full disclosure, I work with WellPeps. Here is what I'd compare across providers.",
    "Fair question. Disclosure: I work with WellPeps, so I'm not neutral. Compare the same things everywhere.",
])
def test_woven_and_conversational_disclosures_pass(text):
    assert has_disclosure(text)
    assert _disclosure_hits(text) == []


def test_acknowledgement_opener_then_disclosure_passes():
    text = ("Yeah, the intro-price-then-jump thing catches a lot of people. I work with WellPeps, so take this "
            f"with that in mind. {ANSWER}")
    assert compliance_filter(text, "reddit", IDS).tier == "green"


def test_a_reaction_may_use_words_that_are_not_about_the_brand():
    text = "Honestly, our whole industry is confusing about this. I work with WellPeps. " + ANSWER
    # "our whole industry" is not a program, guide or membership: no brand-voice hit.
    assert _red_r3(text) == []


# --- Failing placements --------------------------------------------------------------------------


def test_disclosure_only_in_sentence_three_or_later_is_red():
    text = f"Good question. Thanks for asking. I work with WellPeps. {ANSWER}"
    assert not has_disclosure(text)
    hits = _red_r3(text)
    assert hits and "first two sentences" in hits[0].reason
    assert compliance_filter(text, "reddit", IDS).tier == "red"


def test_disclosure_only_at_the_end_is_red():
    text = f"{ANSWER} Compare the same things everywhere. Hope that helps. I work with WellPeps."
    assert not has_disclosure(text)
    assert _red_r3(text)
    assert compliance_filter(text, "reddit", IDS).tier == "red"


def test_no_disclosure_at_all_is_red_with_the_new_reason_text():
    hits = _red_r3("Thanks for asking. " + ANSWER)
    assert hits and hits[0].reason.startswith("no approved disclosure in the first two sentences")


def test_wellpeps_named_before_the_disclosure_is_red():
    text = f"WellPeps has a guide on this. I work with WellPeps. {ANSWER}"
    assert has_disclosure(text)
    hits = _red_r3(text)
    assert hits and "before the disclosure" in hits[0].reason
    assert compliance_filter(text, "reddit", IDS).tier == "red"


def test_a_wellpeps_link_before_the_disclosure_is_red():
    text = f"This covers it: {GUIDE_URL}. I work with WellPeps. {ANSWER}"
    hits = _red_r3(text, GUIDE_IDS)
    assert hits and "before the disclosure" in hits[0].reason


def test_brand_voice_before_the_disclosure_is_red():
    text = f"Our free guide covers that. I work with WellPeps. {ANSWER}"
    hits = _red_r3(text)
    assert hits and "before the disclosure" in hits[0].reason


def test_wellpeps_woven_into_the_disclosure_sentence_itself_is_fine():
    text = f"Quick note first: I'm part of the WellPeps team, so I'm biased. {ANSWER}"
    assert _red_r3(text) == []


def test_third_person_talk_without_disclosure_still_gets_its_own_reason():
    text = "Fair question. WellPeps offers follow-up care in the price."
    result = compliance_filter(text, "reddit", IDS)
    assert result.tier == "red"
    assert any(h.rule_id == "R2" and h.kind == "disclosure" for h in result.red_hits)


# --- R47 and R43 follow the window ----------------------------------------------------------------


def test_influencer_form_in_sentence_two_is_yellow_not_red():
    text = f"Good question. I'm a WellPeps Brand Ambassador. {ANSWER}"
    result = compliance_filter(text, "reddit", IDS)
    assert result.tier == "yellow"
    assert any(h.rule_id == "R47" for h in result.hits)


def test_naked_link_still_works_with_the_disclosure_in_sentence_two():
    assert naked_link(f"Fair question. I work with WellPeps. {GUIDE_URL}")
    assert naked_link(f"I work with WellPeps. {GUIDE_URL}")


def test_a_link_after_a_real_answer_is_not_naked_with_the_disclosure_in_sentence_two():
    text = ("That pricing maze is the worst. I work with WellPeps, so I'm biased. A few things worth checking "
            f"with any provider are whether a licensed clinician reviews you and what follow-up is included. {GUIDE_URL}")
    assert not naked_link(text)


def test_the_disclosure_sentence_is_exempt_from_the_word_count_wherever_it_sits():
    # 11 words of answer + a long disclosure sentence: still short of the 12-word minimum.
    minimum = knowledge.engagement_guide().link_rules.min_words_with_link
    answer = " ".join(["word"] * (minimum - 1)) + "."
    first = f"I work with WellPeps, so I'm biased but happy to help you with this one. {answer} {GUIDE_URL}"
    second = f"Fair enough. I work with WellPeps, so I'm biased but happy to help you with this one. {answer} {GUIDE_URL}"
    assert naked_link(first)
    # The reaction sentence counts as content (2 words), so the second reply clears the minimum.
    assert not naked_link(second.replace("Fair enough.", "Fair enough friend, that makes sense."))
