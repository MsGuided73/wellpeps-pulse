"""The Communities page (owner request 2026-10-22).

Every community Pulse collects from, with a link to the group, a clearance
checklist (rules read, brand replies, links, moderator contact) and, in plain
words, what stays blocked until it is cleared. Communities seen only in
collected posts are listed too, so nothing Pulse pulls from is invisible.
No network, no Claude.
"""

from datetime import datetime, timezone

import pytest

from harvey import communities
from harvey.models import Mention, Platform
from tests.dashboard_helpers import ADMIN, VIEWER, client_for, post, run, setup_app, teardown_app

SEMA = "reddit:r/semaglutide"


@pytest.fixture
def env(tmp_path, monkeypatch):
    state, _ = setup_app(tmp_path, monkeypatch)
    yield state
    teardown_app()


def _row(**kw) -> dict:
    base = {"id": SEMA, "platform": "reddit", "name": "r/Semaglutide", "brand_participation": "unknown",
            "links_allowed": "unknown", "rules_checked_at": None, "moderator_contact": "", "competitor": ""}
    base.update(kw)
    return base


# --- Group links ------------------------------------------------------------------------------------


@pytest.mark.parametrize("cid, url", [
    ("reddit:r/semaglutide", "https://www.reddit.com/r/semaglutide/"),
    ("facebook:groups/glp1support", "https://www.facebook.com/groups/glp1support/"),
    ("twitter:whatever", ""),
])
def test_each_community_links_to_its_group(cid, url):
    assert communities.community_url(cid) == url


# --- Clearance checklist and blocks -----------------------------------------------------------------


def _items(clearance) -> dict:
    return {i["key"]: i["state"] for i in clearance["items"]}


def test_an_unchecked_community_lists_every_step_and_its_blocks(monkeypatch):
    monkeypatch.setenv(communities.RULES_ENV, "advisory")
    c = communities.clearance(_row())
    assert c["status"] == "not_checked"
    assert _items(c) == {"rules_read": "todo", "brand_replies": "todo", "links": "todo", "moderator": "optional"}
    blocks = " ".join(c["blocks"])
    # R-40: unverified rules block neither the guide nor its link.
    assert "Smart Patient's Guide" not in blocks and "links" not in blocks.lower()
    assert "acknowledge" in blocks.lower()            # advisory mode: the rep confirms before approving


def test_enforced_mode_says_approval_is_refused(monkeypatch):
    monkeypatch.setenv(communities.RULES_ENV, "enforced")
    blocks = " ".join(communities.clearance(_row())["blocks"]).lower()
    assert "can't be approved" in blocks


def test_a_cleared_community_with_links_has_nothing_blocked(monkeypatch):
    monkeypatch.setenv(communities.RULES_ENV, "advisory")
    c = communities.clearance(_row(brand_participation="allowed", links_allowed="true", rules_checked_at="2026-10-22",
                                   moderator_contact="modmail"))
    assert c["status"] == "cleared" and c["blocks"] == []
    assert _items(c) == {"rules_read": "done", "brand_replies": "done", "links": "done", "moderator": "done"}


def test_no_links_means_the_guide_is_named_without_one(monkeypatch):
    monkeypatch.setenv(communities.RULES_ENV, "advisory")
    c = communities.clearance(_row(brand_participation="allowed", links_allowed="false",
                                   rules_checked_at="2026-10-22"))
    assert c["status"] == "cleared"
    assert _items(c)["links"] == "done"
    assert any("named without a link" in b for b in c["blocks"])


def test_a_community_that_bans_brand_replies_says_so(monkeypatch):
    monkeypatch.setenv(communities.RULES_ENV, "advisory")
    c = communities.clearance(_row(brand_participation="not_allowed", links_allowed="false",
                                   rules_checked_at="2026-10-22"))
    assert c["status"] == "not_allowed"
    assert _items(c)["brand_replies"] == "blocked"
    assert any("don't allow brand replies" in b for b in c["blocks"])


def test_a_competitors_own_community_is_flagged(monkeypatch):
    monkeypatch.setenv(communities.RULES_ENV, "advisory")
    c = communities.clearance(_row(id="reddit:r/noom", name="r/Noom", competitor="Noom Med"))
    assert any("Noom Med" in b for b in c["blocks"])


# --- The listing ------------------------------------------------------------------------------------


def test_the_listing_adds_communities_seen_only_in_collected_posts(env):
    state = env
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    run(state.upsert_mention(Mention(platform=Platform.REDDIT, external_id="s1", text="post", collected_at=now,
                                     url="https://www.reddit.com/r/SnoringHelp/comments/s1/")))
    client, _ = client_for(VIEWER)

    rows = {r["id"]: r for r in client.get("/api/communities").json()["communities"]}

    seen = rows["reddit:r/snoringhelp"]
    assert seen["source"] == "seen" and seen["mentions_total"] == 1 and seen["mentions_7d"] == 1
    assert seen["url"] == "https://www.reddit.com/r/snoringhelp/"
    assert seen["clearance"]["status"] == "not_checked"
    sema = rows[SEMA]
    assert sema["url"] == "https://www.reddit.com/r/semaglutide/" and sema["mentions_total"] == 0
    assert {"status", "items", "blocks"} <= set(sema["clearance"])
    # Busiest first: a community Pulse collects from comes before an empty registry entry.
    order = [r["id"] for r in client.get("/api/communities").json()["communities"]]
    assert order.index("reddit:r/snoringhelp") < order.index(SEMA)


def test_recording_rules_for_a_seen_community_clears_it(env):
    state = env
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    run(state.upsert_mention(Mention(platform=Platform.REDDIT, external_id="s2", text="post", collected_at=now,
                                     url="https://www.reddit.com/r/SnoringHelp/comments/s2/")))
    admin, csrf = client_for(ADMIN)

    resp = post(admin, csrf, "/api/communities/reddit:r/snoringhelp/permission",
                {"brand_participation": "allowed", "links_allowed": "true", "moderator_contact": "modmail",
                 "note": "Rule 4 allows helpful brand replies with disclosure."})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["clearance"]["status"] == "cleared" and body["url"].endswith("/r/snoringhelp/")
    rows = {r["id"]: r for r in admin.get("/api/communities").json()["communities"]}
    assert rows["reddit:r/snoringhelp"]["source"] == "recorded"
