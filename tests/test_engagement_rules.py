"""Rules of engagement in the deterministic compliance filter (table-driven):
words and phrases to avoid (Guide §7, §9, §26; Operations Manual §19),
disclosure forms (Guide §4), FINALIZE items, link rules (Guide §14),
community rules and the 80/20 limit (via ReplyContext)."""

import pytest

from harvey import knowledge
from harvey.compliance import ReplyContext, compliance_filter, has_disclosure, naked_link

OPEN = "I work with WellPeps. "
IDS = ["CLM-AMG-04-WORK-WITH"]
GUIDE_URL = "https://wellpeps.com/smart-patient-guides/glp-1-weight-loss"
GUIDE_IDS = ["CLM-AMG-04-WORK-WITH", "CLM-EDU-GUIDE-GLP-1-WEIGHT-LOSS"]


def _red_rules(text: str, ids=IDS, **kw) -> set[str]:
    return {h.rule_id for h in compliance_filter(OPEN + text, "facebook", ids, **kw).red_hits}


# --- Words and phrases to avoid ---------------------------------------------------------------

AVOID = [
    # Guide §26
    ("Guaranteed results.", "R13"),
    ("Results are guaranteed with this program.", "R13"),
    ("Everyone qualifies.", "R17"),
    ("It's completely safe.", "R14"),
    ("There is no risk.", "R41"),
    ("There are no side effects.", "R14"),
    ("This is the best treatment for you.", "R41"),
    ("You should take the higher dose.", "R30"),
    ("You need a higher dose.", "R41"),
    ("You need to take it daily.", "R41"),
    ("Stop taking it.", "R41"),
    ("This is definitely caused by the medication.", "R41"),
    ("This will fix your problem.", "R41"),
    ("It's better than your doctor's treatment.", "R41"),
    ("WellPeps is safer than other clinics.", "R41"),
    ("We're better than the big brands.", "R41"),
    ("Our program is more effective than theirs.", "R41"),
    # Guide §7 prohibited results / safety language
    ("You'll lose 30 pounds.", "R13"),
    ("This will definitely work.", "R14"),
    # Guide §9 Do Not Say
    ("That's not true.", "R40"),
    ("You're wrong.", "R40"),
    ("You must have misunderstood.", "R40"),
    ("Nobody else has complained.", "R40"),
    ("Delete your comment and DM me.", "R40"),
    # Operations Manual §19 / §10.3 Do Not Say
    ("Yes, almost everyone can get it.", "R41"),
    ("Most people lose at least 25 pounds.", "R41"),
    ("Start low and increase after a month.", "R41"),
    ("Yes, skip it until you feel better.", "R41"),
    ("Your labs look fine.", "R41"),
    ("Keep taking your medication.", "R41"),
    ("Yes, it's very safe.", "R41"),
    ("That would probably be better for you.", "R41"),
    ("That's probably unrelated.", "R41"),
    ("Use WellPeps, we're the best.", "R41"),
    # Guide templates: an unfilled slot
    ("Just to clarify, [insert current approved factual correction].", "R42"),
    ("We have an article: [approved link].", "R42"),
]


@pytest.mark.parametrize("text, rule", AVOID, ids=[a[0] for a in AVOID])
def test_words_and_phrases_to_avoid_are_red(text, rule):
    assert rule in _red_rules(text)


