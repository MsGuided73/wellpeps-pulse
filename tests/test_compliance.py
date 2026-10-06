"""Deterministic compliance filter for draft replies (no Claude calls)."""

import shutil

import pytest
import yaml

from harvey import knowledge
from harvey.compliance import GateResult, Hit, compliance_filter
from harvey.paths import PROJECT_ROOT

CONFIG_DIR = PROJECT_ROOT / "config"

DISCLOSURE = "Disclosure: I work with WellPeps, so I am not neutral."
CLEAN = (
    DISCLOSURE + " Thanks for asking. A few things worth checking with any provider are whether "
    "you are actually reviewed by a licensed clinician, what follow-up is included, "
    "and how questions are handled between visits."
)


@pytest.fixture(autouse=True)
def _fresh_knowledge(monkeypatch):
    monkeypatch.delenv("PULSE_CONFIG_DIR", raising=False)
    knowledge.reload()
    yield
    knowledge.reload()


@pytest.fixture
def claim_id():
    return knowledge.claims()[0].id


def rule_ids(result: GateResult) -> set[str]:
    return {h.rule_id for h in result.hits}


def kinds(result: GateResult) -> set[str]:
    return {h.kind for h in result.hits}


# --- Green -----------------------------------------------------------------


def test_clean_reply_is_green(claim_id):
    result = compliance_filter(CLEAN, "reddit", [claim_id])
    assert result == GateResult(ok=True, tier="green", hits=[])


def test_approved_disclosure_wording_is_not_blocked(claim_id):
    text = (
        DISCLOSURE + " Compounded medications are not FDA-approved finished drug products. A licensed "
        "healthcare provider determines whether a particular treatment and formulation may "
        "be appropriate for an individual patient."
    )
    result = compliance_filter(text, "facebook", [claim_id])
    assert result.tier != "red", result.hits


# --- Claim IDs -------------------------------------------------------------


def test_no_claim_ids_is_red():
    result = compliance_filter(CLEAN, "reddit", [])
    assert result.ok is False and result.tier == "red"
    assert any("no claim id, no publish" in h.reason.lower() for h in result.hits)


def test_unknown_claim_id_is_red(claim_id):
    result = compliance_filter(CLEAN, "reddit", [claim_id, "NOT-A-CLAIM"])
    assert result.tier == "red"
    assert any(h.kind == "claims" and h.match == "NOT-A-CLAIM" for h in result.hits)


def test_pending_claim_ok_for_drafts(claim_id):
    assert compliance_filter(CLEAN, "reddit", [claim_id]).ok is True


def test_pending_claim_red_when_publishable_required(claim_id):
    result = compliance_filter(CLEAN, "reddit", [claim_id], require_publishable=True)
    assert result.tier == "red"
    assert any(h.kind == "claims" and h.match == claim_id for h in result.hits)


