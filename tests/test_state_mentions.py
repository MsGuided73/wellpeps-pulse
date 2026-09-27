"""State layer for WellPeps Pulse: mentions, triage, drafts, audit, escalations."""

import sqlite3
from datetime import datetime, timedelta

import aiosqlite
import pytest
import pytest_asyncio
from pydantic import ValidationError

from harvey.models import (
    AuditEvent,
    AuditEventType,
    Category,
    Draft,
    Escalation,
    Mention,
    MentionStatus,
    Platform,
    ReviewVerdict,
    Triage,
    Urgency,
)
from harvey.state import StateManager


@pytest_asyncio.fixture
async def state(tmp_path):
    sm = StateManager(str(tmp_path / "pulse.db"))
    await sm.init_db()
    yield sm


def _mention(**overrides) -> Mention:
    fields = {
        "platform": Platform.REDDIT,
        "external_id": "t3_abc",
        "url": "https://www.reddit.com/r/Semaglutide/comments/abc/",
        "author_handle": "u/someone",
        "text": "Has anyone tried WellPeps for semaglutide?",
    }
    fields.update(overrides)
    return Mention(**fields)


async def _new_mention_id(state, **overrides) -> int:
    mention_id, created = await state.upsert_mention(_mention(**overrides))
    assert created
    return mention_id


# ── upsert / dedupe ──


@pytest.mark.asyncio
async def test_upsert_new_mention_is_created(state):
    mention_id, created = await state.upsert_mention(_mention())

    assert created is True
    assert isinstance(mention_id, int)


@pytest.mark.asyncio
async def test_upsert_dedupes_by_platform_and_external_id(state):
    first_id, _ = await state.upsert_mention(_mention())

    second_id, created = await state.upsert_mention(
        _mention(url="https://old.reddit.com/r/Semaglutide/comments/abc/")
    )

    assert created is False
    assert second_id == first_id


@pytest.mark.asyncio
async def test_same_external_id_on_another_platform_is_distinct(state):
    await state.upsert_mention(_mention())

    _, created = await state.upsert_mention(
        _mention(platform=Platform.X, url="https://x.com/someone/status/1")
    )

    assert created is True


@pytest.mark.asyncio
async def test_upsert_dedupes_by_normalized_url(state):
    first_id, _ = await state.upsert_mention(
        _mention(external_id="", url="https://example.com/review/42")
    )

    second_id, created = await state.upsert_mention(
        _mention(external_id="", url="HTTPS://Example.com/review/42/?utm_source=feed#top")
    )

    assert created is False
    assert second_id == first_id


@pytest.mark.asyncio
async def test_blank_external_ids_do_not_collide(state):
    _, first = await state.upsert_mention(_mention(external_id="", url="https://a.example/1"))
    _, second = await state.upsert_mention(_mention(external_id="", url="https://a.example/2"))

    assert first is True
    assert second is True


def test_mention_requires_a_permalink():
    with pytest.raises(ValidationError):
        _mention(url="   ")


@pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://x.example/1", "www.reddit.com/r/x"])
def test_mention_permalink_must_be_http(url):
    with pytest.raises(ValidationError):
        _mention(url=url)


# ── read ──


@pytest.mark.asyncio
async def test_get_mention_roundtrips_fields(state):
    posted = datetime(2026, 9, 1, 12, 30)
    mention_id = await _new_mention_id(
        state, posted_at=posted, engagement={"score": 12, "comments": 3}
    )

    mention = await state.get_mention(mention_id)

    assert mention.id == mention_id
    assert mention.platform == Platform.REDDIT
    assert mention.url_norm == "https://www.reddit.com/r/Semaglutide/comments/abc"
    assert mention.posted_at == posted
    assert mention.engagement == {"score": 12, "comments": 3}
    assert mention.status == MentionStatus.NEW


@pytest.mark.asyncio
async def test_get_missing_mention_returns_none(state):
    assert await state.get_mention(999) is None


@pytest.mark.asyncio
async def test_list_mentions_newest_first_with_filters(state):
    a = await _new_mention_id(state, external_id="a", url="https://r.example/a")
    b = await _new_mention_id(state, external_id="b", url="https://r.example/b")
    c = await _new_mention_id(
        state, platform=Platform.TRUSTPILOT, external_id="c", url="https://t.example/c"
    )
    await state.set_mention_status(a, MentionStatus.TRIAGED)

    everything = await state.list_mentions()
    triaged = await state.list_mentions(status=MentionStatus.TRIAGED)
    reddit = await state.list_mentions(platform=Platform.REDDIT)
    limited = await state.list_mentions(limit=1)

    assert [m.id for m in everything] == [c, b, a]
    assert [m.id for m in triaged] == [a]
    assert [m.id for m in reddit] == [b, a]
    assert [m.id for m in limited] == [c]


