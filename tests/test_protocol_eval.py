"""The Competitor Mentions and Provider Switching Protocol's 25 training
examples as an evaluation set ([CP] §12-§14).

No model calls: each example supplies what triage would (intent tags, need,
the two judgement scores) and the community rule status; the test runs the
deterministic parts of Pulse and checks the protocol's expected decision, the
opportunity score, brand mode and escalation route, the Pulse situation (and
so whether and what Pulse drafts), and that the protocol's suggested reply
passes the compliance filter while its "Do not say" line is red.
"""

from pathlib import Path

import pytest
import yaml

from harvey import engagement, knowledge, protocol
from harvey.compliance import compliance_filter
from harvey.escalation import SEVERE_KINDS, escalation_kind
from harvey.models import Category, Mention, Platform, Triage

FIXTURE = Path(__file__).parent / "fixtures" / "protocol_examples.yaml"
EXAMPLES = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))["examples"]
IDS = [f"{e['n']:02d}-{e['title']}" for e in EXAMPLES]
NOT_CONTENT = {"R2", "R3", "CLAIMS"}   # disclosure / claim bookkeeping, not what the line says


class _Community:
    def __init__(self, participation: str):
        self.id = "reddit:r/example" if participation != "none" else ""
        self.participation = participation
        self.permission_obtained = False
        self.links_allowed = None


def _triage(example) -> Triage:
    i = example["inputs"]
    return Triage(mention_id=example["n"], relevant=True, category=Category(i["category"]),
                  subtype=i.get("subtype", ""), subject_type=i.get("subject_type", ""),
                  competitor=i.get("competitor", "") or "", intents=i.get("intents", []),
                  unmet_need=i.get("unmet_need", ""), need_clarity=i.get("need_clarity"),
                  useful_contribution=i.get("useful_contribution"), reply_appropriate=True)


def _record(example) -> protocol.ProtocolRecord:
    i = example["inputs"]
    inp = protocol.input_for(_triage(example), _Community(i.get("participation", "allowed")),
                             prior_clinical_in_thread=bool(i.get("prior_clinical")), text=example["post"])
    return protocol.decide(inp)


def _applied(example) -> Triage:
    """The triage as Pulse stores it (scope, community and thread applied)."""
    i = example["inputs"]
    triage = _triage(example)
    rec = _record(example)
    if not protocol.applies(triage, _mention(example), bool(i.get("prior_clinical"))):
        return triage
    return triage.model_copy(update={"protocol_decision": rec.decision, "protocol_route": rec.route,
                                     "opportunity_score": rec.score, "protocol": rec.as_dict()})


def _mention(example) -> Mention:
    return Mention(id=example["n"], platform=Platform.REDDIT, external_id=f"cp{example['n']}",
                   url=f"https://www.reddit.com/r/example/comments/cp{example['n']}/", text=example["post"])


def test_the_fixture_has_all_25_examples():
    assert [e["n"] for e in EXAMPLES] == list(range(1, 26))


@pytest.mark.parametrize("example", EXAMPLES, ids=IDS)
def test_decision_matches_the_protocol(example):
    record = _record(example)
    assert record.decision == example["expected"]["decision"], record.rationale


@pytest.mark.parametrize("example", EXAMPLES, ids=IDS)
def test_opportunity_is_scored_only_after_the_gates(example):
    expected = example["expected"]
    record = _record(example)
    assert record.score == expected["score"]
    if expected["score"] is None:
        assert record.components is None and record.band is None
    else:
        assert record.decision in protocol.SCORED_DECISIONS
        assert set(record.components) == {"alternative_intent", "need_clarity", "capability_fit",
                                          "useful_contribution"}
        assert all(0 <= v <= 2 for v in record.components.values())
        assert record.band == expected["band"]


