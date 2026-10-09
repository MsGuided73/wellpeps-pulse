"""Rules of engagement: which situation a triage result is, whether Pulse
drafts, and how that combines with escalation (docs/RULES-OF-ENGAGEMENT.md)."""

import pytest

from harvey import engagement, knowledge
from harvey.agents.triager import TriageAnswer, _to_triage, build_prompt, route_status
from harvey.escalation import SAFETY_WATCH, SEVERE_KINDS, escalation_kind, is_serious
from harvey.models import Category, Mention, MentionStatus, Platform, Triage, Urgency
from harvey.models.mention import TRIAGE_SUBTYPES

WP = "wellpeps"


def _t(category=Category.OTHER, subtype="", subject_type="category", **kw) -> Triage:
    return Triage(mention_id=1, relevant=True, category=category, subtype=subtype,
                  subject_type=subject_type, **kw)


# (triage, situation id, reply mode, escalation kind, status after triage)
CASES = [
    # Adverse events: the guide's boundary reply is drafted AND escalated.
    (_t(Category.ADVERSE_EVENT, subject_type=WP), "adverse_event", "boundary_only", "adverse_event",
     MentionStatus.TRIAGED),
    (_t(Category.ADVERSE_EVENT, "medication_change", WP), "adverse_event_dose_change", "boundary_only",
     "adverse_event", MentionStatus.TRIAGED),
    # ... not about WellPeps: the same situation, but nobody is paged (only
    # WellPeps' own incidents page; user decision 2026-10-09, docs/REVISIONS-LOG.md
    # R-13, pending WellPeps clinical confirmation). The review gate
    # (gate:not_ours, tests/test_review_gates.py) keeps it from being drafted.
    (_t(Category.ADVERSE_EVENT), "adverse_event", "boundary_only", None, MentionStatus.TRIAGED),
    # Emergency: emergency line + escalate, whatever the category.
    (_t(Category.QUESTION, "emergency", WP), "emergency", "boundary_only", "adverse_event",
     MentionStatus.TRIAGED),
    # A serious report not about WellPeps: the safety watch (never paged, off the reply queue).
    (_t(Category.ADVERSE_EVENT, "emergency"), "emergency", "boundary_only", "safety_watch",
     MentionStatus.ESCALATED),
    # Media: routing line only + escalate (legal owner until a media contact exists).
    (_t(Category.QUESTION, "media_inquiry", WP), "media_inquiry", "boundary_only", "legal",
     MentionStatus.TRIAGED),
    (_t(Category.OTHER, "media_inquiry"), "media_other", "no_reply", None, MentionStatus.TRIAGED),
    # Legal / regulatory: no substantive reply + escalate.
    (_t(Category.LEGAL_REGULATORY, subject_type=WP), "legal_regulatory", "no_reply", "legal",
     MentionStatus.ESCALATED),
    (_t(Category.COMPLAINT, "legal_threat", WP), "legal_subtype", "no_reply", "legal",
     MentionStatus.ESCALATED),
    (_t(Category.OTHER, "regulatory_contact", WP), "legal_subtype", "no_reply", "legal",
     MentionStatus.ESCALATED),
    # Privacy concern: no public reply (minimal; the privacy owner decides) + escalate.
    (_t(Category.PRIVACY, subject_type=WP), "privacy_concern", "no_reply", "privacy", MentionStatus.ESCALATED),
    # Billing / complaints: Template C, billing also escalates.
    (_t(Category.BILLING_FRAUD, subject_type=WP), "billing_complaint", "boundary_only", "billing_fraud",
     MentionStatus.TRIAGED),
    (_t(Category.COMPLAINT, subject_type=WP), "complaint", "boundary_only", None, MentionStatus.TRIAGED),
    (_t(Category.COMPLAINT, subject_type="competitor"), "complaint_other", "no_reply", None,
     MentionStatus.TRIAGED),
    # Poster's own medical details: privacy response.
    (_t(Category.QUESTION, "personal_medical_info", WP), "personal_medical_info", "boundary_only", None,
     MentionStatus.TRIAGED),
    (_t(Category.QUESTION, "records_dm_request", WP), "records_dm_request", "boundary_only", None,
     MentionStatus.TRIAGED),
    # Individual medical questions to WellPeps: Template B boundary.
    (_t(Category.QUESTION, "dose_question", WP), "dose_question", "boundary_only", None, MentionStatus.TRIAGED),
    (_t(Category.QUESTION, "lab_question", WP), "lab_question", "boundary_only", None, MentionStatus.TRIAGED),
    (_t(Category.QUESTION, "individual_treatment", WP), "individual_treatment", "boundary_only", None,
     MentionStatus.TRIAGED),
    (_t(Category.QUESTION, "dose_question"), "individual_third_party", "no_reply", None, MentionStatus.TRIAGED),
    # Competitors: neutral guide responses.
    (_t(Category.PURCHASE_INTENT, "competitor_comparison", WP), "competitor_comparison_wellpeps",
     "boundary_only", None, MentionStatus.TRIAGED),
    (_t(Category.PURCHASE_INTENT, "competitor_comparison", "competitor"), "competitor_comparison", "draft",
     None, MentionStatus.TRIAGED),
    (_t(Category.PRAISE, "competitor_praise", "competitor"), "competitor_praise", "draft", None,
     MentionStatus.TRIAGED),
    # Misinformation about WellPeps: Template D draft; general misinformation: no reply.
    (_t(Category.MISINFORMATION, "misinformation_about_wellpeps", WP), "misinformation_about_wellpeps",
     "draft", None, MentionStatus.TRIAGED),
    (_t(Category.MISINFORMATION), "misinformation", "no_reply", None, MentionStatus.TRIAGED),
    (_t(Category.QUESTION, "has_anyone_used_wellpeps", WP), "has_anyone_used_wellpeps", "draft", None,
     MentionStatus.TRIAGED),
    # Abusive thread: stop.
    (_t(Category.COMPLAINT, "abusive", WP), "abusive", "stop", None, MentionStatus.TRIAGED),
    (_t(Category.QUESTION), "question", "draft", None, MentionStatus.TRIAGED),
    (_t(Category.PRAISE), "default", "draft", None, MentionStatus.TRIAGED),
    # Competitor / switching protocol: its classification decides first in scope.
    (_t(Category.QUESTION, competitor="Ro", protocol_decision="do_not_engage"), "protocol_do_not_engage",
     "no_reply", None, MentionStatus.TRIAGED),
    (_t(Category.QUESTION, competitor="Ro", protocol_decision="hold"), "protocol_hold", "no_reply", None,
     MentionStatus.TRIAGED),
    (_t(Category.COMPLAINT, competitor="Hims & Hers", protocol_decision="monitor_only"), "protocol_monitor_only",
     "no_reply", None, MentionStatus.TRIAGED),
    # HOLD in an unverified community still routes safety internally ([CP] §4 step 1).
    # About another provider (user decision 2026-10-09, docs/REVISIONS-LOG.md R-13,
    # pending WellPeps clinical confirmation): a serious harm report goes on the
    # safety watch; anything else is trends only (no escalation).
    (_t(Category.QUESTION, competitor="Ro", protocol_decision="hold", protocol_route="adverse_event",
        intents=["possible_serious_harm"]), "protocol_hold", "no_reply", "safety_watch", MentionStatus.ESCALATED),
    (_t(Category.QUESTION, competitor="Ro", protocol_decision="hold", protocol_route="adverse_event"),
     "protocol_hold", "no_reply", None, MentionStatus.TRIAGED),
    (_t(Category.QUESTION, "dose_question", competitor="Ro", protocol_decision="escalate",
        protocol_route="adverse_event"), "protocol_escalate_harm_dose", "boundary_only", None,
     MentionStatus.TRIAGED),
    (_t(Category.QUESTION, competitor="Ro", protocol_decision="escalate", protocol_route="adverse_event",
        intents=["possible_serious_harm"]), "protocol_escalate_harm", "boundary_only", "safety_watch",
     MentionStatus.ESCALATED),
    (_t(Category.QUESTION, competitor="Ro", protocol_decision="escalate", protocol_route="adverse_event"),
     "protocol_escalate_harm", "boundary_only", None, MentionStatus.TRIAGED),
    (_t(Category.QUESTION, competitor="Ro", protocol_decision="escalate", protocol_route="legal"),
     "protocol_escalate_legal", "no_reply", None, MentionStatus.TRIAGED),
    (_t(Category.QUESTION, "personal_medical_info", competitor="Hims & Hers", protocol_decision="escalate",
        protocol_route="privacy"), "protocol_escalate_privacy", "boundary_only", None, MentionStatus.TRIAGED),
    # A WellPeps complaint in the protocol's scope: the same Template C reply, support route.
    (_t(Category.COMPLAINT, subject_type=WP, protocol_decision="escalate", protocol_route="support",
        intents=["wellpeps_complaint"]), "protocol_escalate_support", "boundary_only", None,
     MentionStatus.TRIAGED),
    (_t(Category.QUESTION, competitor="Ro", protocol_decision="escalate", protocol_route="support",
        intents=["wellpeps_complaint"]), "protocol_escalate_support", "boundary_only", None, MentionStatus.TRIAGED),
    (_t(Category.QUESTION, "lab_question", competitor="Hims & Hers", protocol_decision="clinical_caution"),
     "protocol_clinical_labs", "boundary_only", None, MentionStatus.TRIAGED),
    (_t(Category.QUESTION, "medication_change", competitor="Noom Med", protocol_decision="clinical_caution"),
     "protocol_clinical_change", "boundary_only", None, MentionStatus.TRIAGED),
    (_t(Category.QUESTION, competitor="Ro", protocol_decision="clinical_caution"), "protocol_clinical_caution",
     "boundary_only", None, MentionStatus.TRIAGED),
    (_t(Category.PURCHASE_INTENT, "clinic_recommendation", competitor="Ro",
        protocol_decision="appropriate_alternative"), "protocol_appropriate_alternative", "draft", None,
     MentionStatus.TRIAGED),
    (_t(Category.QUESTION, "competitor_comparison", WP, competitor="Hims & Hers",
        protocol_decision="educational_only"), "protocol_educational_only", "draft", None, MentionStatus.TRIAGED),
]


