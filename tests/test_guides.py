"""Smart Patient's Guide references (binding user instruction, 2026-10-07):
"We must refer to a relevant guide and point to how the guide can help."

Catalog (config/guides.yaml), guide + chapter selection, exclusions, the
deterministic filter check, drafter / reviewer prompts, the one-redraft rule in
draft_batch, the protocol's resource, the review desk chip and the few-shot
examples. Stubs and FakeBrain only; no Claude, no network."""

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from harvey import engagement, guides, knowledge, links, protocol
from harvey.agents import reviewer as reviewer_mod
from harvey.agents.drafter import (
    MAX_CLAIMS,
    REDDIT_MAX_WORDS_WITH_GUIDE,
    DraftProposal,
    build_prompt,
    candidate_claims,
    engagement_block,
    length_rule,
)
from harvey.compliance import ReplyContext, compliance_filter
from harvey.drafting import draft_batch
from harvey.models import Category, Mention, MentionStatus, Platform, ReviewVerdict, Triage, Urgency
from harvey.models.knowledge import Situation
from tests.test_drafting import StubReviewer
from tests.test_drafting import state  # noqa: F401  (fixture)

EBOOKS_TS = Path(r"C:\dev\W\wellpeps-site\src\data\ebooks.ts")
GLP1_URL = "https://wellpeps.com/smart-patient-guides/glp-1-weight-loss"
HAIR_URL = "https://wellpeps.com/smart-patient-guides/hair-restoration"
PRICE_POST = ("anyone tried compounded tirzepatide via telehealth? Trying to figure out which providers include "
              "the follow-up visits in the price.")
ALLOWED = ReplyContext(community_id="reddit:r/x", participation="allowed", links_allowed=True)
NO_LINKS = ReplyContext(community_id="reddit:r/x", participation="allowed", links_allowed=False)
NEEDS_PERMISSION = ReplyContext(community_id="fb:g", participation="with_permission", permission_obtained=False)
NOW = datetime(2026, 10, 7, 12, 0)


@pytest.fixture(autouse=True)
def _fresh_knowledge(monkeypatch):
    monkeypatch.delenv("PULSE_CONFIG_DIR", raising=False)
    knowledge.reload()
    yield
    knowledge.reload()


def _mention(text: str, mention_id: int = 1, url: str = "https://www.reddit.com/r/t/comments/g1/") -> Mention:
    return Mention(id=mention_id, platform=Platform.REDDIT, external_id=f"g{mention_id}", url=url, text=text)


def _triage(category=Category.QUESTION, subtype="", drug="", product="", **extra) -> Triage:
    return Triage(mention_id=1, category=category, subtype=subtype, drug=drug, product=product,
                  reply_appropriate=True, subject_type=extra.pop("subject_type", "category"), **extra)


# --- Catalog ---------------------------------------------------------------------------------------


def test_catalog_covers_every_site_guide_with_its_claim_link_and_program():
    data = knowledge.guides()
    assert [g.slug for g in data.guides] == ["glp-1-weight-loss", "sexual-wellness", "hair-restoration",
                                            "healthy-aging-vitality", "nad-therapy"]
    for guide in data.guides:
        claim = knowledge.claims_by_id()[guide.claim_id]
        assert claim.link_id == guide.link_id
        assert knowledge.links_by_id()[guide.link_id].url.endswith("/" + guide.slug)
        assert len(guide.chapters) == 4 and guide.pages == 18
        assert guide.title == "The Smart Patient's Guide to " + guide.short.removesuffix(" guide")
    assert data.series.link_id == "LNK-GUIDES" and data.series.claim_id == "CLM-EDU-GUIDES"


def _ascii(text: str) -> str:
    return text.replace("’", "'").replace("“", '"').replace("”", '"')