@pytest.mark.parametrize("example", EXAMPLES, ids=IDS)
def test_brand_mode_disclosure_and_route(example):
    expected = example["expected"]
    record = _record(example)
    assert record.brand_mode == expected["brand_mode"]
    assert record.route == expected["route"]
    if "disclosure" in expected:
        assert record.disclosure == expected["disclosure"]
    if record.brand_mode == protocol.BRAND_NONE:
        assert record.disclosure == ""
    else:
        assert record.disclosure in ("I work with WellPeps.", "I'm part of the WellPeps team.")
    # [CP] §8: no resource in safety or clinical handling, and no verified link -> none.
    assert record.resource == "none"
    assert protocol.resource_for(record, links_allowed=True) == "none"   # no registry link is live yet


@pytest.mark.parametrize("example", EXAMPLES, ids=IDS)
def test_pulse_situation_and_what_it_drafts(example):
    expected = example["expected"]
    triage = _applied(example)
    situation = engagement.situation_of(triage)
    if "situation" in expected:
        assert situation.id == expected["situation"]
    mode = engagement.reply_mode(triage)
    if expected["decision"] in protocol.NO_DRAFT_DECISIONS or (expected["decision"] == "escalate"
                                                                and expected["route"] == "legal"):
        assert mode == "no_reply"
    if expected["decision"] in ("clinical_caution", "escalate") and mode != "no_reply":
        # Approved boundary / holding language only: never a model draft, never a link.
        assert mode == "boundary_only" and situation.link_policy == "none"
        text, claim_ids = engagement.approved_reply(situation, triage)
        result = compliance_filter(text, "reddit", claim_ids)
        assert not result.red_hits, result.red_hits
        assert "http" not in text and "$" not in text
    if expected["decision"] in protocol.SCORED_DECISIONS:
        assert mode == "draft"
        if expected["decision"] == "educational_only" and protocol.applies(_triage(example), _mention(example)):
            assert situation.education_only


@pytest.mark.parametrize("example", EXAMPLES, ids=IDS)
def test_escalate_reaches_the_designated_owner(example):
    """[CP] §5: serious or uncertain safety reports reach human safety review;
    a symptomatic person asking for a new provider stays in the safety queue."""
    triage = _applied(example)
    route = example["expected"]["route"]
    if route in SEVERE_KINDS:
        assert escalation_kind(triage) == route
    if example["expected"]["decision"] != "escalate":
        assert triage.protocol_route == ""


@pytest.mark.parametrize("example", [e for e in EXAMPLES if e["reply"]],
                         ids=[i for e, i in zip(EXAMPLES, IDS) if e["reply"]])
def test_suggested_reply_passes_the_filter(example):
    claim = "CLM-AMG-04-TEAM" if example["reply"].startswith("I'm part of") else "CLM-AMG-04-WORK-WITH"
    result = compliance_filter(example["reply"], "reddit", [claim])
    assert not result.red_hits, [(h.rule_id, h.match, h.reason) for h in result.red_hits]
    words = len(example["reply"].split())
    assert words <= 90   # [CP] §7: 40-90 words for routine replies, shorter boundary replies


@pytest.mark.parametrize("example", [e for e in EXAMPLES if e["do_not_say_kind"] == "reply"],
                         ids=[i for e, i in zip(EXAMPLES, IDS) if e["do_not_say_kind"] == "reply"])
def test_do_not_say_is_red(example):
    text = f"I work with WellPeps. {example['do_not_say']}"
    result = compliance_filter(text, "reddit", ["CLM-AMG-04-WORK-WITH"])
    content = {h.rule_id for h in result.red_hits} - NOT_CONTENT
    assert content, f"not caught: {example['do_not_say']!r}"


def test_do_not_say_actions_are_impossible_in_pulse():
    """Example 14's "Do not say" is an operator action (posting in a community
    with unknown rules). Pulse drafts nothing for a HOLD and has no posting
    path at all."""
    example = next(e for e in EXAMPLES if e["do_not_say_kind"] == "action")
    assert engagement.reply_mode(_applied(example)) == "no_reply"
    import harvey.review as review

    assert not [name for name in dir(review) if name.startswith(("publish", "post_reply", "send_", "dm_"))]