@pytest.mark.parametrize("triage, situation, mode, kind, status", CASES,
                         ids=[f"{c[1]}-{c[0].category.value}-{c[0].subtype or 'none'}" for c in CASES])
def test_situation_reply_escalation_and_status(triage, situation, mode, kind, status):
    assert engagement.situation_of(triage).id == situation
    assert engagement.reply_mode(triage) == mode
    assert escalation_kind(triage) == kind
    assert route_status(triage) is status


@pytest.mark.parametrize("marker", ["safety_screen:minor: 16", "safety_screen:self_harm: x",
                                    "safety_screen:failed", "triage_failed"])
def test_minors_and_failed_screens_never_get_a_reply_even_a_boundary_one(marker):
    triage = _t(Category.ADVERSE_EVENT, subject_type=WP, urgency_reason=marker)
    assert engagement.reply_mode(triage) == "no_reply"
    assert route_status(triage) is MentionStatus.ESCALATED
    minor_question = _t(Category.QUESTION, "dose_question", WP, urgency_reason=marker)
    assert engagement.drafts_reply(minor_question) is False


def test_every_situation_escalation_agrees_with_escalation_kind():
    # The yaml's `escalate` documents what harvey/escalation.py decides for a
    # post about WellPeps. About anyone else, only a serious adverse event is
    # escalated, onto the never-paged safety watch (user decision 2026-10-09,
    # docs/REVISIONS-LOG.md R-13, pending WellPeps clinical confirmation).
    checked = {WP: 0, "category": 0}
    for sit in knowledge.engagement_guide().situations:
        cats = [c for c in sit.match.categories if c != "*"] or ["other"]
        subs = [s for s in sit.match.subtypes if s != "*"] or [""]
        decision = next((d for d in sit.match.decisions if d != "*"), "")
        route = next((r for r in sit.match.routes if r != "*"), "")
        subjects = {"wellpeps": [WP], "other": ["category"], "any": [WP, "category"]}[sit.match.subject]
        for subject in subjects:
            triage = _t(Category(cats[0]), subs[0], subject, protocol_decision=decision, protocol_route=route)
            if engagement.situation_of(triage).id != sit.id:
                continue  # shadowed by an earlier, more specific situation for this probe
            if subject == WP:
                expected = sit.escalate
            else:
                expected = (SAFETY_WATCH if sit.escalate in SEVERE_KINDS and escalation_kind(
                    triage.model_copy(update={"subject_type": WP})) == "adverse_event" and is_serious(triage)
                    else None)
            assert escalation_kind(triage) == expected, (sit.id, subject)
            checked[subject] += 1
    assert checked[WP] and checked["category"]


