"""Review desk APIs: community permissions (overlay over config/communities.yaml),
GET /api/spend, and the review-queue fields on GET /api/mentions?include_draft=true."""

import json
from datetime import datetime, timedelta, timezone

import pytest

import harvey.dashboard as dashboard
from harvey import communities, knowledge, spend
from harvey.compliance import ReplyContext, compliance_filter
from harvey.models import AuditEventType, Mention, Platform
from harvey.state import StateManager
from tests.dashboard_helpers import (
    ADMIN,
    CLEAN_IDS,
    CLEAN_REPLY,
    REVIEWER,
    VIEWER,
    add_mention,
    add_review_item,
    client_for,
    post,
    run,
    setup_app,
    teardown_app,
)

SEMA = "reddit:r/semaglutide"                       # in config/communities.yaml, rules unverified
SEMA_URL = "https://www.reddit.com/r/Semaglutide/comments/abc123/question/"
NEW_COMMUNITY = "reddit:r/brandnewsub"
PERMISSION = {"brand_participation": "allowed", "links_allowed": "true",
              "moderator_contact": "modmail for r/Semaglutide", "note": "Mods said brand replies are fine."}


@pytest.fixture
def env(tmp_path, monkeypatch):
    state, _ = setup_app(tmp_path, monkeypatch)
    yield state
    teardown_app()


def permission_url(cid: str) -> str:
    return f"/api/communities/{cid}/permission"


# --- Overlay precedence ---------------------------------------------------------------------------------------


def test_yaml_only_when_nothing_is_recorded():
    entry = communities.lookup(SEMA)

    assert entry.brand_participation == "unknown" and entry.links_allowed == "unknown"
    status = communities.status_for_url(SEMA_URL)
    assert status.participation == "unknown" and status.links_allowed is None and status.registered


@pytest.mark.parametrize("recorded,participation,links", [
    ("allowed", "allowed", True), ("not_allowed", "prohibited", False), ("unknown", "unknown", None)])
def test_a_recorded_permission_wins_over_the_yaml_for_every_caller(recorded, participation, links):
    links_value = {True: "true", False: "false", None: "unknown"}[links]
    communities.set_overlay([communities.Recorded(SEMA, recorded, links_value, "mods@example.org", "checked",
                                                  "admin@pulse.test", "2026-10-20 10:00:00")])

    status = communities.status_for_url(SEMA_URL)
    entry = communities.lookup(SEMA)

    assert status.participation == participation and status.links_allowed is links
    assert entry.brand_participation == participation and entry.moderator_contact == "mods@example.org"
    assert str(entry.rules_checked_at) == "2026-10-20"
    assert entry.name == "r/Semaglutide"   # the YAML identity is kept


def test_recorded_rules_change_what_the_compliance_filter_allows():
    link = "Our free guide: https://wellpeps.com/guides/smart-patients-guide"
    text = f"I work with WellPeps. {link}"

    def tier():
        status = communities.status_for_url(SEMA_URL)
        ctx = ReplyContext(community_id=status.id, participation=status.participation,
                           permission_obtained=status.permission_obtained, links_allowed=status.links_allowed)
        return compliance_filter(text, "reddit", [], context=ctx)

    def r44():
        return {h.reason for h in tier().hits if h.rule_id == "R44"}

    unverified = r44()
    communities.set_overlay([communities.Recorded(SEMA, "allowed", "false")])
    no_links = r44()
    communities.set_overlay([communities.Recorded(SEMA, "allowed", "true")])
    links_ok = r44()

    assert any("unverified" in reason for reason in unverified)
    assert no_links == {"no link here: the community does not allow links"}
    assert links_ok == set()


def test_a_community_only_in_the_database_is_known_to_lookups():
    communities.set_overlay([communities.Recorded(NEW_COMMUNITY, "allowed", "true", recorded_at="2026-10-20 09:00:00")])

    entry = communities.lookup(NEW_COMMUNITY)
    status = communities.status_for_url("https://www.reddit.com/r/BrandNewSub/comments/zzz/")

    assert entry.name == "r/brandnewsub" and entry.brand_participation == "allowed"
    assert status.participation == "allowed" and status.links_allowed is True and status.registered


def test_ensure_overlay_loads_rows_written_by_another_process(env):
    state = env
    run(_insert_permission(state, SEMA, "not_allowed", "false"))
    communities.clear_overlay()

    run(communities.ensure_overlay(state))

    assert communities.status_for_url(SEMA_URL).participation == "prohibited"


