"""Every competitor mention, not just the main subject (R-44, owner request 2026-10-23).

"Can we add an analytic section for any and all competitor mentions? ... Henry Meds, Mondae,
Gimme Care or Gimme, PreventativeMD, etc. I want to track all mentions."

Triage keeps ONE competitor per post (the main subject); on the live posts that missed 33 of the
names posts actually use. ``harvey.competitor_mentions`` finds every registry name in a post's
title and text (common-word names like "Found" or "Ro" only when capitalised and not at a
sentence start), the Analytics tab gets a "Competitor mentions" section, and the Feed's
competitor filter shows every post that names them. Aggregates only. No network, no Claude.
"""

from datetime import datetime, timedelta

import pytest

from harvey import analytics, competitor_mentions as cm, review
from harvey.models import Category, Mention, MentionStatus, Platform, Triage
from harvey.state import StateManager

POST = ("Comparing Henry Meds, Mondae and Gimme Care. PreventativeMD quoted me more, and somebody said "
        "Gimme was cheaper. I found Ro too expensive.")


def test_finds_every_competitor_named_in_a_post():
    assert set(cm.names_in(POST)) == {"Henry Meds", "Mondae", "Gimme Care", "PreventiveMD", "Ro"}


def test_lowercase_brand_names_count_but_common_words_do_not():
    assert cm.names_in("anyone tried henry meds or mondae?") == ["Henry Meds", "Mondae"]
    assert cm.names_in("I found a cheaper option and it keeps working") == []
    assert cm.names_in("Found is the one I used") == []           # sentence start: the word, not the company
    assert cm.names_in("I switched to Found last month") == ["Found"]
    assert cm.names_in("gimme a break, rex") == []


def test_each_name_counts_once_per_post():
    assert cm.names_in("Henry Meds, then henry meds again, then HenryMeds") == ["Henry Meds"]


def _row(i, text, at, competitor="", category="question", score=0.0, url="https://www.reddit.com/r/glp1/comments/a/"):
    return {"id": i, "title": "", "text": text, "competitor": competitor, "category": category,
            "sentiment_score": score, "url": url, "at": at}


def test_rollup_counts_posts_change_and_named_with():
    now = datetime(2026, 10, 23, 12)
    current = [_row(1, "Henry Meds vs Mondae", now), _row(2, "henry meds again", now, category="complaint"),
               _row(3, "Mondae is fine", now, competitor="Mondae")]
    previous = [_row(4, "Henry Meds", now - timedelta(days=40))]
    out = cm.rollup(current, previous)
    by = {c["name"]: c for c in out["competitors"]}
    assert out["posts_naming_any"] == 3 and out["total_mentions"] == 4
    assert by["Henry Meds"]["posts"] == 2 and by["Henry Meds"]["previous"] == 1
    assert by["Henry Meds"]["change_pct"] == 100.0
    assert by["Henry Meds"]["complaints"] == 1
    assert by["Henry Meds"]["named_with"] == [{"name": "Mondae", "posts": 1}]
    assert by["Mondae"]["main_subject"] == 1
    assert out["competitors"][0]["name"] in ("Henry Meds", "Mondae")
    assert by["Henry Meds"]["tone"] is None                      # fewer than 3 posts: no average
    assert "takeaway" in out and out["takeaway"]


def test_rollup_payload_has_no_post_text_or_ids():
    now = datetime(2026, 10, 23, 12)
    out = cm.rollup([_row(1, "Henry Meds secret text", now)], [])
    flat = repr(out)
    assert "secret text" not in flat and "reddit.com" not in flat and "'id'" not in flat


def test_communities_need_two_posts():
    now = datetime(2026, 10, 23, 12)
    rows = [_row(i, "Henry Meds", now, url=f"https://www.reddit.com/r/{sub}/comments/{i}/")
            for i, sub in enumerate(("glp1", "glp1", "tirzepatide"), 1)]
    by = {c["name"]: c for c in cm.rollup(rows, [])["competitors"]}
    assert by["Henry Meds"]["communities"] == [{"name": "r/glp1", "posts": 2}]


def test_the_analytics_endpoint_serves_the_section():
    assert "competitors" in analytics.CHARTS


@pytest.fixture
def state(tmp_path):
    import asyncio
    s = StateManager(str(tmp_path / "p.db"))
    asyncio.run(s.init_db())
    return s


def test_feed_competitor_filter_includes_posts_that_name_them(state):
    import asyncio

    async def seed():
        ids = []
        for i, (text, comp) in enumerate((("Henry Meds and Mondae", "Mondae"), ("only Ro here", "Ro"),
                                          ("nothing named", "")), 1):
            mid, _ = await state.upsert_mention(Mention(platform=Platform.REDDIT, external_id=f"e{i}", text=text,
                                                     url=f"https://www.reddit.com/r/x/comments/{i}/"))
            await state.save_triage(Triage(mention_id=mid, category=Category.QUESTION, competitor=comp))
            await state.set_mention_status(mid, MentionStatus.TRIAGED)
            ids.append(mid)
        return ids

    ids = asyncio.run(seed())
    got = asyncio.run(review.feed(state, competitor="Henry Meds"))
    assert [m["id"] for m in got["items"]] == [ids[0]] and got["total"] == 1
    got = asyncio.run(review.feed(state, competitor="Mondae"))
    assert [m["id"] for m in got["items"]] == [ids[0]]
    assert asyncio.run(review.feed(state, competitor="Zealthy"))["total"] == 0