# The guide's own preferred alternatives and approved wording, and ordinary
# sentences that share words with a rule, must not trip it.
BENIGN = [
    ("Results vary by individual, and no specific amount of weight loss can be guaranteed.", "R13"),
    ("Completing an assessment does not guarantee treatment.", "R13"),
    ("No treatment outcome can be guaranteed.", "R13"),
    ("No specific outcome can be guaranteed.", "R13"),
    ("All medications can have potential risks and benefits.", "R41"),
    ("Just to clarify, treatment isn't automatically provided after someone completes an assessment.", "R40"),
    ("I can't advise you to start, stop or change a medication.", "R41"),
    ("Please contact your prescribing healthcare provider.", "R41"),
    ("If you need help, our support team can route this.", "R41"),
    ("Whatever you need, a licensed provider can explain the options.", "R41"),
    ("A licensed provider can determine whether a treatment may be appropriate for you.", "R41"),
    ("I can't interpret laboratory results.", "R41"),
    ("Treatment decisions require an individualized evaluation by a licensed healthcare provider.", "R41"),
    ("Everyone's experience can be different.", "R17"),
    ("Thanks for the feedback, I'll share it with the team.", "R40"),
    ("Regardless of the company, it's important that the evaluation is conducted by a licensed clinician.",
     "R41"),
    ("Nobody should have to wait weeks for a reply from support.", "R40"),
]


@pytest.mark.parametrize("text, rule", BENIGN, ids=[b[0][:50] for b in BENIGN])
def test_benign_and_approved_wording_does_not_trip_the_rule(text, rule):
    assert rule not in _red_rules(text)


def test_you_need_is_yellow_when_it_is_not_a_treatment_directive():
    result = compliance_filter(OPEN + "Here's everything you need to know about the process.", "facebook", IDS)
    assert result.tier == "yellow"
    assert any(h.rule_id == "R41" for h in result.hits) and not result.red_hits


def test_never_write_list_includes_the_guides_do_not_say_lines():
    from harvey.agents.drafter import never_write_phrases

    phrases = never_write_phrases()
    for line in ("That's not true.", "You must have misunderstood.", "Stop taking it.", "no risk",
                 "Most people lose at least 25 pounds"):
        assert line in phrases


# --- Disclosure forms (Guide §4) ------------------------------------------------------------------


@pytest.mark.parametrize("opening", [
    "I work with WellPeps.",
    "I'm part of the WellPeps team.",
    "Hi, I'm Dana — I work with WellPeps.",
    "Full disclosure: I work with WellPeps, so I'm not neutral.",   # older form: still allowed
    "Disclosure: I work with WellPeps, so I am not neutral.",
])
def test_employee_and_official_disclosures_are_clean(opening):
    result = compliance_filter(f"{opening} Thanks for asking.", "reddit", IDS)
    assert has_disclosure(opening)
    assert not [h for h in result.hits if h.kind == "disclosure"]


@pytest.mark.parametrize("opening", ["I'm a WellPeps Brand Ambassador.", "I partner with WellPeps."])
def test_ambassador_and_partner_forms_disclose_but_are_yellow(opening):
    result = compliance_filter(f"{opening} Thanks for asking.", "reddit", IDS)
    assert result.tier == "yellow"
    assert any(h.rule_id == "R47" for h in result.hits)


def test_a_buried_disclosure_is_red():
    result = compliance_filter("Great question. I work with WellPeps. Thanks.", "reddit", IDS)
    assert "R3" in {h.rule_id for h in result.red_hits}


def test_default_disclosure_is_the_guides_primary_form():
    assert knowledge.compliance_rules().disclosure.default_form == "I work with WellPeps."


# --- FINALIZE ---------------------------------------------------------------------------------------


def test_a_cited_claim_with_an_open_finalize_item_is_yellow_with_what_is_missing():
    text = OPEN + knowledge.claims_by_id()["CLM-AMG-APPX-COMPLAINT"].text
    result = compliance_filter(text, "reddit", ["CLM-AMG-04-WORK-WITH", "CLM-AMG-APPX-COMPLAINT"])
    finalize = [h for h in result.hits if h.rule_id == "R42" and h.kind == "finalize"]
    assert result.tier == "yellow" and finalize and "support channel" in finalize[0].reason


# --- Link rules (Guide §14) -------------------------------------------------------------------------


def test_naked_link_is_red():
    assert naked_link(f"I work with WellPeps. {GUIDE_URL}")
    assert "R43" in _red_rules(GUIDE_URL, GUIDE_IDS)
    assert "R43" in _red_rules(f"Here: {GUIDE_URL} hope it helps", GUIDE_IDS)