def test_clinical_approval_for_adverse_events_and_emergencies():
    assert engagement.requires_clinical_approval(_t(Category.ADVERSE_EVENT, subject_type=WP))
    assert engagement.requires_clinical_approval(_t(Category.OTHER, "emergency"))
    assert not engagement.requires_clinical_approval(_t(Category.QUESTION, "dose_question", WP))
    assert not engagement.requires_clinical_approval(None)


def test_viral_wellpeps_complaint_is_still_paged_and_gets_template_c():
    triage = _t(Category.COMPLAINT, subject_type=WP, urgency=Urgency.URGENT)
    assert escalation_kind(triage) == "viral_negative"
    assert engagement.situation_of(triage).template == "C"
    assert route_status(triage) is MentionStatus.TRIAGED


# --- Triage agent: subtype ------------------------------------------------------------


def _answer(**kw) -> TriageAnswer:
    base = dict(relevant=True, subject_type="wellpeps", category="question", sentiment=0.0,
                sentiment_label="neutral", urgency="normal", reply_appropriate=True)
    return TriageAnswer.model_validate({**base, **kw})


def test_triage_answer_subtype_is_optional_and_normalised():
    mention = Mention(id=3, platform=Platform.REDDIT, external_id="e", url="https://www.reddit.com/r/x/comments/e/",
                      text="what dose should I take?")
    assert _to_triage(_answer(), mention, "m").subtype == ""
    assert _to_triage(_answer(subtype="dose_question"), mention, "m").subtype == "dose_question"
    assert _to_triage(_answer(subtype="please_reply_now"), mention, "m").subtype == ""
    assert _to_triage(_answer(subtype=None), mention, "m").subtype == ""