@pytest.mark.skipif(not EBOOKS_TS.is_file(), reason="wellpeps-site checkout not present")
def test_chapters_are_verbatim_from_ebooks_ts():
    source = EBOOKS_TS.read_text(encoding="utf-8")
    for guide in knowledge.guides().guides:
        block = source[source.index(f"slug: '{guide.slug}'"):]
        inside = block[block.index("inside: ["):block.index("],")]
        expected = [_ascii(t) for t in re.findall(r"'([^']*)'", inside)]
        assert [c.title for c in guide.chapters] == expected, guide.slug
        assert f"title: '{guide.short.removesuffix(' guide')}'" in block


def test_loader_rejects_a_guide_whose_link_differs_from_its_claim(tmp_path, monkeypatch):
    import shutil

    for path in knowledge.config_dir().glob("*.yaml"):
        shutil.copy(path, tmp_path / path.name)
    data = yaml.safe_load((tmp_path / "guides.yaml").read_text(encoding="utf-8"))
    data["guides"][0]["link_id"] = "LNK-GUIDES"
    (tmp_path / "guides.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("PULSE_CONFIG_DIR", str(tmp_path))
    knowledge.reload()
    with pytest.raises(knowledge.KnowledgeError, match="link_id must equal"):
        knowledge.guides()


def test_a_guide_requirement_is_only_for_drafted_situations():
    with pytest.raises(ValidationError, match="only for reply: draft"):
        Situation(id="x", label="x", match={}, reply="no_reply", guide="required")


def test_answering_situations_require_the_guide():
    by_id = {s.id: s for s in knowledge.engagement_guide().situations}
    required = {"protocol_appropriate_alternative", "protocol_educational_only", "competitor_comparison",
                "has_anyone_used_wellpeps", "pricing_question", "process_question", "clinic_recommendation",
                "general_education", "question"}
    assert {sid for sid, s in by_id.items() if s.guide == "required"} == required
    assert by_id["misinformation_about_wellpeps"].guide == "if_specific"
    assert all(s.guide == "none" for s in by_id.values() if s.reply != "draft")


# --- Selection -----------------------------------------------------------------------------------


def test_glp1_pricing_question_points_to_whats_included_in_the_price():
    pick = guides.best_guide(_mention(PRICE_POST), _triage(Category.PURCHASE_INTENT, "pricing_question",
                                                           drug="tirzepatide"))
    assert pick.guide.slug == "glp-1-weight-loss" and pick.specific
    assert pick.chapter == "What's actually included in the price"


@pytest.mark.parametrize("text, drug, slug, chapter", [
    ("Is WellPeps hair restoration live yet? Thinking about oral vs topical", "", "hair-restoration",
     "Oral vs. topical: what actually changes?"),
    ("hair loss: what results should I realistically expect from treatment?", "", "hair-restoration",
     "What does a good result actually look like?"),
    ("How do you tell if an online ED clinic is legit?", "", "sexual-wellness",
     "The questions every Smart Patient should know to ask"),
    ("daily or as needed for ED?", "", "sexual-wellness", "Daily or as needed?"),
    ("is NAD+ worth it or just hype vs NMN?", "NAD+", "nad-therapy", "NAD+ vs. NR vs. NMN"),
    ("Thinking about peptide therapy through an online clinic. What should I ask first?", "",
     "healthy-aging-vitality", "The questions every Smart Patient should know to ask"),
    ("oral semaglutide pills vs the shots?", "semaglutide", "glp-1-weight-loss",
     "Injections, oral tablets and additives"),
])
def test_the_most_relevant_guide_and_chapter(text, drug, slug, chapter):
    pick = guides.pick_for(drug=drug, text=text)
    assert pick.guide.slug == slug
    assert pick.chapter == chapter


def test_no_program_falls_back_to_the_series_index():
    pick = guides.pick_for(text="Which telehealth company is legit?")
    assert not pick.specific and pick.guide.link_id == "LNK-GUIDES"


@pytest.mark.parametrize("triage, text", [
    (dict(category=Category.ADVERSE_EVENT), "I got sick after my dose"),
    (dict(category=Category.QUESTION, subtype="emergency"), "chest pain help"),
    (dict(category=Category.QUESTION, subtype="dose_question", subject_type="wellpeps"), "what dose?"),
    (dict(category=Category.COMPLAINT, subject_type="wellpeps"), "terrible service"),
    (dict(category=Category.PRIVACY), "you leaked my info"),
    (dict(category=Category.LEGAL_REGULATORY), "my lawyer will call"),
    (dict(category=Category.BILLING_FRAUD, subject_type="wellpeps"), "double charged"),
    (dict(category=Category.QUESTION, subtype="media_inquiry", subject_type="wellpeps"), "reporter here"),
    (dict(category=Category.QUESTION), "im 16 and want ozempic, which site is easiest?"),
    (dict(category=Category.QUESTION, urgency_reason="safety_screen:minor"), "how do I get it"),
    (dict(category=Category.QUESTION, protocol_decision="clinical_caution"), "Ro gave me a rash, switch?"),
])
def test_excluded_situations_never_get_a_guide(triage, text):
    kwargs = dict(triage)
    t = Triage(mention_id=1, reply_appropriate=True, **{"subject_type": "category", **kwargs})
    assert guides.best_guide(_mention(text), t) is None
    requirement = guides.requirement_for(engagement.situation_of(t), ALLOWED, t, _mention(text))
    assert requirement.mode == "forbidden" and requirement.pick is None


def test_minor_detection_ignores_weights_and_weeks():
    assert guides.mentions_minor("I'm 17, can I get it?")
    assert not guides.mentions_minor("I am 12 weeks in and 15 lbs down")
    assert not guides.mentions_minor("I'm 15 lbs down")


# --- Requirement modes ---------------------------------------------------------------------------


def _requirement(context, text=PRICE_POST, **triage):
    t = _triage(**{"category": Category.PURCHASE_INTENT, "drug": "tirzepatide", **triage})
    return guides.requirement_for(engagement.situation_of(t), context, t, _mention(text))


def test_links_allowed_means_the_tracked_guide_link():
    req = _requirement(ALLOWED)
    assert req.mode == "link" and req.pick.guide.claim_id == "CLM-EDU-GUIDE-GLP-1-WEIGHT-LOSS"


def test_no_links_means_the_guide_is_named_without_one():
    assert _requirement(NO_LINKS).mode == "name"


def test_no_promotion_means_no_guide():
    assert _requirement(NEEDS_PERMISSION).mode == "forbidden"


def test_the_catch_all_situation_needs_no_guide():
    t = _triage(Category.PRAISE)
    assert guides.requirement_for(engagement.situation_of(t), ALLOWED, t, _mention("love it")).mode == "none"


def test_misinformation_correction_gets_a_guide_only_when_one_fits():
    t = _triage(subtype="misinformation_about_wellpeps")
    situation = engagement.situation_of(t)
    assert guides.requirement_for(situation, ALLOWED, t, _mention("WellPeps auto-approves everyone")).mode == "none"
    fits = guides.requirement_for(situation, ALLOWED, t, _mention("WellPeps auto-approves everyone for hair loss"))
    assert fits.mode == "link" and fits.pick.guide.slug == "hair-restoration"


# --- Deterministic check ---------------------------------------------------------------------------

ANSWER = ("I work with WellPeps. It varies by provider, so ask each one exactly what follow-up with a licensed "
          "clinician is included.")
WITH_GUIDE = (ANSWER + " Since you're comparing what's included, our free GLP-1 Weight Loss guide (it asks for your "
              f"email) has a chapter, \"What's actually included in the price\", on what to check: {GLP1_URL}.")
IDS = ["CLM-AMG-04-WORK-WITH", "CLM-R7-PROVIDER-CHECKLIST"]
GUIDE_IDS = [*IDS, "CLM-EDU-GUIDE-GLP-1-WEIGHT-LOSS"]


def _guide_hits(result):
    return [h for h in result.hits if h.rule_id == "GUIDE"]


def test_missing_guide_reference_is_yellow():
    result = compliance_filter(ANSWER, "reddit", IDS, guide=_requirement(ALLOWED))
    assert result.tier == "yellow" and guides.missing(result)
    assert "What's actually included in the price" in _guide_hits(result)[0].reason


def test_guide_with_link_and_email_gate_satisfies_the_rule():
    result = compliance_filter(WITH_GUIDE, "reddit", GUIDE_IDS, guide=_requirement(ALLOWED))
    assert _guide_hits(result) == []
    assert [h.reason for h in result.hits] == ["link not live yet"]     # real config: links not live


def test_the_wrong_guide_link_does_not_satisfy_the_rule():
    text = WITH_GUIDE.replace(GLP1_URL, HAIR_URL)
    result = compliance_filter(text, "reddit", [*IDS, "CLM-EDU-GUIDE-HAIR-RESTORATION"], guide=_requirement(ALLOWED))
    assert guides.missing(result)


def test_guide_link_without_the_email_gate_is_yellow():
    text = WITH_GUIDE.replace(" (it asks for your email)", "")
    result = compliance_filter(text, "reddit", GUIDE_IDS, guide=_requirement(ALLOWED))
    assert [h.reason for h in _guide_hits(result)] == [guides.GATE_REASON]


def test_named_guide_satisfies_the_no_link_rule():
    text = (ANSWER + " Our free GLP-1 Weight Loss guide on the WellPeps website (it asks for your email) has a "
                     "chapter, \"What's actually included in the price\", on what to check.")
    result = compliance_filter(text, "reddit", GUIDE_IDS, guide=_requirement(NO_LINKS), context=NO_LINKS)
    assert _guide_hits(result) == [] and result.tier == "green", result.hits


def test_a_guide_in_an_excluded_situation_is_red():
    t = _triage(Category.ADVERSE_EVENT)
    forbidden = guides.requirement_for(engagement.situation_of(t), ALLOWED, t, _mention("I felt dizzy"))
    result = compliance_filter(WITH_GUIDE, "reddit", GUIDE_IDS, guide=forbidden)
    assert result.tier == "red" and any(guides.FORBIDDEN_REASON in h.reason for h in result.red_hits)


def test_guide_titles_and_chapters_are_not_read_as_review_wording():
    text = ("I work with WellPeps. Our free Hair Restoration guide (it asks for your email) covers "
            "\"Oral vs. topical: what actually changes?\" and \"What does a good result actually look like?\"")
    result = compliance_filter(text, "reddit", ["CLM-AMG-04-WORK-WITH", "CLM-EDU-GUIDE-HAIR-RESTORATION"])
    assert result.tier == "green", result.hits
    # Outside a guide title the same words are still flagged.
    assert compliance_filter("I work with WellPeps. Results vs. other options vary.", "reddit",
                             ["CLM-AMG-04-WORK-WITH"]).tier == "yellow"


def test_gated_download_finalize_is_resolved_and_guide_claims_are_publishable():
    assert "gated_download_disclosure" in knowledge.finalized_keys()
    item = next(f for f in knowledge.engagement_guide().finalize if f.key == "gated_download_disclosure")
    assert "2026-10-07" in item.value and "user's instruction" in item.value
    assert {g.claim_id for g in guides.all_guides()} <= knowledge.publishable_claim_ids()
    # Approval of a reply carrying a guide link still waits for the deployed page.
    assert not any(link.live for link in knowledge.links())


# --- Drafter and reviewer prompts --------------------------------------------------------------------


def _guidance(context=ALLOWED, text=PRICE_POST, **triage):
    t = _triage(**{"category": Category.PURCHASE_INTENT, "subtype": "pricing_question", "drug": "tirzepatide",
                   **triage})
    return t, engagement.guidance_for(engagement.situation_of(t), context, t, _mention(text))


def test_guidance_carries_the_guide_and_chapter():
    _, g = _guidance()
    assert g.guide_required and g.guide_mode == "link"
    assert g.guide_claim_id == "CLM-EDU-GUIDE-GLP-1-WEIGHT-LOSS"
    assert g.guide_chapters[0] == "What's actually included in the price"


def test_the_guide_claim_is_always_offered_right_after_the_disclosure():
    t, g = _guidance()
    claims = candidate_claims(t.product, drug=t.drug, category=t.category, preferred=g.preferred_claims,
                              guide_claim_id=g.guide_claim_id)
    ids = [c.id for c in claims]
    assert len(ids) <= MAX_CLAIMS
    assert ids[:2] == ["CLM-AMG-04-WORK-WITH", "CLM-EDU-GUIDE-GLP-1-WEIGHT-LOSS"]


def test_drafter_prompt_requires_the_how_it_helps_sentence():
    t, g = _guidance()
    mention = _mention(PRICE_POST)
    claims = candidate_claims(t.product, drug=t.drug, category=t.category, preferred=g.preferred_claims,
                              guide_claim_id=g.guide_claim_id)
    prompt = build_prompt(mention, t, claims, guidance=g)
    assert "Smart Patient's Guide (REQUIRED by WellPeps)" in prompt
    assert '"What\'s actually included in the price"' in prompt
    assert "our free GLP-1 Weight Loss guide (it asks for your email)" in prompt
    assert "Chapters in this guide (approved guide content" in prompt
    assert links.tracked_url_for(knowledge.links_by_id()["LNK-GUIDE-GLP-1-WEIGHT-LOSS"], mention) in prompt
    assert f"under {REDDIT_MAX_WORDS_WITH_GUIDE} words" in length_rule("reddit", g)


def test_no_link_guidance_names_the_guide_without_a_url():
    _, g = _guidance(NO_LINKS)
    block = engagement_block(g)
    assert "our free GLP-1 Weight Loss guide on the" in block and "never write a URL" in block


def test_excluded_guidance_says_no_guide():
    t = _triage(Category.ADVERSE_EVENT)
    g = engagement.guidance_for(engagement.situation_of(t), ALLOWED, t, _mention("dizzy"))
    assert "No Smart Patient's Guide" in engagement_block(g)


def test_reviewer_checks_relevance_and_specificity():
    t, g = _guidance()
    by_id = knowledge.claims_by_id()
    prompt = reviewer_mod.build_prompt(WITH_GUIDE, "reddit", _mention(PRICE_POST),
                                       [by_id[c] for c in GUIDE_IDS], g)
    assert 'Smart Patient\'s Guide (rule id "GUIDE"' in prompt
    assert "Guide: REQUIRED (WellPeps instruction)" in prompt
    assert "generic" in prompt and "`needs_human`" in prompt
    assert "Chapters in this guide (approved guide content)" in prompt


# --- draft_batch: one redraft, then needs_human ---------------------------------------------------------


class GuideDrafter:
    def __init__(self, replies):
        self.replies = list(replies)
        self.feedback = []

    async def draft(self, mention, triage, feedback=None, max_calls=None, guidance=None):
        self.feedback.append(feedback)
        reply, ids = self.replies.pop(0)
        return DraftProposal(reply=reply, claim_ids=ids, model="sonnet", calls=1)


async def _question(state, text=PRICE_POST):
    mid, _ = await state.upsert_mention(Mention(
        platform=Platform.REDDIT, external_id="q1", url="https://www.reddit.com/r/t/comments/q1/", text=text,
        collected_at=NOW - timedelta(minutes=5)))
    await state.save_triage(Triage(mention_id=mid, category=Category.PURCHASE_INTENT, subtype="pricing_question",
                                   drug="tirzepatide", reply_appropriate=True, urgency=Urgency.NORMAL,
                                   subject_type="category"))
    await state.set_mention_status(mid, MentionStatus.TRIAGED)
    return mid


@pytest.mark.asyncio
async def test_missing_guide_gets_one_redraft_with_that_feedback(state):
    mid = await _question(state)
    drafter = GuideDrafter([(ANSWER, IDS), (WITH_GUIDE, GUIDE_IDS)])
    reviewer = StubReviewer()

    report = await draft_batch(state, drafter, reviewer)

    assert report.redrafted == 1 and len(drafter.feedback) == 2
    assert drafter.feedback[0] is None
    assert any(line.startswith("GUIDE: missing guide reference") for line in drafter.feedback[1])
    latest = await state.get_latest_draft(mid)
    assert latest.text.startswith(WITH_GUIDE[:40]) and latest.link["id"] == "LNK-GUIDE-GLP-1-WEIGHT-LOSS"
    assert latest.review_verdict is ReviewVerdict.PASS
    assert not any(h.startswith("GUIDE") for h in latest.filter_hits)


@pytest.mark.asyncio
async def test_still_missing_after_the_redraft_needs_a_human(state):
    mid = await _question(state)
    drafter = GuideDrafter([(ANSWER, IDS), (ANSWER, IDS)])

    await draft_batch(state, drafter, StubReviewer())

    latest = await state.get_latest_draft(mid)
    assert latest.review_verdict is ReviewVerdict.NEEDS_HUMAN
    assert any("missing guide reference" in r for r in latest.review_reasons)


# --- Protocol, review desk, examples ----------------------------------------------------------------------


def test_protocol_answering_decisions_carry_the_guide_resource():
    record = protocol.ProtocolRecord(decision=protocol.EDUCATIONAL_ONLY, rationale=())
    assert protocol.resource_for(record, True, "LNK-GUIDE-GLP-1-WEIGHT-LOSS") == "LNK-GUIDE-GLP-1-WEIGHT-LOSS"
    assert protocol.resource_for(record, False, "LNK-GUIDE-GLP-1-WEIGHT-LOSS") == "none"
    assert protocol.resource_for(record, True, "LNK-GUIDE-GLP-1-WEIGHT-LOSS", promotion_ok=False) == "none"
    clinical = protocol.ProtocolRecord(decision=protocol.CLINICAL_CAUTION, rationale=())
    assert protocol.resource_for(clinical, True, "LNK-GUIDE-GLP-1-WEIGHT-LOSS") == "none"


def test_review_desk_guide_view_and_chip():
    t = _triage(Category.PURCHASE_INTENT, "pricing_question", drug="tirzepatide")
    view = engagement.guide_view(engagement.situation_of(t), ALLOWED, t, _mention(PRICE_POST), WITH_GUIDE,
                                 GUIDE_IDS)
    assert view["title"] == "The Smart Patient's Guide to GLP-1 Weight Loss"
    assert view["chapter"] == "What's actually included in the price"
    assert view["satisfied"] and view["drafted"]
    app_js = (Path(__file__).resolve().parents[1] / "harvey" / "web" / "app.js").read_text(encoding="utf-8")
    assert "tag('Guide', g.title + ' → ' + (g.chapter" in app_js


def test_examples_show_the_guide_pattern():
    with_guide = [e for e in knowledge.reply_examples().examples
                  if set(e.claim_ids) & {g.claim_id for g in guides.all_guides()}]
    assert len(with_guide) >= 3
    for example in with_guide:
        guide = next(g for g in guides.all_guides() if g.claim_id in example.claim_ids)
        titles = [c.title for c in (*guide.chapters, *guide.landing)]
        assert any(t in example.reply for t in titles), example.id
        assert "it asks for your email" in example.reply
        assert guides.pick_for(text=example.post).guide.slug == guide.slug, example.id