# ── status transitions ──


@pytest.mark.asyncio
async def test_happy_path_transitions_are_allowed(state):
    mention_id = await _new_mention_id(state)

    for status in ("triaged", "drafted", "in_review", "approved", "posted"):
        await state.set_mention_status(mention_id, status)

    assert (await state.get_mention(mention_id)).status == MentionStatus.POSTED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        ["escalated", "triaged"],
        ["escalated", "dropped"],
        ["triaged", "drafted", "in_review", "drafted"],
        ["triaged", "drafted", "in_review", "rejected"],
        ["dropped"],
    ],
)
async def test_other_allowed_transitions(state, path):
    mention_id = await _new_mention_id(state)

    for status in path:
        await state.set_mention_status(mention_id, status)

    assert (await state.get_mention(mention_id)).status == path[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path, bad",
    [
        ([], "posted"),
        ([], "approved"),
        ([], "new"),
        (["triaged"], "approved"),
        (["triaged", "drafted"], "approved"),
        (["dropped"], "triaged"),
        (["triaged", "drafted", "in_review", "approved", "posted"], "drafted"),
    ],
)
async def test_disallowed_transitions_raise(state, path, bad):
    mention_id = await _new_mention_id(state)
    for status in path:
        await state.set_mention_status(mention_id, status)

    with pytest.raises(ValueError):
        await state.set_mention_status(mention_id, bad)


@pytest.mark.asyncio
async def test_unknown_status_and_missing_mention_raise(state):
    mention_id = await _new_mention_id(state)

    with pytest.raises(ValueError):
        await state.set_mention_status(mention_id, "published")
    with pytest.raises(ValueError):
        await state.set_mention_status(12345, "triaged")


# ── triage ──


@pytest.mark.asyncio
async def test_save_and_get_triage(state):
    mention_id = await _new_mention_id(state)
    triage = Triage(
        mention_id=mention_id,
        relevant=True,
        subject_type="brand",
        subject="WellPeps",
        category=Category.PURCHASE_INTENT,
        sentiment="neutral",
        urgency=Urgency.NORMAL,
        reply_appropriate=True,
        phrases=["tried wellpeps"],
        model="haiku",
    )

    await state.save_triage(triage)
    stored = await state.get_triage(mention_id)

    assert stored.category == Category.PURCHASE_INTENT
    assert stored.phrases == ["tried wellpeps"]
    assert stored.reply_appropriate is True


@pytest.mark.asyncio
async def test_save_triage_replaces_previous(state):
    mention_id = await _new_mention_id(state)
    await state.save_triage(Triage(mention_id=mention_id, category=Category.QUESTION))

    await state.save_triage(
        Triage(mention_id=mention_id, category=Category.ADVERSE_EVENT, urgency=Urgency.URGENT)
    )

    stored = await state.get_triage(mention_id)
    assert stored.category == Category.ADVERSE_EVENT
    assert stored.urgency == Urgency.URGENT
    assert await state.get_triage(mention_id + 1) is None


# ── drafts ──


@pytest.mark.asyncio
async def test_draft_versions_increment_per_mention(state):
    m1 = await _new_mention_id(state, external_id="1", url="https://r.example/1")
    m2 = await _new_mention_id(state, external_id="2", url="https://r.example/2")

    await state.add_draft(Draft(mention_id=m1, text="first"))
    second_id = await state.add_draft(
        Draft(mention_id=m1, text="second", claim_ids=["C-001"], review_verdict=ReviewVerdict.PASS)
    )
    await state.add_draft(Draft(mention_id=m2, text="other"))

    latest = await state.get_latest_draft(m1)
    other = await state.get_latest_draft(m2)

    assert latest.id == second_id
    assert latest.version == 2
    assert latest.text == "second"
    assert latest.claim_ids == ["C-001"]
    assert latest.review_verdict == ReviewVerdict.PASS
    assert other.version == 1
    assert await state.get_latest_draft(9999) is None


# ── audit log ──


