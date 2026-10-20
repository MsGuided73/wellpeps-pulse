"""Filter and prompt rules from the 2026-10-20 session safeguards and WellPeps'
Oct 8-10 guidance (docs/REVISIONS-LOG.md R-20; docs/RULES-OF-ENGAGEMENT.md).

- Never suggest a call, text, e-mail or other private channel (DMs were R49).
- Never ask anyone to post order, account or health details in public.
- R31 narrowed to the core rule: claiming record access or confirming customer
  status; neutral referral wording is allowed.
- Never invent a clinical department or authority; never promise ungated access.
- 40-90 words is a target, never a filter rule.
- Every approved WellPeps response still passes the new rules.
"""

import pytest

from harvey import knowledge
from harvey.agents import prompting
from harvey.compliance import compliance_filter

DISCLOSE = "I work with WellPeps."
CLAIM = ["CLM-AMG-04-WORK-WITH"]


def _red(text: str) -> list[str]:
    result = compliance_filter(f"{DISCLOSE} {text}", "reddit", CLAIM)
    return [h.rule_id for h in result.red_hits if h.rule_id != "CLAIMS"]


@pytest.mark.parametrize("text", [
    "Give us a call and we'll sort it out.",
    "I'll call you tomorrow.",
    "Feel free to email me.",
    "Text me if you have questions.",
    "Happy to hop on a quick call.",
    "Please reach out privately.",
    "Let's chat offline about it.",
    "Send me your number.",
])
def test_private_channel_suggestions_are_red(text):
    assert "R49" in _red(text)


@pytest.mark.parametrize("text", [
    "Post your order number below.",
    "Share your dose and we can compare.",
    "What's your order number?",
    "Drop your lab results here.",
])
def test_asking_for_details_in_public_is_red(text):
    assert "R32" in _red(text)


@pytest.mark.parametrize("text", [
    "Please don't post your order number here.",
    "For your privacy, please don't post medical or account details publicly.",
    "Ask whether the price will include the medication.",
    "I can help route this to the appropriate support channel.",
])
def test_privacy_safe_wording_is_not_flagged(text):
    reds = _red(text)
    assert "R32" not in reds and "R49" not in reds and "R31" not in reds


@pytest.mark.parametrize("text, rule", [
    ("I'm on the WellPeps clinical team.", "R2"),
    ("We're with the medical team here.", "R2"),
    ("It's free, no email required.", "R49"),
    ("Instant download, no signup needed.", "R49"),
    ("Your WellPeps order is on the way.", "R31"),
    ("I checked your account.", "R31"),
    ("Your refill has been shipped.", "R31"),
])
def test_new_red_rules(text, rule):
    assert rule in _red(text)


@pytest.mark.parametrize("text", [
    "I'm part of the WellPeps team, so I'm biased, but here's what I'd check.",
    "Our free GLP-1 Weight Loss guide covers this (it's free, it just asks for an email).",
    "Ask your provider what follow-up is included before you sign anything.",
    "Contact your prescribing provider promptly.",
])
def test_ordinary_wording_stays_clear_of_the_new_rules(text):
    reds = _red(text)
    assert not {"R2", "R31", "R32", "R49"} & set(reds), reds


def test_word_count_is_a_target_not_a_gate():
    # [CP] §7 "aim for": a 150-word reply (under the platform's character limit)
    # gets no length or word-count hit; only the character limit is a rule.
    body = " ".join(["Ask each provider what follow-up is included and how messaging works."] * 14)
    result = compliance_filter(f"{DISCLOSE} {body}", "reddit", CLAIM)
    assert len(body.split()) > 120
    assert not [h for h in result.hits if h.rule_id in ("LIMIT", "WORDS") or h.kind == "length"]


def test_every_approved_response_passes_the_new_rules():
    for claim in knowledge.claims():
        result = compliance_filter(f"{DISCLOSE} {claim.text}", "reddit", [claim.id])
        reds = [(h.rule_id, h.match) for h in result.red_hits if h.rule_id in ("R2", "R31", "R32", "R49")]
        assert not reds, (claim.id, reds)


def test_never_write_list_carries_the_new_examples():
    examples = {e for rule in knowledge.compliance_rules().prohibited for e in rule.examples}
    for phrase in ("Give us a call", "post your order number", "no email required",
                   "I'm on the WellPeps clinical team"):
        assert phrase in examples
    confirmation = {e for rule in knowledge.compliance_rules().patient_confirmation for e in rule.examples}
    assert "your order" not in confirmation and "your provider" not in confirmation
    assert "we'll look into your account" in confirmation


# --- Prompts ------------------------------------------------------------------------------------------


def _prompt(name: str) -> str:
    return (prompting.PROMPTS_DIR / name).read_text(encoding="utf-8")


def test_reviewer_prompt_matches_the_disclosure_window_and_new_checks():
    text = _prompt("review.md")
    assert "first two\n     sentences" in text or "first two sentences" in text
    assert "in the first\n     sentence" not in text
    for rule in ("SELECTION", "OTHER_PERSON"):
        assert f'"{rule}"' in text
    assert "target, not a rule" in text


def test_triage_prompt_keeps_alternatives_requests_and_tags_selection_intents():
    text = _prompt("triage.md")
    assert "who else should I try?" in text and "not a\nreason to set `reply_appropriate` to false" in text
    for tag in ("satisfied_provider", "price_only", "already_answered", "other_person_medical"):
        assert f"`{tag}`" in text


def test_safety_screen_asks_about_someone_else():
    text = _prompt("safety_screen.md")
    assert "`other_person`" in text and "my father" in text


def test_reply_rules_state_the_narrowed_privacy_rule_and_the_length_target():
    text = _prompt("reply_rules.md")
    assert "contact your prescribing provider" in text
    assert "call, text or e-mail" in text
    assert "target, not a limit" in text
