"""Knowledge YAML (config/): loaders, alias lookup, urgent overrides, leak guard."""

import re
import shutil
from pathlib import Path

import pytest
import yaml

from harvey import knowledge
from harvey.paths import PROJECT_ROOT

CONFIG_DIR = PROJECT_ROOT / "config"
YAML_FILES = ["competitors.yaml", "products.yaml", "keywords.yaml", "compliance_rules.yaml", "claims.yaml"]

# --- Leak guard ------------------------------------------------------------
# products.md in the registry holds internal cost data from the pricing
# workbook, including the names of the pharmacies WellPeps buys from. None
# of it may enter config/. The guard list lives here, never in config.
LEAK_PATTERNS = [
    r"(?i)supplier",
    r"(?i)wholesale",
    r"(?i)per[- ]vial",
    r"(?i)cost[- ]basis",
    r"(?i)unit cost",
    r"(?i)pharmacy cost",
    r"\bCOGS\b",
    r"(?i)\bmargins?\b",
    r"(?i)503[AB] pricing",
    r"(?i)suggested retail",
    r"(?i)source of truth",
    r"(?i)master pricing",
    r"(?i)clinic pricing",
    r"\$\d+\.\d{2}",  # cents-level prices appear only in the cost workbook
    # Pharmacy / supplier network named in products.md and competitors.md
    r"\bMiller\b",
    r"(?i)503A Top Tier",
    r"(?i)Ageless Pharma",
    r"(?i)Active Pharm",
    r"(?i)Empower",
    r"(?i)Belmar",
    r"(?i)Olympia",
    r"(?i)Hallandale",
    r"(?i)Greenwich",
    r"(?i)FormBlends",
    r"(?i)Nationwide Compounding",
    r"(?i)Pomegranate",
    r"(?i)Dirx",
    r"(?i)\bVios\b",
    r"(?i)\bLogos\b",
    r"(?i)VitaScripts",
    r"(?i)Optimal Balance",
    r"(?i)St\.? Luke",
]


def leak_hits(text: str) -> list[str]:
    return [p for p in LEAK_PATTERNS if re.search(p, text)]


@pytest.fixture(autouse=True)
def _fresh_knowledge(monkeypatch):
    monkeypatch.delenv("PULSE_CONFIG_DIR", raising=False)
    knowledge.reload()
    yield
    knowledge.reload()


# --- Files and headers -----------------------------------------------------


@pytest.mark.parametrize("name", YAML_FILES)
def test_config_file_exists_with_header(name):
    text = (CONFIG_DIR / name).read_text(encoding="utf-8")
    head = "\n".join(text.splitlines()[:12])
    assert head.startswith("#")
    assert "Source:" in head
    assert "2026-09-27" in head
    assert "do not add internal costs" in head.lower()


@pytest.mark.parametrize("name", YAML_FILES)
def test_no_internal_cost_data_in_config(name):
    text = (CONFIG_DIR / name).read_text(encoding="utf-8")
    assert leak_hits(text) == []


def test_every_config_yaml_is_covered_by_leak_guard():
    on_disk = sorted(p.name for p in CONFIG_DIR.glob("*.yaml"))
    assert on_disk == sorted(YAML_FILES)


@pytest.mark.parametrize(
    "planted",
    [
        "cost: $50.61 per vial",
        "supplier: Miller's Pharmacy",
        "source: The Active Pharm suggested retail",
        "pharmacy: 503A Top Tier",
        "Ageless Pharma RX",
        "gross margin 40%",
        "COGS",
    ],
)
def test_leak_guard_catches_planted_cost_data(planted):
    assert leak_hits(planted)


def test_leak_guard_ignores_public_terms():
    assert leak_hits("AgelessRx sermorelin $149 member price, compounding pharmacy, 503A") == []


# --- Config dir override ---------------------------------------------------


def test_config_dir_defaults_to_repo_config():
    assert knowledge.config_dir() == CONFIG_DIR