def test_a_link_after_a_real_answer_is_not_naked():
    text = ("A few things worth checking with any provider are whether a licensed clinician reviews you "
            f"and what follow-up is included. Our free guide covers more: {GUIDE_URL}")
    assert not naked_link(OPEN + text)
    assert "R43" not in _red_rules(text, GUIDE_IDS)


ANSWER = ("In general, telehealth programs use licensed healthcare providers to review a person's health "
          "information and determine whether treatment is appropriate.")


@pytest.mark.parametrize("context, red, yellow", [
    (ReplyContext(), set(), set()),
    (ReplyContext(community_id="reddit:r/x", participation="unknown"), {"R44"}, {"R44"}),
    (ReplyContext(community_id="reddit:r/x", participation="allowed", links_allowed=False), {"R44"}, set()),
    (ReplyContext(community_id="reddit:r/x", participation="with_permission"), {"R44"}, {"R44"}),
    (ReplyContext(community_id="reddit:r/x", participation="with_permission", permission_obtained=True,
                  links_allowed=True), set(), set()),
    (ReplyContext(community_id="reddit:r/x", participation="allowed", links_allowed=True,
                  repeated_link_ids=frozenset({"LNK-GUIDE-GLP-1-WEIGHT-LOSS"})), set(), {"R43"}),
    (ReplyContext(community_id="reddit:r/x", participation="prohibited"), {"R44"}, set()),
])
def test_community_context_for_a_reply_with_a_link(context, red, yellow):
    text = f"{OPEN}{ANSWER} Our free guide covers questions to ask: {GUIDE_URL}"
    result = compliance_filter(text, "reddit", GUIDE_IDS, context=context)
    red_ids = {h.rule_id for h in result.red_hits}
    yellow_ids = {h.rule_id for h in result.hits if h not in result.red_hits}
    assert red <= red_ids and not ({"R43", "R44"} - red) & red_ids
    assert yellow <= yellow_ids and not ({"R43", "R44"} - yellow) & yellow_ids


def test_eighty_twenty_is_a_planning_metric_never_a_per_reply_flag():
    """Protocol §1: "The 80/20 guideline is a planning principle, not a per-reply
    quota"; Operations Manual §2.1. A promotional reply in a busy community is
    not flagged on the share alone."""
    import dataclasses

    assert "over_eighty_twenty" not in {f.name for f in dataclasses.fields(ReplyContext)}
    text = f"{OPEN}{ANSWER} Our free guide covers questions to ask: {GUIDE_URL}"
    result = compliance_filter(text, "reddit", GUIDE_IDS,
                               context=ReplyContext(community_id="reddit:r/x", participation="allowed",
                                                    links_allowed=True))
    assert not [h for h in result.hits if h.kind == "eighty_twenty" or h.rule_id == "R45"]


def test_unknown_community_without_a_link_is_only_yellow():
    context = ReplyContext(community_id="reddit:r/x", participation="unknown")
    result = compliance_filter(OPEN + ANSWER, "reddit", ["CLM-AMG-04-WORK-WITH", "CLM-AMG-17-TELEHEALTH"],
                               context=context)
    assert result.tier == "yellow"
    assert any("community rules unverified" in h.reason for h in result.hits)


def test_with_permission_not_obtained_blocks_promotion_but_not_education():
    context = ReplyContext(community_id="facebook:groups/g", participation="with_permission")
    education = compliance_filter(OPEN + ANSWER, "facebook", ["CLM-AMG-04-WORK-WITH"], context=context)
    promo = compliance_filter(OPEN + ANSWER + " Get started with our free guide.", "facebook",
                              ["CLM-AMG-04-WORK-WITH"], context=context)
    assert education.tier == "yellow"
    assert "R44" in {h.rule_id for h in promo.red_hits}
