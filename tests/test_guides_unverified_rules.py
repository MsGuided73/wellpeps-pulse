"""Guides in communities whose rules are unverified (R-40, owner decision 2026-10-22).

"Please allow Pulse to reference the guides when appropriate (no block). We can
guard against any rule violations by preventing Pulse itself from POSTING in
the communities."

Unverified rules no longer stop a Smart Patient's Guide or its tracked link:
a WellPeps rep posts every reply by hand and checks the community's rules
first. A rule someone actually recorded still counts: "links not allowed"
means the guide is named without a link, and in enforced mode a community
that bans brand replies still gets no guide. No network, no Claude.
"""

from harvey import communities, engagement, guides
from harvey.compliance import ReplyContext, compliance_filter
from harvey.models import Category
from tests.test_guides import IDS, WITH_GUIDE, _mention, _requirement, _triage

UNKNOWN = ReplyContext(community_id="reddit:r/x", participation="unknown", links_allowed=None)
UNKNOWN_NO_LINKS = ReplyContext(community_id="reddit:r/x", participation="unknown", links_allowed=False)
PROHIBITED = ReplyContext(community_id="reddit:r/x", participation="prohibited", links_allowed=False)


def test_unverified_rules_get_the_guide_with_its_link():
    req = _requirement(UNKNOWN)
    assert req.mode == "link" and req.pick is not None


def test_a_recorded_no_links_rule_still_means_the_guide_is_named_without_one():
    req = _requirement(UNKNOWN_NO_LINKS)
    assert req.mode == "name" and "does not allow links" in req.why


def test_a_community_that_bans_brand_replies_still_gets_no_guide():
    assert _requirement(PROHIBITED).mode == "forbidden"


def test_unverified_rules_do_not_forbid_a_link():
    assert UNKNOWN.link_forbidden == ""
    assert UNKNOWN_NO_LINKS.link_forbidden == "the community does not allow links"


def test_a_guide_link_in_an_unverified_community_is_not_red():
    result = compliance_filter(WITH_GUIDE, "reddit", IDS, context=UNKNOWN)
    assert [h for h in result.red_hits if h.rule_id in ("R44", "GUIDE")] == []
    # The reviewer still sees the reminder to check the rules before posting.
    assert any(h.rule_id == "R44" and "community rules unverified" in h.reason for h in result.hits)


def test_the_drafter_is_allowed_the_guide_link():
    t = _triage(Category.PURCHASE_INTENT, "pricing_question", drug="tirzepatide")
    guidance = engagement.guidance_for(engagement.situation_of(t), UNKNOWN, t, _mention(WITH_GUIDE))
    assert guidance.allow_link is True
    assert guidance.guide_mode == "link"


def test_the_communities_page_no_longer_lists_a_guide_or_link_block(monkeypatch):
    monkeypatch.setenv(communities.RULES_ENV, "advisory")
    row = {"id": "reddit:r/x", "brand_participation": "unknown", "links_allowed": "unknown"}
    blocks = " ".join(communities.clearance(row)["blocks"])
    assert communities.GUIDE_BLOCK not in blocks and "No links" not in blocks
    assert "acknowledge" in blocks.lower()


def test_the_reviewer_prompt_does_not_ban_guides_in_unverified_communities():
    from pathlib import Path
    review = (Path(__file__).resolve().parents[1] / "prompts" / "review.md").read_text(encoding="utf-8")
    assert "whose rules are unverified" not in review


def test_the_guide_check_does_not_flag_a_guide_in_an_unverified_community():
    red, _ = guides.reference_hits(WITH_GUIDE, IDS, _requirement(UNKNOWN))
    assert red == []