def test_config_dir_env_override(tmp_path, monkeypatch):
    for name in YAML_FILES:
        shutil.copy(CONFIG_DIR / name, tmp_path / name)
    data = yaml.safe_load((tmp_path / "claims.yaml").read_text(encoding="utf-8"))
    data["claims"] = data["claims"][:1]
    (tmp_path / "claims.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("PULSE_CONFIG_DIR", str(tmp_path))
    knowledge.reload()

    assert knowledge.config_dir() == tmp_path
    assert len(knowledge.claims()) == 1


def test_loaders_are_cached_until_reload():
    first = knowledge.products()
    assert knowledge.products() is first
    knowledge.reload()
    assert knowledge.products() is not first


def test_invalid_regex_in_config_is_rejected(tmp_path, monkeypatch):
    for name in YAML_FILES:
        shutil.copy(CONFIG_DIR / name, tmp_path / name)
    data = yaml.safe_load((tmp_path / "compliance_rules.yaml").read_text(encoding="utf-8"))
    data["prohibited"][0]["pattern"] = "(unclosed"
    (tmp_path / "compliance_rules.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("PULSE_CONFIG_DIR", str(tmp_path))
    knowledge.reload()

    with pytest.raises(knowledge.KnowledgeError):
        knowledge.compliance_rules()


def test_missing_config_file_raises_knowledge_error(tmp_path, monkeypatch):
    monkeypatch.setenv("PULSE_CONFIG_DIR", str(tmp_path))
    knowledge.reload()
    with pytest.raises(knowledge.KnowledgeError):
        knowledge.products()


# --- Competitors -----------------------------------------------------------


def test_competitor_counts_and_tiers():
    comps = knowledge.competitors()
    assert len(comps.competitors) == 46
    assert len(comps.adjacent) == 18
    assert {c.tier for c in comps.adjacent} == {"lab"}
    assert "lab" not in {c.tier for c in comps.competitors}
    names = {c.name for c in comps.competitors}
    assert {"Hims & Hers", "Ro", "Henry Meds", "BlueChew", "Keeps", "Hone Health", "AgelessRx"} <= names


def test_non_competitors_excluded():
    all_names = {c.name.lower() for c in knowledge.competitors().all()}
    for excluded in ["wellmedr", "openloop", "scriptful", "gen health", "qualiphy", "empower pharmacy"]:
        assert excluded not in all_names


def test_handles_only_from_registry():
    handles = {h for c in knowledge.competitors().all() for h in c.handles}
    assert handles == {"@drmyramochi", "@joinfound", "@slowmyage", "@novoslabs", "moreplatesmoredates"}


@pytest.mark.parametrize(
    "alias,canonical",
    [
        ("Hims", "Hims & Hers"),
        ("HIMS AND HERS", "Hims & Hers"),
        ("hymns", "Hims & Hers"),  # misspelling
        ("roman", "Ro"),
        ("ro.co", "Ro"),
        ("henrymeds", "Henry Meds"),
        ("henrymed", "Henry Meds"),  # misspelling
        ("Ivím", "Ivím Health"),
        ("ivym", "Ivím Health"),
        ("blue chew", "BlueChew"),
        ("lemonade health", "Lemonaid Health"),
        ("mpmd", "Marek Health"),
        ("ww clinic", "WeightWatchers Clinic"),
        ("function health", "Function Health"),  # adjacent lab
    ],
)
def test_competitor_lookup(alias, canonical):
    assert knowledge.competitor_lookup()[alias.lower()] == canonical


def test_competitor_lookup_keys_are_lowercase_and_unambiguous():
    lookup = knowledge.competitor_lookup()
    assert all(k == k.lower() for k in lookup)
    # Building the lookup raises on an alias claimed by two competitors;
    # reaching here means none collide.
    assert len(lookup) > 100


def test_competitor_lookup_excludes_supplier_lookalike():
    assert "ageless" not in knowledge.competitor_lookup()
    assert knowledge.competitor_lookup()["agelessrx"] == "AgelessRx"


# --- Products --------------------------------------------------------------


def test_products_public_fields_only():
    prods = knowledge.products()
    assert len(prods.products) == 17
    by_name = {p.name: p for p in prods.products}
    sema = by_name["Compounded Semaglutide"]
    assert sema.site_price.amount_usd == 109
    assert sema.site_price.member_price is True
    assert "Ozempic" in sema.brand_equivalents
    assert by_name["Oral Tirzepatide"].site_price is None
    assert by_name["Topical Treatments"].status == "coming_soon"
    assert by_name["Sermorelin"].status == "live"
    allowed = {"name", "generic_names", "brand_equivalents", "form", "category", "site_price", "status", "aliases", "formulations"}
    for p in prods.products:
        assert set(p.model_dump()) <= allowed


def test_inferred_aliases_marked():
    sema = next(p for p in knowledge.products().products if p.name == "Compounded Semaglutide")
    terms = {a.term: a.inferred for a in sema.aliases}
    assert terms["sema"] is True
    assert terms["compounded semaglutide"] is False


def test_medication_names_cover_r38_list():
    names = {n.lower() for n in knowledge.medication_names()}
    for required in ["semaglutide", "tirzepatide", "sildenafil", "tadalafil", "finasteride", "minoxidil", "sermorelin", "ozempic", "wegovy", "mounjaro", "zepbound", "viagra", "cialis", "rogaine", "propecia"]:
        assert required in names


# --- Keywords --------------------------------------------------------------


def test_keyword_families_present():
    kw = knowledge.keywords()
    assert "WellPeps" in kw.brand.exact
    assert kw.products.generics and kw.products.brand_names
    assert set(kw.risk) >= {"complaint", "legal", "adverse_event", "privacy", "billing"}
    assert kw.gray_market and kw.category_intent
    assert set(kw.urgent_overrides) == {"adverse_event", "legal_regulatory", "privacy", "billing_fraud"}


def test_keyword_competitors_reference_canonical_names():
    canon = {c.name for c in knowledge.competitors().all()}
    assert set(knowledge.keywords().competitors) <= canon


def test_communities_unverified():
    comms = knowledge.keywords().communities
    assert any(c.kind == "subreddit" for c in comms)
    assert any(c.kind == "hashtag" for c in comms)
    assert all(c.verified is False for c in comms)


@pytest.mark.parametrize(
    "text,category",
    [
        ("I was hospitalized after my third shot", "adverse_event"),
        ("ended up with an ER visit last night", "adverse_event"),
        ("Went to the ER, they said pancreatitis", "adverse_event"),
        ("my gallbladder is acting up", "adverse_event"),
        ("severe vomiting for two days", "adverse_event"),
        ("I can't keep anything down", "adverse_event"),
        ("I cant keep anything down", "adverse_event"),
        ("had an allergic reaction to the injection", "adverse_event"),
        ("feeling suicidal since starting", "adverse_event"),
        ("we are filing a lawsuit", "legal_regulatory"),
        ("they got sued by Lilly", "legal_regulatory"),
        ("join the class action", "legal_regulatory"),
        ("reporting to the attorney general", "legal_regulatory"),
        ("they got an FDA warning letter", "legal_regulatory"),
        ("I filed a complaint with the FTC", "legal_regulatory"),
        ("isn't this a HIPAA violation", "privacy"),
        ("they shared my data with advertisers", "privacy"),
        ("my info was leaked", "privacy"),
        ("this company is a scam", "billing_fraud"),
        ("straight up fraud", "billing_fraud"),
        ("I filed a chargeback", "billing_fraud"),
        ("they charged without asking", "billing_fraud"),
        ("unauthorized charge on my card", "billing_fraud"),
    ],
)
def test_urgent_override_hits(text, category):
    hit = knowledge.urgent_override(text)
    assert hit is not None
    assert hit[0] == category


@pytest.mark.parametrize(
    "text",
    [
        "I never had any issues",
        "Ernest said the order was fine",
        "the tester told me it was over",
        "every other provider was fine",
        "this is an issue with shipping",
        "the kids scampered off",
        "great hospitality at the clinic",
        "leaky gut talk again",
        "er, not sure what dose that is",
        "love my results so far",
    ],
)
def test_urgent_override_non_hits(text):
    assert knowledge.urgent_override(text) is None


# --- Compliance rules + claims --------------------------------------------


def test_compliance_rules_shape():
    rules = knowledge.compliance_rules()
    assert len(rules.prohibited) >= 30
    assert all(re.fullmatch(r"R\d{1,2}", r.id) for r in rules.prohibited)
    assert rules.patient_confirmation and rules.yellow
    assert rules.toggles.forbid_medication_names_in_replies is True
    assert rules.toggles.allow_certification_claims is False
    assert rules.limits.max_links == 1
    assert rules.limits.max_hashtags_for("reddit") == 0
    assert rules.limits.max_chars_for("reddit") == 1500
    assert rules.limits.max_chars_for("instagram") == 2200
    assert rules.limits.max_chars_for("facebook") == 8000
    assert rules.limits.max_chars_for("tiktok") == 1000


def test_claims_seeded_all_pending():
    claims = knowledge.claims()
    assert 5 <= len(claims) <= 20
    assert all(c.approved_by == "PENDING" and c.approved_at is None for c in claims)
    assert all(c.source.startswith("reply-compliance-rules.md R") for c in claims)
    assert set(knowledge.claims_by_id()) == {c.id for c in claims}


def test_publishable_claim_ids_empty_while_pending():
    assert knowledge.publishable_claim_ids() == set()


def test_publishable_claim_ids_respects_approval_and_expiry(tmp_path, monkeypatch):
    for name in YAML_FILES:
        shutil.copy(CONFIG_DIR / name, tmp_path / name)
    data = yaml.safe_load((tmp_path / "claims.yaml").read_text(encoding="utf-8"))
    a, b, c = data["claims"][:3]
    a.update(approved_by="Dr. Compliance", approved_at="2026-09-01")
    b.update(approved_by="Dr. Compliance", approved_at="2026-01-01", expires="2026-02-01")
    c.update(approved_by="Dr. Compliance", approved_at="2026-09-01", expires="2099-01-01")
    (tmp_path / "claims.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("PULSE_CONFIG_DIR", str(tmp_path))
    knowledge.reload()

    assert knowledge.publishable_claim_ids() == {a["id"], c["id"]}
