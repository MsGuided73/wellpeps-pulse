"""Community rules as advisory, and recording what a rep actually posted (R-30).

Owner decision 2026-10-21: listening and drafting go ahead whatever a
community's rules say. Where the rules are unknown or prohibit brand replies,
the review desk says so and the rep decides: approving (to copy Pulse's reply)
needs an explicit acknowledgement, and the rep may write their own reply. Every
post a rep makes is recorded with the text actually sent, who sent it and when.

The mode is ``posting_holds:`` in config/communities.yaml, overridable with
PULSE_COMMUNITY_RULES. tests/conftest.py pins "enforced" so the rest of the
suite keeps testing the firm block; these tests opt into "advisory".
No network, no Claude.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from harvey import communities, community_override, engagement, knowledge, posted, protocol, review
from harvey.config import PulseConfig
from harvey.drafting import draft_batch
from harvey.models import AuditEventType, Category, Draft, Mention, MentionStatus, Platform, ReviewVerdict, Triage
from harvey.state import StateManager
from tests.community_helpers import allow_community
from tests.dashboard_helpers import (
    CLEAN_IDS,
    REVIEWER,
    VIEWER,
    approved_claims_dir,
    client_for,
    post,
    run,
    setup_app,
    teardown_app,
)
from tests.test_engagement_drafting import StubDrafter, StubReviewer, config_copy, _community  # noqa: F401

REDDIT = "https://www.reddit.com/r/{sub}/comments/{key}/"
SWITCHING = dict(category="question", competitor="Ro", intents=frozenset({"alternatives_requested"}),
                 unmet_need="provider_access", need_clarity=2, useful_contribution=2)


@pytest.fixture
def advisory(monkeypatch):
    monkeypatch.setenv("PULSE_COMMUNITY_RULES", "advisory")


@pytest_asyncio.fixture
async def state(tmp_path):
    sm = StateManager(str(tmp_path / "pulse.db"))
    await sm.init_db()
    yield sm


def _m(sub="test", key="k1", **kw) -> Mention:
    return Mention(id=kw.pop("id", 1), platform=Platform.REDDIT, url=REDDIT.format(sub=sub, key=key),
                   text=kw.pop("text", "post"), **kw)


def _t(**kw) -> Triage:
    base = {"mention_id": 1, "relevant": True, "reply_appropriate": True, "category": Category.QUESTION,
            "subtype": "general_education", "subject_type": "category"}
    base.update(kw)
    return Triage(**base)


def _prohibit(monkeypatch, cid="reddit:r/nope"):
    from datetime import date

    from harvey.models.knowledge import CommunityEntry

    original = knowledge.communities
    platform, name = cid.split(":", 1)
    entry = CommunityEntry(id=cid, platform=platform, name=name, brand_participation="prohibited",
                           rules_checked_at=date(2026, 10, 1))
    monkeypatch.setattr(knowledge, "communities", lambda: (*(c for c in original() if c.id != cid), entry))


# --- The mode -------------------------------------------------------------------------------------------


def test_the_suite_runs_enforced_and_the_env_switches_to_advisory(monkeypatch):
    assert communities.rules_advisory() is False
    monkeypatch.setenv("PULSE_COMMUNITY_RULES", "advisory")
    assert communities.rules_advisory() is True
    monkeypatch.setenv("PULSE_COMMUNITY_RULES", "enforce")       # a typo fails closed, whatever the YAML says
    assert communities.rules_advisory() is False
    monkeypatch.setenv("PULSE_COMMUNITY_RULES", "")              # unset: the YAML decides
    assert communities.rules_advisory() is (knowledge.community_posting_holds() == "advisory")


def test_the_shipped_config_is_advisory():
    # Owner decision 2026-10-21 (R-30); set back to "enforced" to restore the firm block.
    assert knowledge.community_posting_holds() == "advisory"


def test_drafting_status_hides_a_prohibition_only_in_advisory_mode(monkeypatch, advisory):
    _prohibit(monkeypatch)
    status = communities.status_for_url(REDDIT.format(sub="nope", key="a"))
    assert status.participation == "prohibited"
    assert communities.drafting_status(status).participation == "unknown"
    monkeypatch.setenv("PULSE_COMMUNITY_RULES", "enforced")
    assert communities.drafting_status(status).participation == "prohibited"


# --- Protocol, selection and gates ----------------------------------------------------------------------


def test_protocol_no_longer_holds_for_community_rules_in_advisory_mode():
    for participation in ("unknown", "prohibited"):
        held = protocol.decide(protocol.ProtocolInput(**SWITCHING, participation=participation))
        assert held.decision in (protocol.HOLD, protocol.DO_NOT_ENGAGE)
        advisory = protocol.decide(protocol.ProtocolInput(**SWITCHING, participation="unknown",
                                                          rules_advisory=True))
        assert advisory.decision not in protocol.NO_DRAFT_DECISIONS
        assert any("advisory" in r for r in advisory.rationale)


def test_advisory_mode_still_routes_safety_first():
    inp = protocol.ProtocolInput(category="question", competitor="Ro", participation="unknown",
                                 rules_advisory=True,
                                 intents=frozenset({"possible_serious_harm", "alternatives_requested"}))
    record = protocol.decide(inp)
    assert record.route == "adverse_event" and record.decision not in (protocol.APPROPRIATE_ALTERNATIVE,)


def test_selection_responds_in_unknown_and_competitor_communities(advisory):
    triage = _t()
    unknown = engagement.selection(triage, _m())
    assert unknown.decision == engagement.RESPOND
    assert unknown.posting_hold                       # the warning is still shown
    competitor = engagement.selection(triage, _m(sub="Noom"))
    assert competitor.decision == engagement.RESPOND
    assert "Noom" in competitor.posting_hold


def test_selection_keeps_the_firm_block_when_enforced():
    assert engagement.selection(_t(), _m()).decision == engagement.HOLD
    assert engagement.selection(_t(), _m(sub="Noom")).reason_code == "COMPETITOR_COMMUNITY"


def test_competitor_community_gate_is_not_recorded_in_advisory_mode(advisory):
    triage = _t(category=Category.PURCHASE_INTENT, subject_type="competitor", competitor="Noom Med",
                intents=["alternatives_requested"])
    assert engagement.review_gate(triage, _m(sub="Noom")) == ""


def test_community_warning_names_the_problem(monkeypatch, advisory):
    _prohibit(monkeypatch)
    assert "unverified" in community_override.warning(_m())
    assert "prohibits" in community_override.warning(_m(sub="nope"))
    assert "Noom" in community_override.warning(_m(sub="Noom"))
    allow_community(monkeypatch)
    assert community_override.warning(_m()) == ""


def test_no_warning_when_enforced():
    assert community_override.warning(_m()) == ""


# --- Drafting -------------------------------------------------------------------------------------------


async def _triaged(state, sub, key="p1", **triage) -> int:
    mid, _ = await state.upsert_mention(Mention(platform=Platform.REDDIT, external_id=key,
                                                url=REDDIT.format(sub=sub, key=key), text="a question"))
    await state.save_triage(Triage(mention_id=mid, relevant=True, reply_appropriate=True,
                                   **{"category": Category.QUESTION, "subtype": "general_education",
                                      "subject_type": "wellpeps", **triage}))
    await state.set_mention_status(mid, MentionStatus.TRIAGED)
    return mid


@pytest.mark.asyncio
async def test_a_prohibited_community_is_drafted_in_advisory_mode(state, config_copy, advisory):
    config_copy("communities.yaml", lambda d: d["communities"].append(
        _community("reddit:r/nobrands", brand_participation="prohibited", rules_checked_at="2026-10-01")))
    mid = await _triaged(state, "NoBrands")
    reply = ("I work with WellPeps. In general, telehealth programs use licensed healthcare providers to "
             "review a person's health information and determine whether treatment is appropriate.")
    drafter = StubDrafter(reply, ["CLM-AMG-04-WORK-WITH", "CLM-AMG-17-TELEHEALTH"])

    report = await draft_batch(state, drafter, StubReviewer())

    assert report.skipped == 0 and len(drafter.calls) == 1
    assert drafter.calls[0]["guidance"].allow_link is False          # treated like unverified rules
    draft = await state.get_latest_draft(mid)
    assert draft is not None and draft.tier != "red"
    assert not any("prohibited" in h for h in draft.filter_hits)


@pytest.mark.asyncio
async def test_reapply_releases_posts_held_for_community_rules(state, monkeypatch):
    mid = await _triaged(state, "SomeNewSub", subject_type="competitor", competitor="Ro",
                         intents=["alternatives_requested"], unmet_need="provider_access",
                         need_clarity=2, useful_contribution=2)
    mention = await state.get_mention(mid)
    await state.save_triage(protocol.applied(await state.get_triage(mid), mention))
    assert (await state.get_triage(mid)).protocol_decision == protocol.HOLD
    assert mid not in [m.id for m in await state.list_draftable_mentions()]

    monkeypatch.setenv("PULSE_COMMUNITY_RULES", "advisory")
    changed = await community_override.reapply(state)

    assert changed == 1
    assert (await state.get_triage(mid)).protocol_decision not in protocol.NO_DRAFT_DECISIONS
    assert mid in [m.id for m in await state.list_draftable_mentions()]
    assert await community_override.reapply(state) == 0              # idempotent


@pytest.mark.asyncio
async def test_reapply_does_nothing_when_enforced(state):
    await _triaged(state, "SomeNewSub", subject_type="competitor", competitor="Ro",
                   intents=["alternatives_requested"])
    assert await community_override.reapply(state) == 0


# --- Approval with acknowledgement ----------------------------------------------------------------------


async def _review_item(state, sub="test", key="r1") -> int:
    claims = knowledge.claims_by_id()
    text = f"{claims['CLM-AMG-04-WORK-WITH'].text} {claims['CLM-AMG-26-GENERAL-INFO'].text}"
    mid, _ = await state.upsert_mention(Mention(platform=Platform.REDDIT, url=REDDIT.format(sub=sub, key=key),
                                                external_id=key, text="What should I ask a telehealth provider?"))
    await state.save_triage(Triage(mention_id=mid, relevant=True, category=Category.QUESTION,
                                   subtype="general_education", subject_type="category", reply_appropriate=True))
    for status in (MentionStatus.TRIAGED, MentionStatus.DRAFTED, MentionStatus.IN_REVIEW):
        await state.set_mention_status(mid, status)
    await state.add_draft(Draft(mention_id=mid, text=text, claim_ids=["CLM-AMG-04-WORK-WITH",
                                                                      "CLM-AMG-26-GENERAL-INFO"],
                                model="sonnet", filter_ok=True, tier="yellow", review_verdict=ReviewVerdict.PASS))
    return mid


@pytest.mark.asyncio
async def test_approval_in_an_unverified_community_needs_an_acknowledgement(state, advisory):
    mid = await _review_item(state)
    with pytest.raises(review.ReviewError) as err:
        await review.approve(state, mid, "reviewer@x", PulseConfig(), role="reviewer")
    assert err.value.status == 409 and "acknowledge" in err.value.detail

    detail = await review.detail(state, mid, PulseConfig(), role="reviewer")
    assert detail["approval"]["blockers"] == []                      # nothing else stands in the way
    assert "unverified" in detail["community_warning"]

    await review.approve(state, mid, "reviewer@x", PulseConfig(), role="reviewer", acknowledge_community=True)
    assert (await state.get_mention(mid)).status is MentionStatus.APPROVED
    approved = [e for e in await state.list_audit(mid) if e.event is AuditEventType.APPROVED][-1]
    assert approved.verdict["community_override"]["participation"] == "unknown"
    assert "unverified" in approved.verdict["community_override"]["warning"]


@pytest.mark.asyncio
async def test_a_prohibited_community_can_be_approved_with_acknowledgement(state, monkeypatch, advisory):
    _prohibit(monkeypatch)
    mid = await _review_item(state, sub="nope")
    await review.approve(state, mid, "reviewer@x", PulseConfig(), role="reviewer", acknowledge_community=True)
    assert (await state.get_mention(mid)).status is MentionStatus.APPROVED


@pytest.mark.asyncio
async def test_acknowledgement_never_lifts_the_block_when_enforced(state):
    mid = await _review_item(state)
    with pytest.raises(review.ReviewError) as err:
        await review.approve(state, mid, "reviewer@x", PulseConfig(), role="reviewer", acknowledge_community=True)
    assert engagement.POSTING_HOLD_LABEL in err.value.detail


@pytest.mark.asyncio
async def test_a_cleared_community_needs_no_acknowledgement(state, monkeypatch, advisory):
    allow_community(monkeypatch)
    mid = await _review_item(state)
    await review.approve(state, mid, "reviewer@x", PulseConfig(), role="reviewer")
    approved = [e for e in await state.list_audit(mid) if e.event is AuditEventType.APPROVED][-1]
    assert "community_override" not in approved.verdict


# --- Recording what was posted --------------------------------------------------------------------------


NOW = datetime(2026, 10, 21, 18, 0, 0)


@pytest.mark.asyncio
async def test_record_own_reply_from_review_marks_it_posted(state, advisory):
    mid = await _review_item(state)
    sent = "I work with WellPeps. A good first question for any provider is how follow-up works."
    when = NOW - timedelta(minutes=30)

    record = await posted.record_post(state, mid, text=sent, responder="Leah", posted_at=when.isoformat() + "Z",
                                      actor="dana@x", posted_url=REDDIT.format(sub="test", key="r1") + "c1/",
                                      now=NOW)

    assert (await state.get_mention(mid)).status is MentionStatus.POSTED
    event = [e for e in await state.list_audit(mid) if e.event is AuditEventType.POSTED][-1]
    assert event.final_text == sent and event.actor == "dana@x"
    assert event.claim_ids == []                                     # not the drafted text
    assert event.verdict["recorded"] is True and event.verdict["responder"] == "Leah"
    assert event.verdict["posted_at"] == when.isoformat()
    assert event.verdict["matches_draft"] is False
    assert event.verdict["community_permission"] == "unknown" and event.verdict["community_warning"]
    assert event.filter_result["tier"] in ("green", "yellow", "red")
    assert record["responder"] == "Leah" and record["tier"] == event.filter_result["tier"]


@pytest.mark.asyncio
async def test_own_words_are_checked_for_content_not_for_claim_ids(state):
    plain = await _review_item(state, key="p1")
    record = await posted.record_post(state, plain, text="I work with WellPeps. Happy to share what to ask.",
                                      responder="Dana", posted_at=NOW.isoformat(), actor="d@x", now=NOW)
    assert record["tier"] != "red"                                   # no claim IDs is not a problem here
    risky = await _review_item(state, key="p2")
    record = await posted.record_post(state, risky, text="I work with WellPeps. Results are guaranteed.",
                                      responder="Dana", posted_at=NOW.isoformat(), actor="d@x", now=NOW)
    assert record["tier"] == "red"
    event = [e for e in await state.list_audit(risky) if e.event is AuditEventType.POSTED][-1]
    assert any(h["rule_id"] == "R13" for h in event.filter_result["hits"])
    assert event.filter_result["claim_rule_counted"] is False


@pytest.mark.asyncio
async def test_recording_the_drafted_text_keeps_its_claims(state, advisory):
    mid = await _review_item(state)
    draft = await state.get_latest_draft(mid)
    await posted.record_post(state, mid, text=f"  {draft.text}\n", responder="Dana",
                             posted_at=NOW.isoformat(), actor="dana@x", now=NOW)
    event = [e for e in await state.list_audit(mid) if e.event is AuditEventType.POSTED][-1]
    assert event.verdict["matches_draft"] is True and event.claim_ids == draft.claim_ids


@pytest.mark.asyncio
async def test_record_from_triaged_and_rejected(state):
    triaged = await _triaged(state, "test", key="t1")
    await posted.record_post(state, triaged, text="I work with WellPeps. Hi.", responder="Dana",
                             posted_at=NOW.isoformat(), actor="dana@x", now=NOW)
    assert (await state.get_mention(triaged)).status is MentionStatus.POSTED

    rejected = await _review_item(state, key="r2")
    await review.reject(state, rejected, "wrote my own", "dana@x")
    await posted.record_post(state, rejected, text="I work with WellPeps. Hi.", responder="Dana",
                             posted_at=NOW.isoformat(), actor="dana@x", now=NOW)
    assert (await state.get_mention(rejected)).status is MentionStatus.POSTED


@pytest.mark.asyncio
async def test_an_escalated_post_is_recorded_without_closing_the_escalation(state):
    mid = await _triaged(state, "test", key="e1")
    await state.set_mention_status(mid, MentionStatus.ESCALATED)
    await posted.record_post(state, mid, text="I work with WellPeps. Please call 911.", responder="Dr. Y",
                             posted_at=NOW.isoformat(), actor="clin@x", now=NOW)
    assert (await state.get_mention(mid)).status is MentionStatus.ESCALATED
    assert [e for e in await state.list_audit(mid) if e.event is AuditEventType.POSTED]


@pytest.mark.asyncio
async def test_a_second_record_is_a_correction_not_an_overwrite(state):
    mid = await _review_item(state)
    for text in ("I work with WellPeps. First.", "I work with WellPeps. Corrected."):
        await posted.record_post(state, mid, text=text, responder="Dana", posted_at=NOW.isoformat(),
                                 actor="dana@x", now=NOW)
    records = await posted.records(state, mid)
    assert [r["text"] for r in records] == ["I work with WellPeps. Corrected.", "I work with WellPeps. First."]


@pytest.mark.asyncio
@pytest.mark.parametrize("kw, message", [
    (dict(text="  "), "text"),
    (dict(text="x" * 10001), "longer"),
    (dict(responder=""), "who"),
    (dict(responder="x" * 101), "who"),
    (dict(posted_at="yesterday"), "date"),
    (dict(posted_at=(NOW + timedelta(hours=1)).isoformat()), "future"),
    (dict(posted_at=(NOW - timedelta(days=400)).isoformat()), "year"),
    (dict(posted_url="javascript:alert(1)"), "http"),
])
async def test_record_post_validates_its_input(state, kw, message):
    mid = await _review_item(state)
    args = dict(text="I work with WellPeps. Hi.", responder="Dana", posted_at=NOW.isoformat(), actor="d@x",
                now=NOW)
    args.update(kw)
    with pytest.raises(review.ReviewError) as err:
        await posted.record_post(state, mid, **args)
    assert err.value.status == 400 and message in err.value.detail.lower()


@pytest.mark.asyncio
async def test_record_post_404_for_an_unknown_mention(state):
    with pytest.raises(review.ReviewError) as err:
        await posted.record_post(state, 999, text="x", responder="Dana", posted_at=NOW.isoformat(),
                                 actor="d@x", now=NOW)
    assert err.value.status == 404


def test_posted_at_with_an_offset_is_stored_as_utc():
    parsed = posted.parse_posted_at("2026-10-21T10:00:00-07:00", now=NOW)
    assert parsed == datetime(2026, 10, 21, 17, 0, 0)


# --- API ------------------------------------------------------------------------------------------------


@pytest.fixture
def api(tmp_path, monkeypatch):
    directory = approved_claims_dir(tmp_path, CLEAN_IDS)
    monkeypatch.setenv("PULSE_CONFIG_DIR", str(directory))
    knowledge.reload()
    state, _ = setup_app(tmp_path, monkeypatch)
    yield state
    teardown_app()
    monkeypatch.delenv("PULSE_CONFIG_DIR")
    knowledge.reload()


def test_record_post_api(api):
    from tests.dashboard_helpers import add_review_item

    mid = run(add_review_item(api))
    client, csrf = client_for(REVIEWER)
    body = {"text": "I work with WellPeps. Thanks for asking.", "responder": "Leah",
            "posted_at": datetime.now(timezone.utc).isoformat()}

    assert client.post(f"/api/mentions/{mid}/record-post", json=body).status_code == 403   # no CSRF
    resp = post(client, csrf, f"/api/mentions/{mid}/record-post", body)

    assert resp.status_code == 200, resp.text
    assert resp.json()["record"]["responder"] == "Leah"
    assert run(api.get_mention(mid)).status is MentionStatus.POSTED
    detail = client.get(f"/api/mentions/{mid}").json()
    assert detail["posted_records"][0]["text"] == body["text"]


def test_a_viewer_cannot_record_a_post(api):
    from tests.dashboard_helpers import add_review_item

    mid = run(add_review_item(api))
    client, csrf = client_for(VIEWER)
    resp = post(client, csrf, f"/api/mentions/{mid}/record-post",
                {"text": "x", "responder": "x", "posted_at": datetime.now(timezone.utc).isoformat()})
    assert resp.status_code == 403


def test_approve_api_passes_the_acknowledgement(api, monkeypatch):
    from tests.dashboard_helpers import add_review_item

    monkeypatch.setenv("PULSE_COMMUNITY_RULES", "advisory")
    mid = run(add_review_item(api))
    client, csrf = client_for(REVIEWER)
    assert post(client, csrf, f"/api/mentions/{mid}/approve").status_code == 409
    assert post(client, csrf, f"/api/mentions/{mid}/approve", {"acknowledge_community": True}).status_code == 200


# --- Review follow-ups ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reapply_applies_the_gates_the_competitor_gate_hid(state, monkeypatch):
    # A satisfied-provider update in a competitor's community: enforced, the competitor
    # gate is recorded first; advisory, the Selection Module's SKIP must still apply.
    mid = await _triaged(state, "Noom", category=Category.PRAISE, subject_type="competitor",
                         competitor="Noom Med", intents=["satisfied_provider"])
    mention = await state.get_mention(mid)
    await state.save_triage(engagement.with_review_gate(await state.get_triage(mid), mention))
    assert engagement.gated(await state.get_triage(mid)) == engagement.GATE_COMPETITOR_COMMUNITY

    monkeypatch.setenv("PULSE_COMMUNITY_RULES", "advisory")
    await community_override.reapply(state)

    triage = await state.get_triage(mid)
    assert engagement.gated(triage) == engagement.GATE_NOT_WORTH_IT
    assert engagement.GATE_COMPETITOR_COMMUNITY not in (triage.urgency_reason or "")
    assert mid not in [m.id for m in await state.list_draftable_mentions()]


@pytest.mark.asyncio
async def test_a_recorded_reply_counts_in_the_thread_history(state):
    mid = await _triaged(state, "test", key="th1")
    await posted.record_post(state, mid, text="I work with WellPeps. My own words.", responder="Dana",
                             posted_at=NOW.isoformat(), actor="d@x", now=NOW)
    records = await engagement.recent_replies(state)
    mine = [r for r in records if r.mention_id == mid]
    assert len(mine) == 1 and mine[0].text == "I work with WellPeps. My own words." and mine[0].approved
    assert mine[0].approved_at is not None
    assert mine[0].thread == communities.thread_key(REDDIT.format(sub="test", key="th1"))


@pytest.mark.asyncio
async def test_the_posted_text_wins_over_the_draft_in_the_history(state):
    mid = await _review_item(state, key="h2")
    await posted.record_post(state, mid, text="I work with WellPeps. Changed before posting.", responder="Dana",
                             posted_at=NOW.isoformat(), actor="d@x", now=NOW)
    mine = [r for r in await engagement.recent_replies(state) if r.mention_id == mid]
    assert mine[0].text == "I work with WellPeps. Changed before posting." and mine[0].claim_ids == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [MentionStatus.NEW, MentionStatus.DRAFTED])
async def test_recording_is_refused_while_a_post_is_being_processed(state, status):
    mid, _ = await state.upsert_mention(Mention(platform=Platform.REDDIT, external_id=f"x{status.value}",
                                                url=REDDIT.format(sub="test", key=f"x{status.value}"), text="q"))
    if status is MentionStatus.DRAFTED:
        await state.save_triage(Triage(mention_id=mid, relevant=True))
        await state.set_mention_status(mid, MentionStatus.TRIAGED)
        await state.set_mention_status(mid, MentionStatus.DRAFTED)
    with pytest.raises(review.ReviewError) as err:
        await posted.record_post(state, mid, text="x", responder="Dana", posted_at=NOW.isoformat(),
                                 actor="d@x", now=NOW)
    assert err.value.status == 409


@pytest.mark.asyncio
async def test_the_result_says_whether_the_status_changed(state):
    moved = await _review_item(state, key="s1")
    assert (await posted.record_post(state, moved, text="I work with WellPeps. Hi.", responder="D",
                                     posted_at=NOW.isoformat(), actor="d@x", now=NOW))["status"] == "posted"
    kept = await _triaged(state, "test", key="s2")
    await state.set_mention_status(kept, MentionStatus.ESCALATED)
    assert (await posted.record_post(state, kept, text="I work with WellPeps. Hi.", responder="D",
                                     posted_at=NOW.isoformat(), actor="d@x", now=NOW))["status"] == "escalated"


@pytest.mark.asyncio
async def test_a_rewrite_check_reads_rules_like_drafting(monkeypatch, advisory):
    from harvey import rewrite

    _prohibit(monkeypatch)
    assert rewrite._context(_m(sub="nope")).participation == "unknown"