def test_symptoms_plus_alternatives_never_get_a_switching_draft():
    """[CP] §14 Expected override, whatever the community or the opportunity."""
    for participation in ("allowed", "with_permission", "none"):
        inp = protocol.ProtocolInput(category="question", competitor="Ro", participation=participation,
                                     intents=frozenset({"alternatives_requested", "individual_clinical_concern"}),
                                     unmet_need="continuity", need_clarity=2, useful_contribution=2)
        record = protocol.decide(inp)
        assert record.decision == protocol.CLINICAL_CAUTION and record.score is None
        assert record.brand_mode != protocol.BRAND_OPTION


def test_missing_rules_hold_prohibited_rules_do_not_engage_venting_monitors():
    """[CP] §14 Expected abstention."""
    base = dict(category="question", competitor="Ro", intents=frozenset({"alternatives_requested"}),
                unmet_need="provider_access", need_clarity=2, useful_contribution=2)
    assert protocol.decide(protocol.ProtocolInput(**base, participation="unknown")).decision == protocol.HOLD
    assert protocol.decide(protocol.ProtocolInput(**base, participation="prohibited")).decision == \
        protocol.DO_NOT_ENGAGE
    vent = dict(base, intents=frozenset({"venting_only"}))
    assert protocol.decide(protocol.ProtocolInput(**vent, participation="allowed")).decision == \
        protocol.MONITOR_ONLY


def test_safety_is_routed_internally_even_when_the_public_reply_is_held():
    """[CP] §4 step 1: unknown rules -> HOLD, but safety observations may still
    be routed internally (never used to justify promotional posting)."""
    inp = protocol.ProtocolInput(category="question", competitor="Ro", participation="unknown",
                                 intents=frozenset({"possible_serious_harm", "alternatives_requested"}))
    record = protocol.decide(inp)
    assert record.decision == protocol.HOLD and record.route == "adverse_event" and record.safety_routed
    assert record.queue_rank == 0                     # [CP] §9: safety incidents first


def test_with_permission_not_obtained_downgrades_an_alternative_to_education():
    inp = protocol.ProtocolInput(category="question", competitor="Ro", participation="with_permission",
                                 intents=frozenset({"alternatives_requested"}), unmet_need="provider_access",
                                 need_clarity=2, useful_contribution=2)
    record = protocol.decide(inp)
    assert record.decision == protocol.EDUCATIONAL_ONLY and record.brand_mode == protocol.BRAND_AFFILIATION


def test_ongoing_support_is_never_a_response_time_fact():
    """[CP] §14 Expected grounding: approved ongoing support fits provider
    access only partly (no response-time claim exists)."""
    cap = protocol.capability("provider_access")
    assert cap.fit == 1 and cap.claim_ids == ("CLM-LR-02-ONGOING-SUPPORT",)
    assert protocol.capability("lab_testing_terms").fit == 0
    assert protocol.capability("medication_availability").fit == 0


def test_capability_counts_only_publishable_claims():
    assert protocol.capability("care_process", publishable=set()).fit == 0
    assert protocol.capability("care_process", publishable={"CLM-LR-02-ONGOING-SUPPORT"}).fit == 2


def test_the_record_has_the_required_fields_and_no_post_text():
    """[CP] §10 required output record; minimum data ([CP] §2)."""
    example = EXAMPLES[0]
    data = _record(example).as_dict()
    for key in ("decision", "rationale", "rules", "intents", "unmet_need", "risks", "opportunity",
                "brand_mode", "disclosure", "resource", "evidence", "required_review", "blockers", "version"):
        assert key in data
    assert data["risks"]["competitor_claim_risk"] == "medium"     # the protocol's sample record
    assert data["risks"]["clinical_flag"] is False
    assert "publish" in data["publishing"]
    assert example["post"] not in repr(data) and "sick of Ro" not in repr(data)


def test_scoring_bands_follow_the_protocol():
    cfg = knowledge.engagement_guide().switching_protocol
    assert (cfg.high_min, cfg.moderate_min) == (6, 3)
