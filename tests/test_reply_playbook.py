"""Conversion reply playbook: new claims, few-shot examples, playbook claim
selection, the disclosure rules, tracked links through drafting and review,
and the Replies & links analytics. FakeBrain only; no Claude, no network."""

import asyncio
import re
import shutil
import sqlite3

import pytest
import yaml

from harvey import knowledge, links, reply_analytics, review
from harvey.agents.drafter import (
    MAX_CLAIMS,
    REDDIT_MAX_WORDS,
    Drafter,
    build_prompt,
    candidate_claims,
    examples_block,
    guide_claim,
)
from harvey.agents.reviewer import Reviewer
from harvey.compliance import compliance_filter, first_sentence, has_disclosure
from harvey.config import PulseConfig
from harvey.drafting import draft_batch
from harvey.models import (
    AuditEvent,
    AuditEventType,
    Category,
    Draft,
    Mention,
    MentionStatus,
    Platform,
    ReviewVerdict,
    Triage,
)
from harvey.paths import PROJECT_ROOT
from harvey.state import MIGRATIONS, StateManager
from tests.pulse_helpers import FakeBrain
from tests.test_knowledge import leak_hits

EXAMPLE_POST = ("anyone tried compounded tirzepatide via telehealth? Trying to figure out which "
                "providers include the follow-up visits in the price.")
GLP1 = "https://wellpeps.com/smart-patient-guides/glp-1-weight-loss"
DISCLOSURE = "Disclosure: I work with WellPeps, so I am not neutral."
NOT_LIVE = "link not live yet"
# The playbook since the rules of engagement (2026-10-07): disclosure, a
# provider-neutral answer, the provider-determines line; the guide claim is
# offered after them, never pushed second (Protocol §8), and no price claim leads.
PLAYBOOK_IDS = ["CLM-AMG-04-WORK-WITH", "CLM-R7-PROVIDER-CHECKLIST", "CLM-LR-02-ONGOING-SUPPORT",
                "CLM-R22-PROVIDER-DETERMINES"]
# Review notes an example may show besides "link not live yet": the guide
# claims' gated-download FINALIZE (R42).
REVIEW_NOTES = ("FINALIZE",)


@pytest.fixture(autouse=True)
def _fresh_knowledge(monkeypatch):
    monkeypatch.delenv("PULSE_CONFIG_DIR", raising=False)
    knowledge.reload()
    yield
    knowledge.reload()


def run(coro):
    return asyncio.run(coro)