async def _insert_permission(state, cid, participation, links):
    async with state.connect() as db:
        await db.execute("INSERT INTO community_permissions (community_id, brand_participation, links_allowed) "
                         "VALUES (?, ?, ?)", (cid, participation, links))
        await db.commit()


def test_the_overlay_is_reloaded_after_its_ttl(env):
    state = env
    run(communities.ensure_overlay(state))
    run(_insert_permission(state, SEMA, "allowed", "true"))

    run(communities.ensure_overlay(state))                   # still fresh: no reload
    assert communities.status_for_url(SEMA_URL).participation == "unknown"
    run(communities.ensure_overlay(state, max_age=0))
    assert communities.status_for_url(SEMA_URL).participation == "allowed"


def test_draft_batch_and_triage_batch_load_the_overlay(env):
    from harvey.agents.triager import triage_batch
    from harvey.drafting import draft_batch

    state = env
    run(_insert_permission(state, SEMA, "not_allowed", "false"))

    class Unused:
        pass

    run(draft_batch(state, Unused(), Unused()))
    assert communities.status_for_url(SEMA_URL).participation == "prohibited"
    communities.clear_overlay()
    run(triage_batch(state, Unused()))
    assert communities.status_for_url(SEMA_URL).participation == "prohibited"


# --- GET /api/communities ---------------------------------------------------------------------------------------


def test_list_needs_a_session_and_lists_the_registry(env):
    assert client_for()[0].get("/api/communities").status_code == 401
    client, _ = client_for(VIEWER)

    resp = client.get("/api/communities")

    assert resp.status_code == 200
    rows = resp.json()["communities"]
    sema = next(r for r in rows if r["id"] == SEMA)
    assert set(sema) >= {"id", "platform", "name", "topic", "brand_participation", "links_allowed",
                         "permission_obtained", "rules_checked_at", "source", "mentions_7d"}
    assert sema["platform"] == "reddit" and sema["name"] == "r/Semaglutide" and sema["topic"] == "weight"
    assert sema["brand_participation"] == "unknown" and sema["links_allowed"] == "unknown"
    assert sema["source"] == "config" and sema["mentions_7d"] == 0 and sema["permission_obtained"] is False
    assert len(rows) == len(knowledge.communities())


def test_list_counts_the_last_seven_days_of_mentions(env):
    state = env
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for key, age_days in (("a1", 1), ("a2", 3), ("old", 20)):
        mid, _ = run(state.upsert_mention(Mention(
            platform=Platform.REDDIT, external_id=key, text="post",
            url=f"https://www.reddit.com/r/Semaglutide/comments/{key}/", collected_at=now - timedelta(days=age_days))))
    run(add_mention(state, "elsewhere"))
    client, _ = client_for(VIEWER)

    rows = {r["id"]: r for r in client.get("/api/communities").json()["communities"]}

    assert rows[SEMA]["mentions_7d"] == 2


# --- POST /api/communities/{id}/permission ---------------------------------------------------------------------------


def test_record_needs_admin_and_csrf(env):
    assert client_for()[0].post(permission_url(SEMA), json=PERMISSION).status_code == 401
    for email in (VIEWER, REVIEWER):
        client, csrf = client_for(email)
        assert post(client, csrf, permission_url(SEMA), PERMISSION).status_code == 403
    admin, _ = client_for(ADMIN)
    assert admin.post(permission_url(SEMA), json=PERMISSION).status_code == 403   # no CSRF header


def test_record_returns_the_community_and_the_listing_shows_the_record(env):
    admin, csrf = client_for(ADMIN)

    resp = post(admin, csrf, permission_url(SEMA), PERMISSION)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == SEMA and body["brand_participation"] == "allowed" and body["links_allowed"] == "true"
    assert body["source"] == "recorded" and body["permission_obtained"] is True
    assert body["moderator_contact"] == PERMISSION["moderator_contact"] and body["note"] == PERMISSION["note"]
    assert body["recorded_by"] == ADMIN and body["rules_checked_at"] == body["recorded_at"][:10]
    listed = {r["id"]: r for r in admin.get("/api/communities").json()["communities"]}
    assert listed[SEMA]["source"] == "recorded" and listed[SEMA]["brand_participation"] == "allowed"
    assert communities.status_for_url(SEMA_URL).links_allowed is True   # every existing caller sees it