def test_approved_claim_passes_when_publishable_required(tmp_path, monkeypatch):
    for p in CONFIG_DIR.glob("*.yaml"):
        shutil.copy(p, tmp_path / p.name)
    data = yaml.safe_load((tmp_path / "claims.yaml").read_text(encoding="utf-8"))
    data["claims"][0].update(approved_by="Compliance Owner", approved_at="2026-09-01")
    (tmp_path / "claims.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("PULSE_CONFIG_DIR", str(tmp_path))
    knowledge.reload()

    result = compliance_filter(CLEAN, "reddit", [data["claims"][0]["id"]], require_publishable=True)
    assert result.tier == "green", result.hits


# --- Prohibited families (one row per rule family) -------------------------

PROHIBITED_CASES = [
    ("R15", "Our compounded semaglutide is basically generic Ozempic."),
    ("R15", "It has the same active ingredient as Wegovy."),
    ("R15", "Compounded tirzepatide is FDA-approved, so no worries."),
    ("R15", "Compounded versions are safer than the brand pens."),
    ("R14", "This protocol is clinically proven."),
    ("R14", "Totally risk-free and there are no side effects."),
    ("R14", "It is a real miracle for most people."),
    ("R13", "You will lose 30 pounds in three months."),
    ("R13", "Results are guaranteed or your money back."),
    ("R13", "Great for anti-aging and longevity."),
    ("R12", "We are LegitScript certified, so you can trust us."),
    ("R12", "Every order ships from an FDA-approved pharmacy."),
    ("R24", "Check out these before and after photos."),
    ("R24", "Our patients have lost 40 lbs on average."),
    ("R30", "Start with 0.25 mg weekly and increase after a month."),
    ("R22", "Get prescribed today, no doctor visit needed."),
    ("R22", "Approved in minutes by our doctors."),
    ("R23", "Our pharmacy ships next day and we compound everything in-house."),
    ("R8", "Start your assessment at the link."),
    ("R39", "Spots are limited, today only!"),
    ("R37", "Great question #ozempic"),
    ("R2", "Reply from the team, Sarah Jones, MD"),
    ("R2", "As a doctor I can tell you this is fine."),
    ("R10", "Honestly that company is a scam and a total rip-off."),
    ("R19", "WellPeps offers BPC-157 and TB-500 too."),
    ("R20", "Expect harder erections that last all night."),
    ("R32", "What's your current weight? DM us your meds list."),
    ("R11", "Labs included with GLP-1 membership."),
    ("R29", "All our medications are made in America."),
    ("R28", "The at-home test kit is FDA approved."),
]


@pytest.mark.parametrize("rule,text", PROHIBITED_CASES)
def test_prohibited_family_is_red(rule, text, claim_id):
    result = compliance_filter(text, "facebook", [claim_id])
    assert result.ok is False and result.tier == "red"
    assert rule in rule_ids(result), result.hits


def test_certification_claims_allowed_by_toggle(tmp_path, monkeypatch, claim_id):
    for p in CONFIG_DIR.glob("*.yaml"):
        shutil.copy(p, tmp_path / p.name)
    data = yaml.safe_load((tmp_path / "compliance_rules.yaml").read_text(encoding="utf-8"))
    data["toggles"]["allow_certification_claims"] = True
    (tmp_path / "compliance_rules.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("PULSE_CONFIG_DIR", str(tmp_path))
    knowledge.reload()

    result = compliance_filter("We are LegitScript certified.", "facebook", [claim_id])
    assert "R12" not in {h.rule_id for h in result.hits if h.kind == "prohibited"}


# --- Patient confirmation (R31) --------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Sorry about your order, we will fix it.",
        "Your prescription was sent yesterday.",
        "I checked your account and it looks fine.",
        "As our patient you get priority support.",
        "We see that you called last week.",
        "Please ask your provider about that.",
    ],
)
def test_patient_confirmation_is_red(text, claim_id):
    result = compliance_filter(text, "facebook", [claim_id])
    assert result.tier == "red"
    assert "patient_confirmation" in kinds(result)


# --- Medication names (R38 toggle) -----------------------------------------


@pytest.mark.parametrize("text", ["Semaglutide is one option.", "People compare Zepbound and Mounjaro.", "Many ask about NAD+ lately."])
def test_medication_names_red_when_toggle_on(text, claim_id):
    result = compliance_filter(text, "facebook", [claim_id])
    assert result.tier == "red"
    assert any(h.kind == "medication_name" and h.rule_id == "R38" for h in result.hits)


def test_drug_class_allowed_for_education(claim_id):
    result = compliance_filter("GLP-1 medications are a drug class worth learning about.", "facebook", [claim_id])
    assert "medication_name" not in kinds(result)


