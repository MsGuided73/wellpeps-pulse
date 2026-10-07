"""Rules of engagement: triage subtype persistence (migration v8) and the
draftable-mention query built from config/engagement_guide.yaml."""

from datetime import datetime

import aiosqlite
import pytest

from harvey import engagement
from harvey.models import AuditEvent, Category, Mention, MentionStatus, Platform, Triage
from harvey.state import MIGRATIONS
from tests.pulse_helpers import fresh_state

T0 = datetime(2026, 10, 1, 12, 0)


async def _mention(state, key: str, *, url: str | None = None, **triage) -> int:
    mid, _ = await state.upsert_mention(Mention(
        platform=Platform.REDDIT, external_id=key, author_handle="poster",
        url=url or f"https://www.reddit.com/r/test/comments/{key}/", text=f"post {key}",
        posted_at=T0, collected_at=T0,
    ))
    await state.save_triage(Triage(mention_id=mid, relevant=True, **triage))
    await state.set_mention_status(mid, MentionStatus.TRIAGED)
    return mid


@pytest.mark.asyncio
async def test_migration_v8_adds_triage_subtype(tmp_path):
    state = await fresh_state(tmp_path)
    async with aiosqlite.connect(state.db_path) as db:
        cols = {r[1] for r in await (await db.execute("PRAGMA table_info(triage)")).fetchall()}
        (version,) = await (await db.execute("PRAGMA user_version")).fetchone()
    assert {"subtype", "protocol_decision", "protocol_route", "opportunity_score", "protocol_json"} <= cols
    assert version == len(MIGRATIONS) >= 8


@pytest.mark.asyncio
async def test_subtype_round_trips_and_unknown_values_are_blank(tmp_path):
    state = await fresh_state(tmp_path)
    mid = await _mention(state, "a", category=Category.QUESTION, subtype="dose_question")
    assert (await state.get_triage(mid)).subtype == "dose_question"
    assert Triage(mention_id=1, subtype="not-a-subtype").subtype == ""
    assert Triage(mention_id=1, subtype=" Media_Inquiry ").subtype == "media_inquiry"


@pytest.mark.asyncio
@pytest.mark.parametrize("triage, drafted", [
    # model draft: needs reply_appropriate
    ({"category": Category.QUESTION, "subtype": "general_education", "reply_appropriate": True}, True),
    ({"category": Category.QUESTION, "subtype": "general_education", "reply_appropriate": False}, False),
    # boundary replies are drafted whatever reply_appropriate says
    ({"category": Category.QUESTION, "subtype": "dose_question", "subject_type": "wellpeps"}, True),
    ({"category": Category.ADVERSE_EVENT, "subject_type": "wellpeps"}, True),
    ({"category": Category.OTHER, "subtype": "emergency"}, True),
    # third-party individual questions, legal, privacy, misinformation: no reply
    ({"category": Category.QUESTION, "subtype": "dose_question", "subject_type": "category"}, False),
    ({"category": Category.LEGAL_REGULATORY, "subject_type": "wellpeps"}, False),
    ({"category": Category.PRIVACY, "subject_type": "wellpeps"}, False),
    ({"category": Category.MISINFORMATION, "reply_appropriate": True}, False),
    # minors and failed safety screens: never, boundary or not
    ({"category": Category.ADVERSE_EVENT, "subject_type": "wellpeps",
      "urgency_reason": "safety_screen:minor: likely under 18"}, False),
    ({"category": Category.QUESTION, "subtype": "dose_question", "subject_type": "wellpeps",
      "urgency_reason": "triage_failed"}, False),
    # the competitor / switching protocol: its classification decides in scope
    ({"category": Category.QUESTION, "competitor": "Ro", "protocol_decision": "hold",
      "reply_appropriate": True}, False),
    ({"category": Category.QUESTION, "competitor": "Ro", "protocol_decision": "monitor_only",
      "reply_appropriate": True}, False),
    ({"category": Category.QUESTION, "competitor": "Ro", "protocol_decision": "do_not_engage",
      "reply_appropriate": True}, False),
    ({"category": Category.QUESTION, "subtype": "dose_question", "competitor": "Ro",
      "protocol_decision": "clinical_caution"}, True),
    ({"category": Category.QUESTION, "competitor": "Ro", "protocol_decision": "escalate",
      "protocol_route": "legal"}, False),
    ({"category": Category.QUESTION, "competitor": "Ro", "protocol_decision": "escalate",
      "protocol_route": "adverse_event"}, True),
    ({"category": Category.QUESTION, "competitor": "Ro", "protocol_decision": "appropriate_alternative",
      "reply_appropriate": True}, True),
    ({"category": Category.QUESTION, "competitor": "Ro", "protocol_decision": "educational_only",
      "reply_appropriate": False}, False),
])
async def test_draftable_query_agrees_with_drafts_reply(tmp_path, triage, drafted):
    state = await fresh_state(tmp_path)
    mid = await _mention(state, "x", **triage)
    listed = [m.id for m in await state.list_draftable_mentions(limit=10)]
    assert (mid in listed) is drafted
    assert engagement.drafts_reply(await state.get_triage(mid)) is drafted
    assert await state.count_draftable() == int(drafted)


@pytest.mark.asyncio
async def test_a_skipped_mention_is_not_drafted_again(tmp_path):
    state = await fresh_state(tmp_path)
    mid = await _mention(state, "s", category=Category.QUESTION, subtype="general_education",
                         reply_appropriate=True)
    await state.append_audit(AuditEvent(mention_id=mid, event="skipped", actor="drafter",
                                        verdict={"reason": "community prohibits brand participation"}))
    assert await state.list_draftable_mentions() == []


def test_postgres_0004_is_idempotent_and_bumps_to_8():
    import re

    from harvey.paths import PROJECT_ROOT

    sql = (PROJECT_ROOT / "db" / "postgres" / "0004_engagement_protocol.sql").read_text(encoding="utf-8")
    body = re.sub(r"--[^\n]*", "", sql).lower()
    for column in ("subtype text not null default ''", "protocol_decision text not null default ''",
                   "protocol_route text not null default ''", "opportunity_score integer",
                   "protocol_json text not null default '{}'"):
        assert f"alter table pulse.triage add column if not exists {column}" in body
    assert re.search(r"values\s*\(\s*8\s*,", body) and "on conflict (version) do nothing" in body
    assert len(MIGRATIONS) == 8


@pytest.mark.asyncio
async def test_protocol_fields_round_trip_without_post_text(tmp_path):
    state = await fresh_state(tmp_path)
    record = {"decision": "appropriate_alternative", "rationale": ["explicit request"], "risks": {}}
    mid = await _mention(state, "p", category=Category.QUESTION, competitor="Ro",
                         intents=["alternatives_requested", "venting_only"], unmet_need="provider_access",
                         need_clarity=2, useful_contribution=2, protocol_decision="appropriate_alternative",
                         opportunity_score=7, protocol=record)
    triage = await state.get_triage(mid)
    assert triage.intents == ["alternatives_requested", "venting_only"]
    assert (triage.unmet_need, triage.need_clarity, triage.useful_contribution) == ("provider_access", 2, 2)
    assert (triage.protocol_decision, triage.opportunity_score) == ("appropriate_alternative", 7)
    assert triage.protocol == record
    plain = await _mention(state, "q", category=Category.QUESTION)
    out = await state.get_triage(plain)
    assert (out.protocol_decision, out.protocol_route, out.opportunity_score, out.protocol) == ("", "", None, {})