def test_recording_keeps_the_configured_identity_and_topic(env):
    admin, csrf = client_for(ADMIN)
    before = next(r for r in admin.get("/api/communities").json()["communities"] if r["id"] == SEMA)

    posted = post(admin, csrf, permission_url(SEMA), PERMISSION).json()
    after = next(r for r in admin.get("/api/communities").json()["communities"] if r["id"] == SEMA)

    assert before["topic"] == "weight"
    for row in (posted, after):
        assert row["topic"] == "weight" and row["name"] == "r/Semaglutide" and row["platform"] == "reddit"
        assert row["note"] == PERMISSION["note"] and row["brand_participation"] == "allowed"
    entry = communities.lookup(SEMA)
    assert "topic weight" in entry.notes and entry.name == "r/Semaglutide"   # notes stay the YAML's


def test_record_accepts_booleans_and_updates_in_place(env):
    admin, csrf = client_for(ADMIN)
    post(admin, csrf, permission_url(SEMA), PERMISSION)

    resp = post(admin, csrf, permission_url(SEMA), {"brand_participation": "not_allowed", "links_allowed": False})

    assert resp.json()["brand_participation"] == "not_allowed" and resp.json()["links_allowed"] == "false"
    assert resp.json()["moderator_contact"] == ""   # optional field, omitted this time
    rows = [r for r in admin.get("/api/communities").json()["communities"] if r["id"] == SEMA]
    assert len(rows) == 1 and rows[0]["brand_participation"] == "not_allowed"
    assert communities.status_for_url(SEMA_URL).participation == "prohibited"


def test_record_creates_a_community_that_is_not_in_the_yaml(env):
    admin, csrf = client_for(ADMIN)

    resp = post(admin, csrf, permission_url(NEW_COMMUNITY), {"brand_participation": "allowed",
                                                            "links_allowed": "unknown"})

    assert resp.status_code == 200 and resp.json()["name"] == "r/brandnewsub"
    assert any(r["id"] == NEW_COMMUNITY for r in admin.get("/api/communities").json()["communities"])


@pytest.mark.parametrize("body", [
    {"links_allowed": "true"},
    {"brand_participation": "allowed"},
    {"brand_participation": "maybe", "links_allowed": "true"},
    {"brand_participation": "allowed", "links_allowed": "sometimes"},
    {"brand_participation": "allowed", "links_allowed": 3},
    {"brand_participation": "allowed", "links_allowed": "true", "note": "n" * 1001},
    {"brand_participation": "allowed", "links_allowed": "true", "moderator_contact": "c" * 201},
])
def test_invalid_records_are_400(env, body):
    admin, csrf = client_for(ADMIN)

    assert post(admin, csrf, permission_url(SEMA), body).status_code == 400


