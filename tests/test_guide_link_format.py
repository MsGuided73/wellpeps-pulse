"""Short guide links (owner request 2026-10-23): "Can we hide the really long Patient's Guide
Links? Can we change the response to 'Just click here to grab a free copy of the guide' and
associate the long URL with the word 'here'".

On Reddit (Markdown) the offer becomes "Just click [here](<tracked url>) to grab a free copy of
the guide."; platforms without linked text (Facebook, Instagram, ...) keep the visible URL.
The dashboard shows only the word "here" and copies a real hyperlink. No network, no Claude.
"""

from pathlib import Path

import pytest

from harvey import guides, links
from harvey.agents.drafter import Drafter
from harvey.compliance import ReplyContext, compliance_filter
from harvey.models import Category, Mention, Platform, Triage
from tests.pulse_helpers import FakeBrain

ROOT = Path(__file__).resolve().parents[1]
URL = ("https://wellpeps.com/smart-patient-guides/glp-1-weight-loss?utm_source=reddit&utm_medium=community"
       "&utm_campaign=pulse&utm_content=m17&utm_term=semaglutide")
ANSWER = ("I work with WellPeps. It varies by provider, so ask each one what follow-up with a licensed clinician "
          "is included. Our free GLP-1 Weight Loss guide has a section, \"What's actually included in the "
          "price\", that walks through what to check.")
LINKED = f"{ANSWER} Just click [here]({URL}) to grab a free copy of the guide."


@pytest.mark.parametrize("offer", [
    f"Just click here to grab a free copy of the guide: {URL}",
    f"Just click here to grab a free copy of the guide {URL}.",
    f"Here's a link to get a free copy if you'd like: {URL}",
    f"Here's a link to get a free copy if you'd like: {URL}.",
])
def test_reddit_hides_the_url_behind_here(offer):
    assert links.hyperlink_offers(f"{ANSWER} {offer}", "reddit") == LINKED


def test_already_linked_text_is_unchanged():
    assert links.hyperlink_offers(LINKED, "reddit") == LINKED


def test_platforms_without_linked_text_keep_the_visible_url():
    text = f"{ANSWER} Just click here to grab a free copy of the guide: {URL}"
    assert links.hyperlink_offers(text, "facebook") == text


def test_a_url_outside_the_registry_is_never_hidden():
    text = f"{ANSWER} Just click here to grab a free copy of the guide: https://example.com/x"
    assert links.hyperlink_offers(text, "reddit") == text


def test_the_filter_still_sees_the_guide_link():
    assert guides.linked_guides(LINKED) == ["LNK-GUIDE-GLP-1-WEIGHT-LOSS"]
    ctx = ReplyContext(community_id="reddit:r/x", participation="allowed", links_allowed=True)
    ids = ["CLM-AMG-04-WORK-WITH", "CLM-R7-PROVIDER-CHECKLIST", "CLM-EDU-GUIDE-GLP-1-WEIGHT-LOSS"]
    result = compliance_filter(LINKED, "reddit", ids, context=ctx)
    assert [h for h in result.red_hits] == [], result.red_hits
    assert guides.GATE_RX.search(LINKED)


def test_the_drafter_links_the_word_here_on_reddit():
    reply = f"{ANSWER} Just click here to grab a free copy of the guide: https://wellpeps.com/smart-patient-guides/glp-1-weight-loss"
    brain = FakeBrain([{"reply": reply, "claim_ids": ["CLM-AMG-04-WORK-WITH"], "rationale": "x"}])
    mention = Mention(id=17, platform=Platform.REDDIT, text="what's included in the price?",
                      url="https://www.reddit.com/r/semaglutide/comments/abc/t/")
    triage = Triage(mention_id=17, category=Category.QUESTION, drug="semaglutide", reply_appropriate=True,
                    subject_type="category", relevant=True)
    import asyncio
    proposal = asyncio.run(Drafter(brain).draft(mention, triage))
    assert "Just click [here](https://wellpeps.com/smart-patient-guides/glp-1-weight-loss?utm_" in proposal.reply
    assert proposal.reply.endswith(") to grab a free copy of the guide.")


def test_prompts_ask_for_the_new_offer_wording():
    for path in ("prompts/draft.md", "prompts/review.md"):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert links.GUIDE_OFFER in text, path
        assert "Here's a link to get a free copy" not in text, path


def test_dashboard_shows_here_and_copies_a_hyperlink():
    web = ROOT / "harvey" / "web"
    shared = (web / "labels.js").read_text(encoding="utf-8")
    assert "MD_LINK" in shared and "ClipboardItem" in shared and "text/html" in shared
    home = (web / "home.js").read_text(encoding="utf-8")
    assert "replyHtml(text)" in home and "copyReply(text)" in home
    assert "copyReply(draft.text)" in (web / "app.js").read_text(encoding="utf-8")


def test_rewrite_maps_the_shown_selection_back_to_the_saved_text():
    """Owner report 2026-10-23: rewriting post 1 failed with "Couldn't find that passage" because the
    selection read "click here" while the saved draft has "click [here](link)"."""
    web = ROOT / "harvey" / "web"
    assert "function replySourceRange" in (web / "labels.js").read_text(encoding="utf-8")
    rewrite_js = (web / "rewrite.js").read_text(encoding="utf-8")
    assert "replySourceRange(card.text" in rewrite_js and "card.text.slice(from, to)" in rewrite_js
    from harvey import rewrite
    assert "[here](https://...)" in rewrite.GUARDRAILS
