"""Phase 7 review dashboard API: urgent queue, feed filters, detail, and the
human review actions (edit, approve, reject, copied, mark-posted, escalate,
ack). Temp DB, fake reviewer/notifier; no network, no `claude` calls."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from harvey import knowledge
from harvey.agents.reviewer import ReviewResult
from harvey.config import PulseConfig, ReviewConfig
from harvey.models import (
    AuditEventType,
    Escalation,
    MentionStatus,
    Platform,
    ReviewVerdict,
)
from tests.dashboard_helpers import (
    ADMIN,
    CLEAN_IDS,
    CLEAN_REPLY,
    CLINICAL,
    RED_REPLY,
    REVIEWER,
    VIEWER,
    add_mention,
    add_review_item,
    approved_claims_dir,
    client_for,
    post,
    run,
    setup_app,
    teardown_app,
)


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FakeReviewer:
    def __init__(self, verdict=ReviewVerdict.PASS):
        self.verdict = verdict
        self.calls = []

    async def review(self, reply, platform, mention, claims):
        self.calls.append(reply)
        return ReviewResult(verdict=self.verdict,
                            reasons=[{"rule_id": "R1", "explanation": "fake"}], model="fake")


@pytest.fixture
def env(tmp_path, monkeypatch):
    state, notifier = setup_app(tmp_path, monkeypatch)
    yield state, notifier
    teardown_app()


@pytest.fixture
def state(env):
    return env[0]


@pytest.fixture
def reviewer_client(env):
    return client_for(REVIEWER)


def _events(state, mention_id):
    return [e for e in run(state.list_audit(mention_id))]


# --- Urgent -------------------------------------------------------------------------------


def test_urgent_lists_open_escalations_breached_first_then_soonest_sla(state):
    now = _utcnow()
    ids = {}
    for key, due, breached in (("later", 10, False), ("sooner", 5, False), ("late", -3, True),
                               ("acked", 1, False)):
        mid = run(add_mention(state, key, triage={"category": "legal_regulatory"},
                              status=MentionStatus.ESCALATED))
        ids[key] = run(state.create_escalation(Escalation(
            mention_id=mid, kind="legal", owner="Lee", sla_due_at=now + timedelta(minutes=due),
            breached=breached, notified_at=now if key != "sooner" else None)))
    run(state.ack_escalation(ids["acked"], "someone"))
    client, _ = client_for(VIEWER)

    rows = client.get("/api/urgent").json()["items"]

    assert [r["id"] for r in rows] == [ids["late"], ids["sooner"], ids["later"]]
    first = rows[0]
    assert first["breached"] is True
    assert first["permalink"].startswith("https://www.reddit.com/")
    assert {"platform", "category", "kind", "owner", "sla_due_at", "notified", "mention_id"} <= set(first)
    assert rows[1]["notified"] is False


def test_urgent_treats_past_due_as_breached_before_the_sweep_flags_it(state):
    now = _utcnow()
    a = run(add_mention(state, "a", status=MentionStatus.ESCALATED))
    b = run(add_mention(state, "b", status=MentionStatus.ESCALATED))
    soon = run(state.create_escalation(Escalation(mention_id=a, kind="privacy",
                                                  sla_due_at=now + timedelta(minutes=1))))
    overdue = run(state.create_escalation(Escalation(mention_id=b, kind="privacy",
                                                     sla_due_at=now - timedelta(minutes=1))))
    client, _ = client_for(VIEWER)

    rows = client.get("/api/urgent").json()["items"]

    assert [r["id"] for r in rows] == [overdue, soon]
    assert rows[0]["breached"] is True


# --- Feed ---------------------------------------------------------------------------------


@pytest.fixture
def feed(state):
    run(add_mention(state, "f1", text="Semaglutide made me SICK", status=MentionStatus.TRIAGED,
                    triage={"category": "complaint", "drug": "semaglutide", "urgency": "high"}))
    run(add_mention(state, "f2", text="Hims shipping is slow", platform=Platform.TRUSTPILOT,
                    status=MentionStatus.TRIAGED,
                    triage={"category": "complaint", "competitor": "Hims & Hers"}))
    run(add_mention(state, "f3", text="Loving WellPeps tirzepatide", status=MentionStatus.TRIAGED,
                    triage={"category": "praise", "product": "Compounded Tirzepatide",
                            "drug": "tirzepatide", "subject_type": "wellpeps"}))
    run(add_mention(state, "f4", text="x" * 2500))


def _feed(client, **params):
    resp = client.get("/api/mentions", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.parametrize("params,expected", [
    ({}, ["f4", "f3", "f2", "f1"]),
    ({"status": "new"}, ["f4"]),
    ({"platform": "trustpilot"}, ["f2"]),
    ({"competitor": "Hims & Hers"}, ["f2"]),
    ({"product": "Compounded Tirzepatide"}, ["f3"]),
    ({"drug": "semaglutide"}, ["f1"]),
    ({"category": "complaint"}, ["f2", "f1"]),
    ({"urgency": "high"}, ["f1"]),
    ({"q": "sick"}, ["f1"]),
    ({"q": "SHIPPING"}, ["f2"]),
])
def test_feed_filters(feed, params, expected):
    client, _ = client_for(VIEWER)
    body = _feed(client, **params)
    assert [r["external_id"] for r in body["items"]] == expected
    assert body["total"] == len(expected)


def test_feed_paginates(feed):
    client, _ = client_for(VIEWER)
    body = _feed(client, limit=2, offset=1)
    assert [r["external_id"] for r in body["items"]] == ["f3", "f2"]
    assert body["total"] == 4 and body["limit"] == 2 and body["offset"] == 1


def test_feed_truncates_text_and_carries_triage_tags(feed):
    client, _ = client_for(VIEWER)
    rows = {r["external_id"]: r for r in _feed(client)["items"]}
    assert len(rows["f4"]["text"]) == 2000 and rows["f4"]["text_truncated"] is True
    assert rows["f3"]["text_truncated"] is False
    assert rows["f3"]["drug"] == "tirzepatide" and rows["f3"]["product"] == "Compounded Tirzepatide"
    assert rows["f3"]["category"] == "praise"


@pytest.mark.parametrize("params", [{"status": "bogus"}, {"platform": "myspace"},
                                    {"category": "nope"}, {"urgency": "meh"}])
def test_feed_rejects_unknown_enum_values(feed, params):
    client, _ = client_for(VIEWER)
    assert client.get("/api/mentions", params=params).status_code == 400


# --- Detail ---------------------------------------------------------------------------------


def test_detail_has_everything_for_the_review_desk(state):
    mid = run(add_review_item(state))
    client, csrf = client_for(REVIEWER)
    post(client, csrf, f"/api/mentions/{mid}/edit", {"text": CLEAN_REPLY + " Thanks.", "claim_ids": CLEAN_IDS})

    body = client.get(f"/api/mentions/{mid}").json()

    assert body["mention"]["id"] == mid and body["mention"]["url"].startswith("https://")
    assert body["triage"]["category"] == "question"
    assert body["latest_draft"]["version"] == 2
    assert [d["version"] for d in body["drafts"]] == [2, 1]
    assert body["escalations"] == []
    assert [e["event"] for e in body["audit"]][-1] == "edited"
    assert set(body["claims"]) == set(CLEAN_IDS)
    assert body["claims"][CLEAN_IDS[0]]["text"]
    assert body["approval"]["allowed"] is False  # seed claims are PENDING


def test_detail_404(state):
    client, _ = client_for(VIEWER)
    assert client.get("/api/mentions/999").status_code == 404


# --- Edit -----------------------------------------------------------------------------------


def test_edit_creates_a_new_version_by_the_human(state, reviewer_client):
    mid = run(add_review_item(state))
    client, csrf = reviewer_client

    resp = post(client, csrf, f"/api/mentions/{mid}/edit", {"text": RED_REPLY, "claim_ids": CLEAN_IDS})

    assert resp.status_code == 200
    draft = run(state.get_latest_draft(mid))
    assert draft.version == 2 and draft.text == RED_REPLY
    assert draft.tier == "red" and draft.filter_ok is False
    assert draft.model == f"human:{REVIEWER}"
    assert run(state.get_mention(mid)).status is MentionStatus.IN_REVIEW
    edited = _events(state, mid)[-1]
    assert edited.event is AuditEventType.EDITED
    assert edited.actor == REVIEWER
    assert edited.final_text == RED_REPLY and edited.claim_ids == CLEAN_IDS
    assert edited.filter_result["tier"] == "red"


def test_edit_does_not_rerun_the_reviewer_by_default(tmp_path, monkeypatch):
    fake = FakeReviewer()
    state, _ = setup_app(tmp_path, monkeypatch, reviewer=fake)
    try:
        mid = run(add_review_item(state))
        client, csrf = client_for(REVIEWER)
        post(client, csrf, f"/api/mentions/{mid}/edit", {"text": CLEAN_REPLY, "claim_ids": CLEAN_IDS})
        assert fake.calls == []
        assert run(state.get_latest_draft(mid)).review_verdict is ReviewVerdict.NEEDS_HUMAN
    finally:
        teardown_app()


def test_edit_reruns_the_reviewer_when_configured(tmp_path, monkeypatch):
    fake = FakeReviewer(ReviewVerdict.REJECT)
    cfg = PulseConfig(review=ReviewConfig(rerun_reviewer_on_edit=True))
    state, _ = setup_app(tmp_path, monkeypatch, config=cfg, reviewer=fake)
    try:
        mid = run(add_review_item(state))
        client, csrf = client_for(REVIEWER)
        post(client, csrf, f"/api/mentions/{mid}/edit", {"text": CLEAN_REPLY, "claim_ids": CLEAN_IDS})
        assert fake.calls == [CLEAN_REPLY]
        draft = run(state.get_latest_draft(mid))
        assert draft.review_verdict is ReviewVerdict.REJECT
        assert draft.review_reasons == ["R1: fake"]
    finally:
        teardown_app()


@pytest.mark.parametrize("body", [{"text": "   ", "claim_ids": []}, {"text": "ok", "claim_ids": ["x"] * 50}])
def test_edit_rejects_bad_input(state, reviewer_client, body):
    mid = run(add_review_item(state))
    client, csrf = reviewer_client
    assert post(client, csrf, f"/api/mentions/{mid}/edit", body).status_code in (400, 422)


def test_edit_only_while_in_review(state, reviewer_client):
    mid = run(add_mention(state, "new1"))
    client, csrf = reviewer_client
    resp = post(client, csrf, f"/api/mentions/{mid}/edit", {"text": CLEAN_REPLY, "claim_ids": CLEAN_IDS})
    assert resp.status_code == 409


# --- Approve ----------------------------------------------------------------------------------


def test_approve_blocked_when_filter_is_red(state, reviewer_client):
    mid = run(add_review_item(state, text=RED_REPLY, tier="red"))
    client, csrf = reviewer_client

    resp = post(client, csrf, f"/api/mentions/{mid}/approve")

    assert resp.status_code == 409
    assert "red" in resp.json()["detail"]
    assert run(state.get_mention(mid)).status is MentionStatus.IN_REVIEW


def test_approve_blocked_without_claims(state, reviewer_client):
    mid = run(add_review_item(state, claim_ids=[]))
    client, csrf = reviewer_client

    resp = post(client, csrf, f"/api/mentions/{mid}/approve")

    assert resp.status_code == 409
    assert "claim" in resp.json()["detail"].lower()


def test_approve_blocked_by_pending_claims_lists_them(state, reviewer_client):
    mid = run(add_review_item(state))
    client, csrf = reviewer_client

    resp = post(client, csrf, f"/api/mentions/{mid}/approve")

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "not approved for publishing" in detail
    for cid in CLEAN_IDS:
        assert cid in detail
    assert not any(e.event is AuditEventType.APPROVED for e in _events(state, mid))


def test_approve_allowed_with_pending_claims_when_config_says_so(tmp_path, monkeypatch):
    cfg = PulseConfig(review=ReviewConfig(require_publishable_claims=False))
    state, _ = setup_app(tmp_path, monkeypatch, config=cfg)
    try:
        mid = run(add_review_item(state))
        client, csrf = client_for(REVIEWER)
        assert post(client, csrf, f"/api/mentions/{mid}/approve").status_code == 200
    finally:
        teardown_app()


@pytest.fixture
def signed_claims(tmp_path, monkeypatch):
    directory = approved_claims_dir(tmp_path, CLEAN_IDS)
    monkeypatch.setenv("PULSE_CONFIG_DIR", str(directory))
    knowledge.reload()
    yield
    monkeypatch.delenv("PULSE_CONFIG_DIR")
    knowledge.reload()


def test_approve_succeeds_with_publishable_claims(state, signed_claims, reviewer_client):
    mid = run(add_review_item(state))
    client, csrf = reviewer_client

    resp = post(client, csrf, f"/api/mentions/{mid}/approve")

    assert resp.status_code == 200, resp.text
    assert run(state.get_mention(mid)).status is MentionStatus.APPROVED
    approved = _events(state, mid)[-1]
    assert approved.event is AuditEventType.APPROVED
    assert approved.actor == REVIEWER
    assert approved.final_text == CLEAN_REPLY
    assert approved.claim_ids == CLEAN_IDS
    assert approved.permalink.startswith("https://www.reddit.com/")
    assert approved.draft_id == run(state.get_latest_draft(mid)).id


def test_approve_refuses_a_stale_draft(state, signed_claims, reviewer_client):
    mid = run(add_review_item(state))
    client, csrf = reviewer_client
    stale = run(state.get_latest_draft(mid)).id
    post(client, csrf, f"/api/mentions/{mid}/edit", {"text": CLEAN_REPLY + " Thanks.", "claim_ids": CLEAN_IDS})

    resp = post(client, csrf, f"/api/mentions/{mid}/approve", {"draft_id": stale})

    assert resp.status_code == 409


def test_approve_only_from_in_review(state, reviewer_client):
    mid = run(add_mention(state, "tri", status=MentionStatus.TRIAGED))
    client, csrf = reviewer_client
    assert post(client, csrf, f"/api/mentions/{mid}/approve").status_code == 409


# --- Reject / copied / mark-posted ------------------------------------------------------------


def test_reject_records_reason(state, reviewer_client):
    mid = run(add_review_item(state))
    client, csrf = reviewer_client

    assert post(client, csrf, f"/api/mentions/{mid}/reject", {"reason": ""}).status_code == 400
    resp = post(client, csrf, f"/api/mentions/{mid}/reject", {"reason": "not our place to reply"})

    assert resp.status_code == 200
    assert run(state.get_mention(mid)).status is MentionStatus.REJECTED
    event = _events(state, mid)[-1]
    assert event.event is AuditEventType.REJECTED and event.actor == REVIEWER
    assert event.verdict["reason"] == "not our place to reply"


def _approved_item(state, client, csrf):
    mid = run(add_review_item(state))
    assert post(client, csrf, f"/api/mentions/{mid}/approve").status_code == 200
    return mid


def test_copied_is_audited_and_does_not_post(state, signed_claims, reviewer_client):
    client, csrf = reviewer_client
    mid = _approved_item(state, client, csrf)

    assert post(client, csrf, f"/api/mentions/{mid}/copied").status_code == 200

    assert run(state.get_mention(mid)).status is MentionStatus.APPROVED
    event = _events(state, mid)[-1]
    assert event.event is AuditEventType.COPIED and event.actor == REVIEWER
    assert event.final_text == CLEAN_REPLY


def test_copy_refused_before_approval(state, reviewer_client):
    mid = run(add_review_item(state))
    client, csrf = reviewer_client
    assert post(client, csrf, f"/api/mentions/{mid}/copied").status_code == 409


def test_mark_posted_only_from_approved(state, signed_claims, reviewer_client):
    client, csrf = reviewer_client
    pending = run(add_review_item(state, "other"))
    assert post(client, csrf, f"/api/mentions/{pending}/mark-posted").status_code == 409

    mid = _approved_item(state, client, csrf)
    assert post(client, csrf, f"/api/mentions/{mid}/mark-posted",
                {"posted_url": "javascript:alert(1)"}).status_code == 400
    resp = post(client, csrf, f"/api/mentions/{mid}/mark-posted",
                {"posted_url": "https://www.reddit.com/r/test/comments/rev1/reply1/"})

    assert resp.status_code == 200
    assert run(state.get_mention(mid)).status is MentionStatus.POSTED
    event = _events(state, mid)[-1]
    assert event.event is AuditEventType.POSTED and event.actor == REVIEWER
    assert event.verdict["posted_url"].endswith("/reply1/")
    assert event.verdict["manual"] is True


# --- Manual escalation & ack -------------------------------------------------------------------


def test_manual_escalation_from_the_review_desk(env, reviewer_client):
    state, notifier = env
    mid = run(add_review_item(state))
    client, csrf = reviewer_client

    resp = post(client, csrf, f"/api/mentions/{mid}/escalate", {"kind": "adverse_event"})

    assert resp.status_code == 200, resp.text
    assert run(state.get_mention(mid)).status is MentionStatus.ESCALATED
    escalation = run(state.get_open_escalation(mid))
    assert escalation.kind == "adverse_event" and escalation.notified_at is not None
    assert len(notifier.sent) == 1
    assert "Is WellPeps legit" not in notifier.sent[0]  # link + category only
    assert any(e.event is AuditEventType.ESCALATED and e.actor == REVIEWER for e in _events(state, mid))


def test_manual_viral_negative_stays_triaged(env, reviewer_client):
    state, _ = env
    mid = run(add_mention(state, "viral", status=MentionStatus.TRIAGED, triage={"category": "complaint"}))
    client, csrf = reviewer_client

    assert post(client, csrf, f"/api/mentions/{mid}/escalate", {"kind": "viral_negative"}).status_code == 200
    assert run(state.get_mention(mid)).status is MentionStatus.TRIAGED
    assert run(state.get_open_escalation(mid)).kind == "viral_negative"


def test_manual_escalation_validates_kind_and_status(env, reviewer_client):
    state, _ = env
    client, csrf = reviewer_client
    mid = run(add_review_item(state))
    assert post(client, csrf, f"/api/mentions/{mid}/escalate", {"kind": "vibes"}).status_code == 400
    fresh = run(add_mention(state, "fresh"))
    assert post(client, csrf, f"/api/mentions/{fresh}/escalate", {"kind": "legal"}).status_code == 409


def _escalation(state, kind):
    mid = run(add_mention(state, f"esc-{kind}", status=MentionStatus.ESCALATED))
    return run(state.create_escalation(Escalation(mention_id=mid, kind=kind,
                                                  sla_due_at=_utcnow() + timedelta(minutes=15))))


@pytest.mark.parametrize("email,kind,expected", [
    (VIEWER, "legal", 403),
    (REVIEWER, "legal", 200),
    (REVIEWER, "adverse_event", 403),
    (CLINICAL, "adverse_event", 200),
    (CLINICAL, "privacy", 403),
    (CLINICAL, "legal", 403),
    (ADMIN, "adverse_event", 200),
])
def test_ack_role_rules(state, email, kind, expected):
    esc = _escalation(state, kind)
    client, csrf = client_for(email)

    resp = post(client, csrf, f"/api/escalations/{esc}/ack")

    assert resp.status_code == expected
    acked = run(state.get_escalation(esc))
    assert (acked.acked_by == email) is (expected == 200)


def test_ack_twice_and_missing(state):
    esc = _escalation(state, "legal")
    client, csrf = client_for(REVIEWER)
    assert post(client, csrf, f"/api/escalations/{esc}/ack").status_code == 200
    assert post(client, csrf, f"/api/escalations/{esc}/ack").status_code == 409
    assert post(client, csrf, "/api/escalations/999/ack").status_code == 404


def test_claims_endpoint_lists_publishability(state):
    client, _ = client_for(VIEWER)
    claims = {c["id"]: c for c in client.get("/api/claims").json()}
    assert claims[CLEAN_IDS[0]]["publishable"] is False
    assert claims[CLEAN_IDS[0]]["text"]


def test_no_route_posts_on_its_own(state):
    """Hard rule 3: nothing in the API publishes to a platform."""
    import harvey.dashboard as dashboard

    paths = {getattr(r, "path", "") for r in dashboard.app.routes}
    assert not any("publish" in p for p in paths)
    with sqlite3.connect(state.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM mentions WHERE status = 'posted'").fetchone()[0] == 0