def test_a_malformed_community_id_is_400_and_a_bad_body_is_400(env):
    admin, csrf = client_for(ADMIN)

    assert post(admin, csrf, permission_url("not a community"), PERMISSION).status_code == 400
    assert post(admin, csrf, permission_url("twitter:someone"), PERMISSION).status_code == 400
    raw = admin.post(permission_url(SEMA), content=b"[]",
                     headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"})
    assert raw.status_code == 400


def test_recording_is_audited_with_the_old_and_new_values(env):
    state = env
    admin, csrf = client_for(ADMIN)
    post(admin, csrf, permission_url(SEMA), PERMISSION)
    post(admin, csrf, permission_url(SEMA), {"brand_participation": "not_allowed", "links_allowed": "false"})

    async def rows():
        async with state.connect() as db:
            async with db.execute("SELECT actor, verdict_json FROM audit_log WHERE event = ? ORDER BY id",
                                  (AuditEventType.COMMUNITY_PERMISSION.value,)) as cursor:
                return [(r[0], json.loads(r[1])) for r in await cursor.fetchall()]

    audit = run(rows())

    assert [a for a, _ in audit] == [ADMIN, ADMIN]
    assert audit[0][1]["from"] is None and audit[0][1]["to"]["brand_participation"] == "allowed"
    assert audit[1][1]["from"]["brand_participation"] == "allowed"
    assert audit[1][1]["to"]["brand_participation"] == "not_allowed"


# --- GET /api/spend ------------------------------------------------------------------------------------------------


def test_spend_needs_a_session(env):
    assert client_for()[0].get("/api/spend").status_code == 401


def test_spend_totals_this_months_claude_and_apify_cost(env, monkeypatch):
    state = env
    monkeypatch.setenv("PULSE_CLAUDE_BILLING", "subscription")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    last_month = spend.month_start(now) - timedelta(days=3)
    run(state.record_usage_event(agent="drafter", task="reply", cost_usd=1.25))
    run(state.record_usage_event(agent="drafter", task="reply", cost_usd=0.75))
    run(state.record_usage_event(agent="rewriter", task="rewrite", cost_usd=0.5))
    run(state.record_usage_event(agent="", task="x", cost_usd=0.25))
    run(state.record_usage_event(agent="reviewer", task="review", cost_usd=9.0, created_at=last_month.isoformat(" ")))
    first = run(state.start_run("apify_reddit", provider="collector"))
    run(state.finish_run(first, cost_usd=0.04))
    second = run(state.start_run("apify_reddit", provider="collector"))
    run(state.finish_run(second, cost_usd=0.06))
    other = run(state.start_run("fixture", provider="collector"))
    run(state.finish_run(other, cost_usd=5.0))
    client, _ = client_for(VIEWER)

    resp = client.get("/api/spend")

    assert resp.status_code == 200
    body = resp.json()
    assert body["month"] == now.strftime("%Y-%m") and body["month_label"] == now.strftime("%B")
    assert body["claude"] == {"total": 2.75, "by_agent": {"drafter": 2.0, "rewriter": 0.5, "other": 0.25},
                              "billing": "subscription"}
    assert body["apify"] == {"total": 0.1, "runs": 2}
    assert body["total"] == 2.85


def test_spend_on_an_empty_database_is_zero(env, monkeypatch):
    monkeypatch.setenv("PULSE_CLAUDE_BILLING", "api")
    client, _ = client_for(VIEWER)

    body = client.get("/api/spend").json()

    assert body["claude"] == {"total": 0.0, "by_agent": {}, "billing": "api"}
    assert body["apify"] == {"total": 0.0, "runs": 0} and body["total"] == 0.0


def test_spend_billing_falls_back_when_misconfigured(monkeypatch):
    monkeypatch.setenv("PULSE_CLAUDE_BILLING", "gold")

    assert spend.billing_label() == "subscription"


# --- Review-queue fields on /api/mentions --------------------------------------------------------------------------------


def test_mentions_without_the_flag_keep_their_shape(env):
    state = env
    run(add_review_item(state))
    client, _ = client_for(VIEWER)

    item = client.get("/api/mentions?status=in_review").json()["items"][0]

    assert "draft" not in item and "community" not in item and "guide" not in item
    assert {"id", "url", "title", "posted_at", "platform", "status", "text"} <= set(item)


def test_include_draft_adds_the_draft_community_and_guide(env):
    state = env
    mid = run(add_review_item(state))
    run(add_mention(state, "bare"))                       # a mention with no draft or triage
    client, _ = client_for(VIEWER)

    items = client.get("/api/mentions?status=in_review&include_draft=true").json()["items"]
    item = next(i for i in items if i["id"] == mid)

    assert item["draft"]["text"] == CLEAN_REPLY and item["draft"]["claim_ids"] == CLEAN_IDS
    assert item["draft"]["tier"] == "green" and item["draft"]["version"] == 1
    assert set(item["draft"]) >= {"id", "hits", "review_verdict", "review_reasons", "filter_ok", "created_at"}
    assert item["community"]["id"] == "reddit:r/test" and item["community"]["allows_brand_replies"] is None
    assert "guide" in item
    everything = client.get("/api/mentions?include_draft=true").json()["items"]
    bare = next(i for i in everything if i["url"].endswith("/bare/"))
    assert bare["draft"] is None and bare["guide"] is None


def test_community_flag_follows_recorded_rules(env):
    state = env
    mid, _ = run(state.upsert_mention(Mention(platform=Platform.REDDIT, external_id="s1", text="post",
                                              url=SEMA_URL)))
    client, csrf = client_for(ADMIN)

    def community():
        items = client.get("/api/mentions?include_draft=true").json()["items"]
        return next(i for i in items if i["id"] == mid)["community"]

    assert community()["allows_brand_replies"] is None and community()["id"] == SEMA
    admin_resp = post(client, csrf, permission_url(SEMA), PERMISSION)
    assert admin_resp.status_code == 200
    assert community()["allows_brand_replies"] is True and community()["links_allowed"] is True
    post(client, csrf, permission_url(SEMA), {"brand_participation": "not_allowed", "links_allowed": "false"})
    assert community()["allows_brand_replies"] is False


def test_hits_are_parsed_into_rule_and_reason():
    from harvey import review

    assert review.hit_dict("R12: no guarantees [guaranteed]") == {"rule": "R12", "reason": "no guarantees"}
    assert review.hit_dict("plain text") == {"rule": "", "reason": "plain text"}


def test_mention_detail_carries_the_community(env):
    state = env
    mid = run(add_review_item(state))
    client, _ = client_for(VIEWER)

    detail = client.get(f"/api/mentions/{mid}").json()

    assert "allows_brand_replies" in detail["community"]
    assert detail["latest_draft"]["text"] == CLEAN_REPLY