def _config_copy(tmp_path, monkeypatch, *, live=None, forbid_meds=None, approve=(), community_allowed=True,
                 finalize=()):
    target = tmp_path / "config"
    shutil.copytree(knowledge.config_dir(), target)
    if community_allowed:
        # The example subreddit with verified rules that allow brand replies
        # and links (an unverified community may carry no link, R44).
        data = yaml.safe_load((target / "communities.yaml").read_text(encoding="utf-8"))
        data["communities"].append({
            "id": "reddit:r/tirzepatidecompound", "platform": "reddit", "name": "r/tirzepatidecompound",
            "brand_participation": "allowed", "links_allowed": True, "rules_checked_at": "2026-10-01"})
        (target / "communities.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    if live is not None:
        data = yaml.safe_load((target / "links.yaml").read_text(encoding="utf-8"))
        for link in data["links"]:
            link["live"] = live
        (target / "links.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    if forbid_meds is not None:
        data = yaml.safe_load((target / "compliance_rules.yaml").read_text(encoding="utf-8"))
        data["toggles"]["forbid_medication_names_in_replies"] = forbid_meds
        (target / "compliance_rules.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    if approve:
        data = yaml.safe_load((target / "claims.yaml").read_text(encoding="utf-8"))
        for claim in data["claims"]:
            if claim["id"] in approve:
                claim.update(approved_by="Fixture Compliance Officer", approved_at="2026-09-01")
        (target / "claims.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    if finalize:
        data = yaml.safe_load((target / "engagement_guide.yaml").read_text(encoding="utf-8"))
        for item in data["finalize"]:
            if item["key"] in finalize:
                item["value"] = "Fixture: provided by WellPeps"
        (target / "engagement_guide.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("PULSE_CONFIG_DIR", str(target))
    knowledge.reload()


# --- Claims -----------------------------------------------------------------------------


def test_conversion_claims_exist_pending_and_cite_site_sources():
    by_id = knowledge.claims_by_id()
    for cid in ("CLM-PRICE-FOLLOWUP", "CLM-PRICE-ALLIN", "CLM-EDU-GUIDES", "CLM-EDU-GUIDE-GLP-1-WEIGHT-LOSS",
                "CLM-EDU-GUIDE-SEXUAL-WELLNESS", "CLM-EDU-GUIDE-HAIR-RESTORATION",
                "CLM-EDU-GUIDE-HEALTHY-AGING-VITALITY", "CLM-EDU-GUIDE-NAD-THERAPY"):
        claim = by_id[cid]
        assert claim.approved_by == "PENDING" and claim.approved_at is None
        assert "wellpeps-site/src/" in claim.source
        assert leak_hits(claim.text) == []
    assert by_id["CLM-PRICE-FOLLOWUP"].text == (
        "Follow-up care with a licensed clinician is included in the one monthly price.")
    assert by_id["CLM-PRICE-FOLLOWUP"].products == ["*"]
    assert "if prescribed" in by_id["CLM-PRICE-ALLIN"].text
    # The kept claims are still there.
    assert {"CLM-R3-DISCLOSURE", "CLM-R22-PROVIDER-DETERMINES"} <= set(by_id)


def test_only_the_glp1_guide_claims_a_checklist():
    guides = [c for c in knowledge.claims() if c.id.startswith("CLM-EDU-GUIDE-")]
    with_checklist = {c.id for c in guides if "checklist" in c.text.lower()}
    assert with_checklist == {"CLM-EDU-GUIDE-GLP-1-WEIGHT-LOSS"}
    assert all("questions to ask" in c.text for c in guides)
    assert all(c.text.startswith("Free guide: The Smart Patient's Guide to ") for c in guides)


def test_every_guide_claim_maps_to_one_program_with_a_matching_link():
    by_link = knowledge.links_by_id()
    for claim in (c for c in knowledge.claims() if c.id.startswith("CLM-EDU-GUIDE-")):
        programs = {knowledge.program_of_product(p) for p in claim.products}
        assert len(programs) == 1, claim.id
        assert programs <= set(by_link[claim.link_id].programs), claim.id


def test_new_claim_wording_is_not_red_except_r38():
    for claim in (c for c in knowledge.claims() if not c.has_placeholder):  # slots: test_compliance
        result = compliance_filter(f"{DISCLOSURE} {claim.text}", "facebook", [claim.id])
        blocking = [h for h in result.red_hits if h.rule_id != "R38"]
        assert blocking == [], (claim.id, blocking)


# --- Few-shot examples ------------------------------------------------------------------


def _example_hits(example):
    result = compliance_filter(example.reply, example.platform, example.claim_ids)
    return result, [h for h in result.hits if h.reason != NOT_LIVE and not h.reason.startswith(REVIEW_NOTES)]


def test_examples_are_pending_style_guidance():
    data = knowledge.reply_examples()
    assert data.status == "PENDING" and "style guidance" in data.usage
    assert 3 <= len(data.examples) <= 4


@pytest.mark.parametrize("example", knowledge.reply_examples().examples, ids=lambda e: e.id)
def test_every_example_passes_the_filter_except_link_not_live(example):
    result, other = _example_hits(example)
    assert result.tier != "red" and other == [], other
    assert has_disclosure(example.reply)
    if example.platform == "reddit":
        assert len(example.reply.split()) <= REDDIT_MAX_WORDS


def test_link_examples_show_only_the_not_live_yellow():
    linked = [e for e in knowledge.reply_examples().examples if links.find_links(e.reply)]
    assert len(linked) >= 2
    for example in linked:
        result, other = _example_hits(example)
        assert result.tier == "yellow" and other == []


def test_the_real_tirzepatide_scenario_is_an_example():
    """Rewritten to the guidelines: the guide's primary disclosure, one brief
    factual option (the person asks which providers include follow-up), no
    'one monthly price', the guide's email gate stated."""
    example = next(e for e in knowledge.reply_examples().examples if e.post == EXAMPLE_POST)
    assert example.category == "purchase_intent" and example.platform == "reddit"
    assert example.reply.startswith("I work with WellPeps.")
    assert "WellPeps is one option you can evaluate" in example.reply
    assert "one monthly price" not in example.reply and "CLM-PRICE-FOLLOWUP" not in example.claim_ids
    assert "it asks for your email" in example.reply
    assert links.registry_link(GLP1).id == links.find_links(example.reply)[0].link.id


def test_examples_follow_the_guidelines_not_the_superseded_playbook():
    for example in knowledge.reply_examples().examples:
        assert example.reply.startswith("I work with WellPeps.")             # Guide §4; Protocol §7
        assert 30 <= len(example.reply.split()) <= 90                         # Protocol §7: 40-90 words
        assert not {"CLM-PRICE-FOLLOWUP", "CLM-PRICE-ALLIN"} & set(example.claim_ids)
        if links.find_links(example.reply):
            assert "asks for your email" in example.reply                     # Protocol §8


def test_examples_block_masks_registry_urls():
    block = examples_block()
    assert "<guide link>" in block and "wellpeps.com" not in block
    assert EXAMPLE_POST in block


# --- Claim selection ----------------------------------------------------------------------


def _ids(claims):
    return [c.id for c in claims]


@pytest.mark.parametrize("drug, guide", [
    ("tirzepatide", "CLM-EDU-GUIDE-GLP-1-WEIGHT-LOSS"),
    ("semaglutide", "CLM-EDU-GUIDE-GLP-1-WEIGHT-LOSS"),
    ("GLP-1", "CLM-EDU-GUIDE-GLP-1-WEIGHT-LOSS"),
    ("minoxidil", "CLM-EDU-GUIDE-HAIR-RESTORATION"),
    ("finasteride", "CLM-EDU-GUIDE-HAIR-RESTORATION"),
    ("tadalafil", "CLM-EDU-GUIDE-SEXUAL-WELLNESS"),
    ("sildenafil", "CLM-EDU-GUIDE-SEXUAL-WELLNESS"),
    ("sermorelin", "CLM-EDU-GUIDE-HEALTHY-AGING-VITALITY"),
    ("glutathione", "CLM-EDU-GUIDE-HEALTHY-AGING-VITALITY"),
    # R38's blanket ban is superseded, so the NAD+ guide is chosen for NAD+.
    ("NAD+", "CLM-EDU-GUIDE-NAD-THERAPY"),
    ("", "CLM-EDU-GUIDES"),
    ("BPC-157", "CLM-EDU-GUIDES"),
])
def test_guide_claim_follows_the_drug_program(drug, guide):
    assert guide_claim(drug=drug).id == guide


def test_healthy_aging_guide_is_the_fallback_if_medication_names_are_forbidden(tmp_path, monkeypatch):
    _config_copy(tmp_path, monkeypatch, forbid_meds=True)
    assert guide_claim(drug="NAD+").id == "CLM-EDU-GUIDE-HEALTHY-AGING-VITALITY"


def test_guide_claim_follows_the_wellpeps_product():
    assert guide_claim(product="Tadalafil Daily").id == "CLM-EDU-GUIDE-SEXUAL-WELLNESS"


@pytest.mark.parametrize("category", [Category.PURCHASE_INTENT, Category.QUESTION])
def test_purchase_intent_always_offers_the_playbook_claims_first(category):
    ids = _ids(candidate_claims("", drug="tirzepatide", category=category))
    # The guide is offered after the fixed claims, never pushed second (Protocol §8).
    assert ids[:5] == [*PLAYBOOK_IDS, "CLM-EDU-GUIDE-GLP-1-WEIGHT-LOSS"]
    assert len(ids) <= MAX_CLAIMS and len(ids) == len(set(ids))
    assert "CLM-R15-COMPOUNDED-DISCLOSURE" in ids  # drug-specific claims still offered


def test_other_categories_keep_the_old_order():
    ids = _ids(candidate_claims("", category=Category.COMPLAINT))
    assert ids[0] == "CLM-R3-DISCLOSURE" and "CLM-EDU-GUIDE-GLP-1-WEIGHT-LOSS" not in ids


def test_drug_maps_to_products_when_there_is_no_wellpeps_product():
    ids = _ids(candidate_claims("", drug="tirzepatide"))
    assert ids[0] == "CLM-R15-COMPOUNDED-DISCLOSURE"
    assert "CLM-R18-NAD-MECHANISM" not in ids


# --- Prompt ---------------------------------------------------------------------------------


def _mention(mention_id=77, text=EXAMPLE_POST, platform=Platform.REDDIT,
             url="https://www.reddit.com/r/tirzepatidecompound/comments/abc/t/") -> Mention:
    return Mention(id=mention_id, platform=platform, text=text, url=url)


def _triage(mention_id=77, category=Category.PURCHASE_INTENT, drug="tirzepatide") -> Triage:
    return Triage(mention_id=mention_id, category=category, drug=drug, reply_appropriate=True,
                  subject_type="category", relevant=True)


def test_prompt_carries_the_playbook_tracked_link_examples_and_word_limit():
    mention, triage = _mention(), _triage()
    claims = candidate_claims("", drug="tirzepatide", category=triage.category)
    prompt = build_prompt(mention, triage, claims)

    tracked = links.tracked_url(GLP1, platform="reddit", mention_id=77, community="tirzepatidecompound")
    assert f"Link for this claim: {tracked}" in prompt
    assert "Playbook for questions and purchase intent" in prompt
    assert f"under {REDDIT_MAX_WORDS} words" in prompt
    assert "Drug or topic discussed (if any): tirzepatide" in prompt
    assert "Program (if known): Weight Loss" in prompt
    assert "<guide link>" in prompt and "PENDING compliance approval" in prompt
    assert "{{" not in prompt.split("BEGIN_UNTRUSTED_MENTION")[0]
    assert leak_hits(prompt) == []


def test_non_reddit_prompt_has_no_word_limit():
    mention = _mention(platform=Platform.FACEBOOK, url="https://facebook.com/p/1")
    prompt = build_prompt(mention, _triage(), candidate_claims(""))
    assert "words" not in prompt.split("Length:")[1].split("\n")[0]


def test_reviewer_and_rules_check_disclosure_astroturfing_and_link_relevance():
    review_md = (PROJECT_ROOT / "prompts" / "review.md").read_text(encoding="utf-8")
    rules = (PROJECT_ROOT / "prompts" / "reply_rules.md").read_text(encoding="utf-8")
    assert "Undisclosed affiliation" in review_md and "FIRST sentence" in review_md
    assert "Astroturfing" in review_md and "as a happy customer" in review_md
    assert "Link relevance" in review_md
    assert "FIRST sentence" in rules and "links registry" in rules


# --- Disclosure rules -------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "Full disclosure: I work with WellPeps, so I'm not neutral. Thanks.",
    "Disclosure: I work with WellPeps, so I am not neutral.",
    "I work for WellPeps. Happy to help.",
    "I’m with WellPeps, so take this with that in mind.",
])
def test_approved_disclosure_forms_pass(text):
    assert has_disclosure(text)


def test_missing_disclosure_is_red():
    result = compliance_filter("Thanks for asking. A licensed healthcare provider determines whether "
                               "treatment is appropriate.", "reddit", ["CLM-R22-PROVIDER-DETERMINES"])
    assert result.tier == "red"
    assert any(h.rule_id == "R3" and h.kind == "disclosure" for h in result.hits)


def test_disclosure_must_be_in_the_first_sentence():
    text = "Great question. Disclosure: I work with WellPeps, so I am not neutral."
    assert first_sentence(text) == "Great question."
    assert compliance_filter(text, "reddit", ["CLM-R3-DISCLOSURE"]).tier == "red"


@pytest.mark.parametrize("text", [
    "I went with WellPeps and they are great.",
    "Honestly just check out WellPeps.",
    "WellPeps offers follow-up care in the price.",
])
def test_third_person_customer_talk_without_disclosure_is_red_with_its_own_reason(text):
    result = compliance_filter(text, "reddit", ["CLM-R3-DISCLOSURE"])
    assert result.tier == "red"
    assert any(h.rule_id == "R2" and h.kind == "disclosure" for h in result.hits)


def test_third_person_wording_is_fine_once_disclosed():
    text = f"{DISCLOSURE} WellPeps offers free Smart Patient's Guides."
    result = compliance_filter(text, "reddit", ["CLM-R3-DISCLOSURE", "CLM-EDU-GUIDES"])
    assert "disclosure" not in {h.kind for h in result.hits}


# --- Drafting, review and approval with a tracked link --------------------------------


GOOD_REPLY = (
    "I work with WellPeps. When you compare telehealth programs, ask whether follow-up with a licensed "
    "clinician is included or billed separately, how dose adjustments are handled, and which pharmacy prepares "
    "the medication. WellPeps is one option you can evaluate: if treatment is prescribed, ongoing support is "
    "part of the WellPeps process. Our free guide (it asks for your email) has questions to ask before choosing "
    f"a provider: {GLP1}."
)
GOOD_IDS = ["CLM-AMG-04-WORK-WITH", "CLM-R7-PROVIDER-CHECKLIST", "CLM-LR-02-ONGOING-SUPPORT",
            "CLM-EDU-GUIDE-GLP-1-WEIGHT-LOSS"]


def test_drafter_rewrites_the_link_to_the_mentions_tracked_url():
    brain = FakeBrain([{"reply": GOOD_REPLY, "claim_ids": GOOD_IDS, "rationale": "playbook"}])
    proposal = run(Drafter(brain).draft(_mention(), _triage()))
    tracked = links.tracked_url(GLP1, platform="reddit", mention_id=77, community="tirzepatidecompound")
    assert tracked in proposal.reply and proposal.claim_ids == GOOD_IDS
    assert links.link_record(proposal.reply)["utm_content"] == "m77"


async def _seed_example(state) -> int:
    mention_id, _ = await state.upsert_mention(Mention(
        platform=Platform.REDDIT, external_id="ex1", text=EXAMPLE_POST,
        url="https://www.reddit.com/r/tirzepatidecompound/comments/ex1/t/",
    ))
    await state.save_triage(Triage(mention_id=mention_id, category=Category.PURCHASE_INTENT, drug="tirzepatide",
                                   reply_appropriate=True, relevant=True, subject_type="category"))
    await state.set_mention_status(mention_id, MentionStatus.TRIAGED)
    return mention_id


def _pipeline(tmp_path):
    state = StateManager(str(tmp_path / "pulse.db"))
    run(state.init_db())
    mention_id = run(_seed_example(state))
    drafter = Drafter(FakeBrain([{"reply": GOOD_REPLY, "claim_ids": GOOD_IDS, "rationale": "playbook"}]))
    reviewer = Reviewer(FakeBrain([{"verdict": "pass", "reasons": []}]))
    report = run(draft_batch(state, drafter, reviewer))
    return state, mention_id, report


def test_fakebrain_example_post_drafts_yellow_with_a_stored_tracked_link(tmp_path, monkeypatch):
    _config_copy(tmp_path, monkeypatch)
    state, mention_id, report = _pipeline(tmp_path)

    draft = run(state.get_latest_draft(mention_id))
    assert report.drafted == 1 and report.redrafted == 0
    assert draft.tier == "yellow" and draft.review_verdict is ReviewVerdict.PASS
    assert "R8: link not live yet [LNK-GUIDE-GLP-1-WEIGHT-LOSS]" in draft.filter_hits
    assert all(h.startswith(("R8:", "R42: FINALIZE")) for h in draft.filter_hits), draft.filter_hits
    assert draft.link["id"] == "LNK-GUIDE-GLP-1-WEIGHT-LOSS"
    assert draft.link["utm_content"] == f"m{mention_id}" and draft.link["utm_term"] == "tirzepatidecompound"
    assert draft.link["url"] in draft.text


def test_approval_is_blocked_while_the_link_is_not_live(tmp_path, monkeypatch):
    _config_copy(tmp_path, monkeypatch, approve=GOOD_IDS)
    state, mention_id, _ = _pipeline(tmp_path)
    draft = run(state.get_latest_draft(mention_id))

    blockers = review.approval_blockers(MentionStatus.IN_REVIEW, draft, "reddit", PulseConfig())
    live = [b for b in blockers if "not live yet" in b]
    assert len(live) == 1 and "LNK-GUIDE-GLP-1-WEIGHT-LOSS" in live[0]
    # The guide's gated-download disclosure is a FINALIZE item too (Protocol §8, §13).
    # ... so that claim is not publishable yet either.
    assert all("not live yet" in b or b.startswith("FINALIZE") or b.endswith(": CLM-EDU-GUIDE-GLP-1-WEIGHT-LOSS")
               for b in blockers), blockers
    with pytest.raises(review.ReviewError) as err:
        run(review.approve(state, mention_id, "reviewer@pulse.test", PulseConfig()))
    assert err.value.status == 409 and "not live yet" in err.value.detail

    detail = run(review.detail(state, mention_id, PulseConfig()))
    assert detail["tracked_link"]["live"] is False and detail["tracked_link"]["utm_content"] == f"m{mention_id}"
    assert detail["approval"]["allowed"] is False


def test_approval_goes_through_once_the_link_is_live(tmp_path, monkeypatch):
    _config_copy(tmp_path, monkeypatch, approve=GOOD_IDS, live=True, finalize=("gated_download_disclosure",))
    state, mention_id, _ = _pipeline(tmp_path)

    run(review.approve(state, mention_id, "reviewer@pulse.test", PulseConfig()))

    assert run(state.get_mention(mention_id)).status is MentionStatus.APPROVED


def test_human_edit_recomputes_the_stored_link(tmp_path):
    state, mention_id, _ = _pipeline(tmp_path)
    text = f"{DISCLOSURE} Our free guide series: https://wellpeps.com/smart-patient-guides"
    run(review.edit(state, mention_id, text, ["CLM-R3-DISCLOSURE", "CLM-EDU-GUIDES"], "rev@pulse.test",
                    PulseConfig()))
    draft = run(state.get_latest_draft(mention_id))
    assert draft.link["id"] == "LNK-GUIDES" and draft.link["utm_content"] == ""
    run(review.edit(state, mention_id, f"{DISCLOSURE} No link now.", ["CLM-R3-DISCLOSURE"], "rev@pulse.test",
                    PulseConfig()))
    assert run(state.get_latest_draft(mention_id)).link is None


# --- Migration v7 ------------------------------------------------------------------------


def test_migration_v7_adds_drafts_link_json(tmp_path):
    path = tmp_path / "m.db"
    run(StateManager(str(path)).init_db())
    with sqlite3.connect(path) as db:
        columns = {r[1] for r in db.execute("PRAGMA table_info(drafts)")}
        assert "link_json" in columns
        assert db.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS) >= 7


def test_postgres_0003_is_idempotent_and_bumps_to_7():
    sql = (PROJECT_ROOT / "db" / "postgres" / "0003_draft_links.sql").read_text(encoding="utf-8")
    body = re.sub(r"--[^\n]*", "", sql).lower()
    assert "alter table pulse.drafts add column if not exists link_json text" in body
    assert re.search(r"values\s*\(\s*7\s*,", body) and "on conflict (version) do nothing" in body


# --- Replies & links analytics -----------------------------------------------------------


async def _approved_reply(state, key, *, platform=Platform.REDDIT, link=None, posted=False) -> int:
    mention_id, _ = await state.upsert_mention(Mention(
        platform=platform, external_id=key, text="post", url=f"https://www.reddit.com/r/x/comments/{key}/"))
    for status in (MentionStatus.TRIAGED, MentionStatus.DRAFTED, MentionStatus.IN_REVIEW, MentionStatus.APPROVED):
        await state.set_mention_status(mention_id, status)
    draft_id = await state.add_draft(Draft(mention_id=mention_id, text="t", claim_ids=["CLM-R3-DISCLOSURE"],
                                           link=link))
    await state.append_audit(AuditEvent(mention_id=mention_id, draft_id=draft_id,
                                        event=AuditEventType.APPROVED, actor="rev"))
    if posted:
        await state.set_mention_status(mention_id, MentionStatus.POSTED)
        await state.append_audit(AuditEvent(mention_id=mention_id, draft_id=draft_id,
                                            event=AuditEventType.POSTED, actor="rev"))
    return mention_id


def _link(mention_id, term="x"):
    return {"id": "LNK-GUIDE-GLP-1-WEIGHT-LOSS", "label": "GLP-1 guide", "url": GLP1,
            "utm_content": f"m{mention_id}", "utm_term": term}


def test_reply_analytics_counts_by_platform_and_link(tmp_path):
    state = StateManager(str(tmp_path / "r.db"))
    run(state.init_db())
    a = run(_approved_reply(state, "a", link=_link(1)))
    run(_approved_reply(state, "b", link=_link(2, term="=cmd()"), posted=True))
    run(_approved_reply(state, "c", platform=Platform.FACEBOOK))

    report = run(reply_analytics.replies(state, 30))

    assert report["approved"] == 2 and report["posted"] == 1
    assert {p["platform"]: p["total"] for p in report["by_platform"]} == {"reddit": 2, "facebook": 1}
    by_link = {l["link_id"]: l for l in report["by_link"]}
    assert by_link["LNK-GUIDE-GLP-1-WEIGHT-LOSS"]["total"] == 2 and by_link["none"]["total"] == 1
    assert by_link["LNK-GUIDE-GLP-1-WEIGHT-LOSS"]["live"] is False
    assert [r["utm_content"] for r in report["rows"]] == ["m1", "m2"]
    csv_text = reply_analytics.to_csv(report)
    assert csv_text.splitlines()[0] == ",".join(reply_analytics.CSV_COLUMNS)
    assert "'=cmd()" in csv_text  # spreadsheet formulas are neutralised
    assert a  # seeded


def test_reply_analytics_window_and_validation(tmp_path):
    from datetime import datetime, timedelta, timezone
    state = StateManager(str(tmp_path / "r.db"))
    run(state.init_db())
    run(_approved_reply(state, "a", link=_link(1)))
    later = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=40)
    assert run(reply_analytics.replies(state, 30, now=later))["rows"] == []
    assert reply_analytics.parse_days(None) == 30
    for bad in ("0", "367", "x"):
        with pytest.raises(reply_analytics.RepliesError):
            reply_analytics.parse_days(bad)


def test_replies_card_script_follows_the_csp_rules():
    js = (PROJECT_ROOT / "harvey" / "web" / "replies.js").read_text(encoding="utf-8")
    html = (PROJECT_ROOT / "harvey" / "web" / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"\son\w+=|style=", js)
    assert '<script src="/static/replies.js"></script>' in html and 'id="rp-card"' in html
    app_js = (PROJECT_ROOT / "harvey" / "web" / "app.js").read_text(encoding="utf-8")
    assert "function trackedLinkHtml" in app_js and "escHtml(t.utm_content)" in app_js


# --- Dashboard API -------------------------------------------------------------------------


def test_replies_api_json_csv_and_validation(tmp_path, monkeypatch):
    from tests.dashboard_helpers import VIEWER, client_for, setup_app, teardown_app

    state, _ = setup_app(tmp_path, monkeypatch)
    try:
        run(_approved_reply(state, "a", link=_link(1), posted=True))
        client, _ = client_for(VIEWER)

        data = client.get("/api/analytics/replies?days=30").json()
        assert data["posted"] == 1 and data["rows"][0]["utm_content"] == "m1"

        resp = client.get("/api/analytics/replies?format=csv")
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/csv")
        assert "attachment" in resp.headers["content-disposition"]
        assert resp.text.splitlines()[1].startswith("m1,LNK-GUIDE-GLP-1-WEIGHT-LOSS,reddit,")

        assert client.get("/api/analytics/replies?days=999").status_code == 400
        assert client_for()[0].get("/api/analytics/replies").status_code == 401
    finally:
        teardown_app()
