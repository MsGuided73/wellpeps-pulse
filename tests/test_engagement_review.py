"""The review desk under the rules of engagement: what the detail view shows
(situation, community rules, 80/20 share, protocol score and reasons, clinical
approval, FINALIZE warnings, escalation handoff) and the approval gate
(clinical approver, FINALIZE, live protocol decision, community rules)."""

import pytest

from harvey import engagement, review
from harvey.config import PulseConfig
from harvey.models import Category, Draft, MentionStatus, ReviewVerdict
from tests.dashboard_helpers import (
    CLINICAL,
    REVIEWER,
    add_mention,
    client_for,
    post,
    run,
    setup_app,
    teardown_app,
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    state, _ = setup_app(tmp_path, monkeypatch)
    yield state
    teardown_app()


async def _with_draft(state, key, triage, text, claim_ids, model="approved-response") -> int:
    mid = await add_mention(state, key, triage=triage, status=MentionStatus.IN_REVIEW)
    await state.add_draft(Draft(mention_id=mid, text=text, claim_ids=claim_ids, model=model,
                                filter_ok=True, tier="yellow", review_verdict=ReviewVerdict.NEEDS_HUMAN))
    return mid


def _adverse(state):
    from harvey import knowledge

    claims = knowledge.claims_by_id()
    text = f"{claims['CLM-AMG-04-WORK-WITH'].text} {claims['CLM-AMG-10-REACTION'].text}"
    return run(_with_draft(state, "ae1", {"category": Category.ADVERSE_EVENT, "subject_type": "wellpeps"},
                           text, ["CLM-AMG-04-WORK-WITH", "CLM-AMG-10-REACTION"]))


def test_adverse_event_reply_needs_a_clinical_approver(env):
    mid = _adverse(env)
    with pytest.raises(review.ReviewError) as err:
        run(review.approve(env, mid, "reviewer@x", PulseConfig(), role="reviewer"))
    assert err.value.status == 403 and "clinical approval required" in str(err.value)
    with pytest.raises(review.ReviewError):
        run(review.approve(env, mid, "someone@x", PulseConfig()))          # unknown role = reviewer
    run(review.approve(env, mid, "clinical@x", PulseConfig(), role="clinical"))
    assert run(env.get_mention(mid)).status is MentionStatus.APPROVED
    approved = [e for e in run(env.list_audit(mid)) if e.event.value == "approved"][0]
    assert approved.verdict["clinical_approval"] is True and approved.verdict["role"] == "clinical"


def test_clinical_user_cannot_approve_a_routine_reply(env):
    mid = run(_with_draft(env, "r1", {"category": Category.QUESTION, "subject_type": "wellpeps"},
                          "I work with WellPeps. Thanks for asking.", ["CLM-AMG-04-WORK-WITH"], model="sonnet"))
    with pytest.raises(review.ReviewError) as err:
        run(review.approve(env, mid, "clinical@x", PulseConfig(), role="clinical"))
    assert err.value.status == 403


def test_dashboard_routes_the_clinical_gate_by_role(env):
    mid = _adverse(env)
    reviewer, csrf = client_for(REVIEWER)
    resp = post(reviewer, csrf, f"/api/mentions/{mid}/approve")
    assert resp.status_code == 403 and "clinical" in resp.json()["detail"]
    detail = reviewer.get(f"/api/mentions/{mid}").json()
    assert detail["approval"]["clinical_required"] is True
    assert any("clinical approval required" in b for b in detail["approval"]["blockers"])
    clinical, csrf = client_for(CLINICAL)
    assert clinical.get(f"/api/mentions/{mid}").json()["approval"]["allowed"] is True
    assert post(clinical, csrf, f"/api/mentions/{mid}/approve").status_code == 200


def test_finalize_claims_block_approval_and_are_listed(env):
    from harvey import knowledge

    claims = knowledge.claims_by_id()
    text = f"{claims['CLM-AMG-04-TEAM'].text} {claims['CLM-AMG-APPX-COMPLAINT'].text}"
    mid = run(_with_draft(env, "c1", {"category": Category.COMPLAINT, "subject_type": "wellpeps"},
                          text, ["CLM-AMG-04-TEAM", "CLM-AMG-APPX-COMPLAINT"]))
    detail = run(review.detail(env, mid, PulseConfig(), role="reviewer"))
    assert any(b.startswith("FINALIZE: CLM-AMG-APPX-COMPLAINT") for b in detail["approval"]["blockers"])
    assert detail["engagement"]["finalize"][0]["claim_id"] == "CLM-AMG-APPX-COMPLAINT"
    assert detail["engagement"]["situation"]["template"] == "C"


def test_protocol_record_is_shown_and_a_live_hold_blocks_approval(env):
    record = {"label": "APPROPRIATE ALTERNATIVE", "opportunity": {"alternative_intent": 2, "total": 7},
              "rationale": ["explicit request for alternatives"], "brand_mode": "brief_factual_option"}
    triage = {"category": Category.QUESTION, "competitor": "Ro", "subject_type": "competitor",
              "intents": ["alternatives_requested"], "unmet_need": "provider_access", "need_clarity": 2,
              "useful_contribution": 2, "protocol_decision": "appropriate_alternative",
              "opportunity_score": 7, "protocol": record, "reply_appropriate": True}
    mid = run(_with_draft(env, "p1", triage, "I work with WellPeps. Ask how follow-up works.",
                          ["CLM-AMG-04-WORK-WITH"], model="sonnet"))
    detail = run(review.detail(env, mid, PulseConfig(), role="reviewer"))
    proto = detail["engagement"]["protocol"]
    assert proto["score"] == 7 and proto["label"] == "APPROPRIATE ALTERNATIVE"
    assert proto["rationale"] == ["explicit request for alternatives"]
    # r/test has no verified rules: recomputed now, the protocol says HOLD.
    assert any("protocol says HOLD" in b for b in detail["approval"]["blockers"])
    assert detail["engagement"]["community"]["participation"] == "unknown"


def test_no_protocol_view_outside_its_scope(env):
    mid = run(_with_draft(env, "g1", {"category": Category.QUESTION, "subject_type": "wellpeps"},
                          "I work with WellPeps. Thanks.", ["CLM-AMG-04-WORK-WITH"], model="sonnet"))
    detail = run(review.detail(env, mid, PulseConfig()))
    assert detail["engagement"]["protocol"] is None
    assert detail["engagement"]["persona"]["persona"] == "official_account"


def test_handoff_is_never_claimed_without_a_page(env):
    assert review._handoff([], None) is None
    paged = review._handoff([{"kind": "adverse_event", "notified_at": "2026-10-07T10:00", "acked_at": None}], None)
    assert paged["paged"] is True and "waiting" in paged["status"]
    failed = review._handoff([{"kind": "adverse_event", "notified_at": None, "acked_at": None}], None)
    assert failed["paged"] is False and failed["status"].startswith("NOT handed off")


def test_requires_clinical_approval_follows_the_situation():
    from harvey.models import Triage

    assert engagement.requires_clinical_approval(Triage(mention_id=1, category=Category.QUESTION, competitor="Ro",
                                                        protocol_decision="escalate",
                                                        protocol_route="adverse_event"))
    assert not engagement.requires_clinical_approval(Triage(mention_id=1, category=Category.QUESTION,
                                                            competitor="Ro", protocol_decision="clinical_caution"))


def test_engagement_mix_endpoint_is_a_planning_view(env):
    from tests.dashboard_helpers import VIEWER

    client, _ = client_for(VIEWER)
    resp = client.get("/api/analytics/engagement-mix?days=30")
    assert resp.status_code == 200
    data = resp.json()
    assert {"education", "promotion", "promotional_share", "by_platform", "by_community", "limit"} <= set(data)
    assert client.get("/api/analytics/engagement-mix?days=0").status_code == 400


def test_review_desk_ui_renders_engagement_through_eschtml_only():
    """Strict CSP: no inline style or on* handlers in the new UI code, and the
    rules-of-engagement values go through escHtml / tag / toneBadge."""
    import re

    from harvey.paths import PROJECT_ROOT

    web = PROJECT_ROOT / "harvey" / "web"
    app = (web / "app.js").read_text(encoding="utf-8")
    body = app[app.index("function engagementHtml"):app.index("// The registry link the draft carries")]
    assert "style=" not in body and not re.search(r"\son[a-z]+=", body)
    assert "innerHTML" not in body
    for field in ("p.rationale", "f.missing", "e.handoff.status", "p.brand_limits"):
        assert field in body
    html = (web / "index.html").read_text(encoding="utf-8")
    card = html[html.index('id="mix-card"'):html.index("</section>", html.index('id="mix-card"'))]
    assert "style=" not in card and not re.search(r"\son[a-z]+=", card)