def test_medication_names_yellow_when_toggle_off(tmp_path, monkeypatch, claim_id):
    for p in CONFIG_DIR.glob("*.yaml"):
        shutil.copy(p, tmp_path / p.name)
    data = yaml.safe_load((tmp_path / "compliance_rules.yaml").read_text(encoding="utf-8"))
    data["toggles"]["forbid_medication_names_in_replies"] = False
    (tmp_path / "compliance_rules.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("PULSE_CONFIG_DIR", str(tmp_path))
    knowledge.reload()

    result = compliance_filter(DISCLOSURE + " Semaglutide is one option.", "facebook", [claim_id])
    assert result.ok is True and result.tier == "yellow"
    assert any(h.kind == "medication_name" and h.rule_id == "R10" for h in result.hits)


# --- Limits ----------------------------------------------------------------


GUIDES_URL = "https://wellpeps.com/smart-patient-guides"


def test_one_link_ok_two_links_red(claim_id):
    one = compliance_filter(CLEAN + f" More at {GUIDES_URL}", "facebook", [claim_id, "CLM-EDU-GUIDES"])
    two = compliance_filter(CLEAN + f" {GUIDES_URL} and www.example.org/x", "facebook",
                            [claim_id, "CLM-EDU-GUIDES"])
    assert not [h for h in one.hits if h.kind == "links" and h.reason != "link not live yet"]
    assert two.tier == "red" and any(h.rule_id == "R6" for h in two.hits)


def test_hashtags_red_on_reddit(claim_id):
    result = compliance_filter(CLEAN + " #wellness", "reddit", [claim_id])
    assert result.tier == "red" and "hashtags" in kinds(result)


def test_hashtag_limit_on_instagram(claim_id):
    five = " ".join(["#wellness", "#telehealth", "#healthyaging", "#metabolichealth", "#WellPeps"])
    ok = compliance_filter(CLEAN + " " + five, "instagram", [claim_id])
    over = compliance_filter(CLEAN + " " + five + " #labwork", "instagram", [claim_id])
    assert "hashtags" not in kinds(ok)
    assert over.tier == "red" and "hashtags" in kinds(over)


@pytest.mark.parametrize("platform,limit", [("reddit", 1500), ("instagram", 2200), ("facebook", 8000), ("x", 1000)])
def test_length_limit_per_platform(platform, limit, claim_id):
    at_limit = ("a " * limit)[:limit]
    over = at_limit + "a"
    assert "length" not in kinds(compliance_filter(at_limit, platform, [claim_id]))
    result = compliance_filter(over, platform, [claim_id])
    assert result.tier == "red" and "length" in kinds(result)


# --- Yellow ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Membership pricing starts at $49 for the first month.",
        "Some people compare telehealth providers versus local clinics.",
        "Compounded medications are a common topic here.",
    ],
)
def test_yellow_needs_review_but_ok(text, claim_id):
    result = compliance_filter(f"{DISCLOSURE} {text}", "facebook", [claim_id])
    assert result.ok is True and result.tier == "yellow", result.hits


def test_tiktok_is_always_at_least_yellow(claim_id):
    result = compliance_filter(CLEAN, "tiktok", [claim_id])
    assert result.tier == "yellow" and "R34" in rule_ids(result)


def test_hit_shape(claim_id):
    result = compliance_filter("This is clinically proven.", "facebook", [claim_id])
    hit = next(h for h in result.hits if h.kind == "prohibited")
    assert isinstance(hit, Hit)
    assert hit.rule_id == "R14" and hit.match.lower() == "clinically proven" and hit.reason


@pytest.mark.parametrize("claim", knowledge.claims(), ids=lambda c: c.id)
def test_seeded_claim_wording_is_never_hard_blocked(claim):
    # Approved-library wording must not trip the prohibited/privacy rules.
    # Only the R38 medication-name toggle may block it (e.g. the NAD+ claim).
    result = compliance_filter(f"{DISCLOSURE} {claim.text}", "facebook", [claim.id])
    blocking = [h for h in result.hits if h.kind != "yellow" and h.rule_id != "R38"]
    assert blocking == []


def test_filter_is_deterministic(claim_id):
    text = "Guaranteed results, clinically proven, spots are limited."
    assert compliance_filter(text, "facebook", [claim_id]) == compliance_filter(text, "facebook", [claim_id])