def test_triage_prompt_lists_every_subtype():
    mention = Mention(id=3, platform=Platform.REDDIT, external_id="e", url="https://www.reddit.com/r/x/comments/e/",
                      text="hello")
    prompt = build_prompt(mention, nonce="n")
    for subtype in TRIAGE_SUBTYPES:
        assert f"`{subtype}`" in prompt


def test_triage_answer_protocol_inputs_are_optional_and_normalised():
    mention = Mention(id=3, platform=Platform.REDDIT, external_id="e", url="https://www.reddit.com/r/x/comments/e/",
                      text="anyone recommend another provider?")
    plain = _to_triage(_answer(), mention, "m")
    assert plain.intents == [] and plain.unmet_need == "" and plain.need_clarity is None
    full = _to_triage(_answer(intents=["alternatives_requested", "made_up", "ALTERNATIVES_REQUESTED"],
                              unmet_need="provider_access", need_clarity=5, useful_contribution=-1),
                      mention, "m")
    assert full.intents == ["alternatives_requested"]
    assert full.unmet_need == "provider_access"
    assert (full.need_clarity, full.useful_contribution) == (2, 0)
    assert _to_triage(_answer(unmet_need="vibes"), mention, "m").unmet_need == ""


def test_triage_prompt_lists_every_protocol_intent_and_need():
    from harvey.models.mention import PROTOCOL_INTENTS, PROTOCOL_NEEDS

    mention = Mention(id=3, platform=Platform.REDDIT, external_id="e", url="https://www.reddit.com/r/x/comments/e/",
                      text="hello")
    prompt = build_prompt(mention, nonce="n")
    for tag in (*PROTOCOL_INTENTS, *PROTOCOL_NEEDS):
        assert f"`{tag}`" in prompt
