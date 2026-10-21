"""Derek Goldberg's Community Agent Voice (must follow; owner decisions 2026-10-20):
the official-account disclosure, the avoid list as a yellow warning, the voice
principles in the drafter and reviewer prompts, and the review desk's
"Posting as" switch. No `claude` calls."""

import pytest

from harvey import dashboard
from harvey.compliance import compliance_filter

IDS = ["CLM-AMG-04-WORK-WITH", "CLM-R7-PROVIDER-CHECKLIST"]
ANSWER = "It varies by provider, so ask each one what follow-up with a licensed clinician is included."
WEB = dashboard.WEB_DIR
PROMPTS = dashboard.WEB_DIR.parent.parent / "prompts"


def _rules(result) -> set[str]:
    return {h.rule_id for h in result.hits}


@pytest.mark.parametrize("disclosure", ["We're the WellPeps team.", "We are the WellPeps team.",
                                        "I work with WellPeps on patient education."])
def test_official_account_and_patient_education_disclosures_are_accepted(disclosure):
    result = compliance_filter(f"{disclosure} {ANSWER}", "reddit", IDS)
    assert "R3" not in _rules(result)


@pytest.mark.parametrize("phrase", ["Hope this helps!", "Happy to help!", "Trust me, it varies.",
                                    "Ready to get started?", "This can transform your journey.",
                                    "I understand your frustration."])
def test_avoid_list_phrases_are_a_yellow_warning_never_red(phrase):
    result = compliance_filter(f"I work with WellPeps. {ANSWER} {phrase}", "reddit", IDS)
    hits = [h for h in result.hits if h.rule_id == "R50"]
    assert hits and all(h.kind == "yellow" for h in hits)


def test_plain_helpful_wording_does_not_trip_the_voice_rule():
    result = compliance_filter(f"I work with WellPeps on patient education. {ANSWER}", "reddit", IDS)
    assert "R50" not in _rules(result)


def test_drafter_prompt_carries_the_voice_principles_and_avoid_list():
    draft = " ".join((PROMPTS / "draft.md").read_text(encoding="utf-8").split())
    for needle in ("Community Agent Voice", "Answer the actual question", "three to five sentences",
                   "Never write a person's name", "transform your journey", "Happy to help!",
                   "Here's a link to get a free copy if you'd like"):
        assert needle in draft, needle
    assert '("Happy to help.", "Thanks for asking.")' not in draft


def test_reviewer_checks_voice_separately_and_never_blocks_on_it():
    review = " ".join((PROMPTS / "review.md").read_text(encoding="utf-8").split())
    assert 'rule id "VOICE"' in review and "never change the verdict" in review
    assert "asks for an email. The chapter titles" not in review


def test_review_card_has_the_posting_as_switch_and_voice_notes():
    js = (WEB / "home.js").read_text(encoding="utf-8")
    assert "We're the WellPeps team" in js and "I work with WellPeps on patient education" in js
    assert "'as-me':" in js and "'as-brand':" in js and 'aria-label="Posting as"' in js
    assert "/^VOICE:/" in js