@pytest.mark.asyncio
async def test_append_and_list_audit(state):
    mention_id = await _new_mention_id(state)

    first = await state.append_audit(
        AuditEvent(mention_id=mention_id, event=AuditEventType.COLLECTED, actor="system")
    )
    second = await state.append_audit(
        AuditEvent(
            mention_id=mention_id,
            event=AuditEventType.APPROVED,
            actor="reviewer@wellpeps.com",
            claim_ids=["C-001"],
            final_text="Thanks for asking.",
            permalink="https://www.reddit.com/r/x/comments/abc",
        )
    )

    events = await state.list_audit(mention_id)

    assert second > first
    assert [e.event for e in events] == [AuditEventType.COLLECTED, AuditEventType.APPROVED]
    assert events[1].claim_ids == ["C-001"]


@pytest.mark.asyncio
async def test_audit_log_rejects_update(state):
    mention_id = await _new_mention_id(state)
    event_id = await state.append_audit(
        AuditEvent(mention_id=mention_id, event=AuditEventType.COLLECTED, actor="system")
    )

    async with aiosqlite.connect(state.db_path) as db:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            await db.execute("UPDATE audit_log SET actor = 'mallory' WHERE id = ?", (event_id,))


@pytest.mark.asyncio
async def test_audit_log_rejects_delete(state):
    mention_id = await _new_mention_id(state)
    await state.append_audit(
        AuditEvent(mention_id=mention_id, event=AuditEventType.COLLECTED, actor="system")
    )

    async with aiosqlite.connect(state.db_path) as db:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            await db.execute("DELETE FROM audit_log")


# ── escalations ──


@pytest.mark.asyncio
async def test_escalation_create_list_ack(state):
    m1 = await _new_mention_id(state, external_id="1", url="https://r.example/1")
    m2 = await _new_mention_id(state, external_id="2", url="https://r.example/2")
    due = datetime(2026, 9, 27, 12, 15)

    later = await state.create_escalation(
        Escalation(mention_id=m1, kind="adverse_event", owner="clinical",
                   sla_due_at=due + timedelta(minutes=10))
    )
    sooner = await state.create_escalation(
        Escalation(mention_id=m2, kind="legal_regulatory", owner="clinical", sla_due_at=due)
    )

    open_before = await state.list_open_escalations()
    acked = await state.ack_escalation(sooner, acked_by="nurse@wellpeps.com")
    acked_again = await state.ack_escalation(sooner, acked_by="someone-else")
    open_after = await state.list_open_escalations()

    assert [e.id for e in open_before] == [sooner, later]
    assert acked is True
    assert acked_again is False
    assert [e.id for e in open_after] == [later]


# ── kept usage ledger ──


@pytest.mark.asyncio
async def test_usage_events_still_recorded(state):
    ok = await state.record_usage_event(
        agent="triager", task="triage_batch", session_id="s-1",
        model="claude-haiku-4-5", input_tokens=10, output_tokens=20,
    )

    totals = await state.usage_totals()
    by_agent = await state.usage_by_agent(days=1)

    assert ok is True
    assert totals["today"]["calls"] == 1
    assert totals["today"]["output_tokens"] == 20
    assert by_agent[0]["agent"] == "triager"


# ── summary ──


@pytest.mark.asyncio
async def test_state_summary_counts_mentions_and_open_escalations(state):
    m1 = await _new_mention_id(state, external_id="1", url="https://r.example/1")
    await _new_mention_id(state, external_id="2", url="https://r.example/2")
    await state.set_mention_status(m1, MentionStatus.ESCALATED)
    await state.create_escalation(Escalation(mention_id=m1, kind="adverse_event"))

    summary = await state.get_state_summary()

    assert summary["mentions"]["new"] == 1
    assert summary["mentions"]["escalated"] == 1
    assert summary["mentions"]["posted"] == 0
    assert set(summary["mentions"]) == {s.value for s in MentionStatus}
    assert summary["open_escalations"] == 1
    assert summary["usage_today"] == 0


# ── Phase 4 additions ──


@pytest.mark.asyncio
async def test_triage_sentiment_score_round_trips(state):
    mention_id = await _new_mention_id(state)

    await state.save_triage(Triage(mention_id=mention_id, sentiment="negative", sentiment_score=-0.8))
    stored = await state.get_triage(mention_id)

    assert stored.sentiment == "negative"
    assert stored.sentiment_score == pytest.approx(-0.8)


@pytest.mark.asyncio
async def test_list_mentions_oldest_first(state):
    base = datetime(2026, 9, 27, 12, 0)
    late = await _new_mention_id(state, external_id="late", url="https://r.example/late", collected_at=base)
    early = await _new_mention_id(
        state, external_id="early", url="https://r.example/early", collected_at=base - timedelta(hours=1)
    )

    oldest = await state.list_mentions(status=MentionStatus.NEW, oldest_first=True)

    assert [m.id for m in oldest] == [early, late]
