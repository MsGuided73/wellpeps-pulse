"""State layer against a real Postgres (the Supabase `pulse` schema).

Skipped unless PULSE_TEST_DATABASE_URL points at a DISPOSABLE database: every
test drops and recreates the `pulse` schema from db/postgres/*.sql. Never
point it at the live Supabase project.

    PULSE_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/pulse_test \
        .venv/Scripts/python -m pytest -q -m postgres
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from harvey import db as dbmod
from harvey import pulse_store, review
from harvey.auth import AuthStore
from harvey.models import (
    AuditEvent,
    AuditEventType,
    Draft,
    Escalation,
    Mention,
    MentionStatus,
    Platform,
    ReviewVerdict,
    Triage,
)
from harvey.state import MIGRATIONS, StateManager

TEST_URL = os.environ.get("PULSE_TEST_DATABASE_URL", "").strip()
PG_DIR = Path(__file__).resolve().parent.parent / "db" / "postgres"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not TEST_URL, reason="PULSE_TEST_DATABASE_URL not set"),
]


async def _reset_schema(url: str) -> None:
    import asyncpg

    live = dbmod.dotenv_database_url()
    if live and live == url:
        pytest.fail("PULSE_TEST_DATABASE_URL equals the live PULSE_DATABASE_URL; refusing to drop it")
    conn = await asyncpg.connect(url)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS pulse CASCADE")
        for path in sorted(PG_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            await conn.execute(path.read_text(encoding="utf-8"))
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def state():
    await _reset_schema(TEST_URL)
    sm = StateManager(database_url=TEST_URL)
    await sm.init_db()
    yield sm
    await sm.close()


async def _raw(url: str):
    import asyncpg

    conn = await asyncpg.connect(url)
    await conn.execute("SET search_path TO pulse")
    return conn


def _mention(key: str, **kw) -> Mention:
    return Mention(platform=kw.pop("platform", Platform.REDDIT), external_id=key,
                   url=kw.pop("url", f"https://www.reddit.com/r/t/comments/{key}/"),
                   text=kw.pop("text", f"text {key}"), **kw)


# ── Schema / init ──


@pytest.mark.asyncio
async def test_init_db_accepts_the_applied_schema(state):
    from harvey.db import postgres

    assert await postgres.schema_version(TEST_URL) == len(MIGRATIONS)
    await state.init_db()  # idempotent


@pytest.mark.asyncio
async def test_migration_file_is_idempotent(state):
    conn = await _raw(TEST_URL)
    try:
        for path in sorted(PG_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            await conn.execute(path.read_text(encoding="utf-8"))
    finally:
        await conn.close()
    await state.init_db()


@pytest.mark.asyncio
async def test_init_db_refuses_a_missing_schema():
    import asyncpg

    conn = await asyncpg.connect(TEST_URL)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS pulse CASCADE")
    finally:
        await conn.close()
    sm = StateManager(database_url=TEST_URL)
    try:
        with pytest.raises(RuntimeError, match="db/postgres/README.md"):
            await sm.init_db()
    finally:
        await sm.close()


# ── Mentions ──


@pytest.mark.asyncio
async def test_upsert_mention_dedupes_on_external_id(state):
    first, created = await state.upsert_mention(_mention("abc"))
    again, created_again = await state.upsert_mention(
        _mention("abc", url="https://www.reddit.com/r/t/comments/abc/other/"))
    assert created and not created_again
    assert again == first


@pytest.mark.asyncio
async def test_upsert_mention_dedupes_on_normalized_url(state):
    first, _ = await state.upsert_mention(_mention("", url="https://www.reddit.com/r/t/comments/x/"))
    again, created = await state.upsert_mention(
        _mention("", url="https://www.reddit.com/r/t/comments/x?utm_source=foo"))
    assert not created and again == first


@pytest.mark.asyncio
async def test_blank_external_ids_do_not_collide(state):
    a, _ = await state.upsert_mention(_mention("", url="https://example.com/a"))
    b, created = await state.upsert_mention(_mention("", url="https://example.com/b"))
    assert created and a != b


@pytest.mark.asyncio
async def test_mention_round_trip_keeps_types(state):
    posted = datetime(2026, 9, 20, 8, 30, 15, 250000)
    mid, _ = await state.upsert_mention(_mention("rt", posted_at=posted, owned_channel=True,
                                                 engagement={"score": 3}))
    got = await state.get_mention(mid)
    assert got.posted_at == posted
    assert got.owned_channel is True
    assert got.engagement == {"score": 3}
    assert got.status is MentionStatus.NEW


@pytest.mark.asyncio
async def test_status_transitions_are_enforced(state):
    mid, _ = await state.upsert_mention(_mention("s1"))
    await state.set_mention_status(mid, MentionStatus.TRIAGED)
    with pytest.raises(ValueError, match="not allowed"):
        await state.set_mention_status(mid, MentionStatus.POSTED)
    await state.set_mention_status(mid, MentionStatus.DRAFTED)
    assert (await state.get_mention(mid)).status is MentionStatus.DRAFTED
    with pytest.raises(ValueError, match="not found"):
        await state.set_mention_status(999999, MentionStatus.TRIAGED)


@pytest.mark.asyncio
async def test_triage_replace_and_draftable(state):
    mid, _ = await state.upsert_mention(_mention("t1"))
    await state.save_triage(Triage(mention_id=mid, reply_appropriate=False, sentiment_score=-0.5))
    await state.save_triage(Triage(mention_id=mid, reply_appropriate=True, drug="semaglutide",
                                   phrases=["so tired"]))
    await state.set_mention_status(mid, MentionStatus.TRIAGED)
    triage = await state.get_triage(mid)
    assert triage.reply_appropriate is True and triage.drug == "semaglutide"
    assert triage.sentiment_score == 0.0 and triage.phrases == ["so tired"]
    assert [m.id for m in await state.list_draftable_mentions()] == [mid]
    assert await state.count_draftable() == 1
    summary = await state.get_state_summary()
    assert summary["mentions"]["triaged"] == 1 and summary["draftable"] == 1


# ── Drafts and audit ──


@pytest.mark.asyncio
async def test_draft_versions_increment(state):
    mid, _ = await state.upsert_mention(_mention("d1"))
    first = await state.add_draft(Draft(mention_id=mid, text="one", filter_ok=True))
    second = await state.add_draft(Draft(mention_id=mid, text="two", filter_ok=None,
                                         review_verdict=ReviewVerdict.PASS, claim_ids=["C1"]))
    drafts = await state.list_drafts(mid)
    assert [d.version for d in drafts] == [2, 1]
    assert [d.id for d in drafts] == [second, first]
    assert drafts[1].filter_ok is True and drafts[0].filter_ok is None
    assert drafts[0].claim_ids == ["C1"]


@pytest.mark.asyncio
async def test_audit_log_is_append_only(state):
    import asyncpg

    mid, _ = await state.upsert_mention(_mention("a1"))
    audit_id = await state.append_audit(AuditEvent(mention_id=mid, event=AuditEventType.COLLECTED,
                                                   actor="system", verdict={"ok": True}))
    events = await state.list_audit(mid)
    assert [e.id for e in events] == [audit_id] and events[0].verdict == {"ok": True}

    conn = await _raw(TEST_URL)
    try:
        for sql in ("UPDATE audit_log SET actor = 'x'", "DELETE FROM audit_log",
                    "TRUNCATE audit_log", "TRUNCATE audit_log CASCADE"):
            with pytest.raises(asyncpg.PostgresError, match="append-only"):
                await conn.execute(sql)
    finally:
        await conn.close()
    assert len(await state.list_audit(mid)) == 1


# ── Escalations ──


@pytest.mark.asyncio
async def test_escalation_lifecycle(state):
    mid, _ = await state.upsert_mention(_mention("e1"))
    due = datetime(2026, 9, 27, 12, 15)
    esc_id = await state.create_escalation(Escalation(mention_id=mid, kind="adverse_event",
                                                      owner="clinical", sla_due_at=due))
    assert (await state.get_open_escalation(mid)).id == esc_id
    assert await state.mark_escalation_notified(esc_id, datetime(2026, 9, 27, 12))
    assert not await state.mark_escalation_notified(esc_id, datetime(2026, 9, 27, 12, 1))
    assert await state.mark_escalation_breached(esc_id)
    assert not await state.mark_escalation_breached(esc_id)
    esc = await state.get_escalation(esc_id)
    assert esc.breached is True and esc.sla_due_at == due
    assert [e.id for e in await state.list_open_escalations()] == [esc_id]
    assert await state.ack_escalation(esc_id, "dr@wellpeps.com")
    assert not await state.ack_escalation(esc_id, "dr@wellpeps.com")
    assert await state.list_open_escalations() == []
    assert (await state.get_state_summary())["open_escalations"] == 0


@pytest.mark.asyncio
async def test_urgent_queue_reads(state):
    mid, _ = await state.upsert_mention(_mention("u1"))
    await state.create_escalation(Escalation(mention_id=mid, kind="privacy",
                                             sla_due_at=datetime(2026, 9, 27, 12)))
    data = await review.urgent(state, now=datetime(2026, 9, 27, 13))
    assert data["items"][0]["breached"] is True


# ── Auth ──


@pytest.mark.asyncio
async def test_users_and_sessions(state):
    store = AuthStore(state)
    uid = await store.create_user("Ann@WellPeps.com", "correct horse battery", "admin", "Ann")
    with pytest.raises(ValueError, match="already exists"):
        await store.create_user("ann@wellpeps.com", "correct horse battery", "admin")
    assert await store.count_active_admins() == 1
    user = await store.authenticate("ann@wellpeps.com", "correct horse battery")
    assert user["id"] == uid and user["active"] is True
    assert (await store.get_user("ann@wellpeps.com"))["last_login_at"]
    assert await store.authenticate("ann@wellpeps.com", "wrong password!!") is None

    token, csrf = await store.create_session(uid)
    me = await store.session_user(token)
    assert me["email"] == "ann@wellpeps.com" and me["csrf"] == csrf
    later = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=48)
    assert await store.session_user(token, now=later) is None  # expired -> deleted
    assert await store.session_user(token) is None

    await store.create_session(uid)
    assert await store.disable_user("ann@wellpeps.com")
    assert await store.count_active_admins() == 0
    assert await store.purge_expired_sessions() == 0


# ── Settings, runs, usage ──


@pytest.mark.asyncio
async def test_settings_runs_and_usage(state):
    await state.set_setting("kill", "1")
    await state.set_setting("kill", "0")
    assert await state.get_setting("kill") == "0"
    assert await state.increment_setting("n", 2) == 2

    run_id = await state.start_run("ingest", params={"a": 1})
    await state.finish_run(run_id, records=3, cost_usd=0.5)
    assert (await state.get_runs())[0]["status"] == "completed"
    assert await state.sweep_stale_runs() == 0

    await state.increment_usage()
    await state.increment_usage()
    assert await state.get_usage_today() == 2

    assert await state.record_usage_event(agent="triage", request_key="k1", input_tokens=10,
                                          cost_usd=0.25)
    assert not await state.record_usage_event(agent="triage", request_key="k1")
    assert await state.record_usage_event(agent="", request_key="", output_tokens=5,
                                          created_at="2026-01-01T00:00:00")
    totals = await state.usage_totals()
    assert totals["today"]["calls"] == 1 and totals["today"]["input_tokens"] == 10
    by_agent = {r["agent"]: r for r in await state.usage_by_agent(days=30)}
    assert by_agent["triage"]["cost_usd"] == 0.25
    days = await state.usage_by_day(days=30)
    assert len(days) == 1 and isinstance(days[0]["day"], str) and len(days[0]["day"]) == 10


# ── Language bank and briefs ──


async def _triaged(state, key, **triage) -> dict:
    mid, _ = await state.upsert_mention(_mention(key, posted_at=datetime(2026, 9, 21, 10)))
    await state.save_triage(Triage(mention_id=mid, **triage))
    await state.set_mention_status(mid, MentionStatus.TRIAGED)
    return mid


@pytest.mark.asyncio
async def test_language_bank_is_idempotent_per_mention(state):
    mid = await _triaged(state, "lb1", drug="semaglutide", category="complaint")
    [row] = await pulse_store.unbanked_mentions(state)
    phrases = [("Food Noise", "food noise"), ("so tired", "so tired")]
    assert await pulse_store.bank_mention(state, row, phrases)
    assert not await pulse_store.bank_mention(state, row, phrases)
    assert await pulse_store.unbanked_mentions(state) == []

    mid2 = await _triaged(state, "lb2", drug="semaglutide")
    [row2] = await pulse_store.unbanked_mentions(state)
    assert row2["id"] == mid2
    await pulse_store.bank_mention(state, row2, [("food  noise", "food noise")])
    page = await pulse_store.search_language_bank(state, q="FOOD", drug="Semaglutide")
    assert page["total"] == 1
    item = page["items"][0]
    assert item["count"] == 2 and item["phrase"] == "Food Noise" and item["example_mention_id"] == mid
    assert item["first_seen"] == "2026-09-21T10:00:00"


@pytest.mark.asyncio
async def test_briefs_and_trend_terms(state):
    brief = {"period": "daily", "window_start": datetime(2026, 9, 26), "window_end": datetime(2026, 9, 27),
             "status": "ok", "headline": "h", "summary_md": "s", "action_cards": [{"title": "t"}],
             "watchlist": ["w"], "data": {"n": 1}, "model": "m", "created_at": datetime(2026, 9, 27, 7)}
    terms = [{"term": "food noise", "count": 3, "baseline_count": 1, "velocity": 2.5, "score": 1,
              "is_new": True, "example_ids": [1, 2]}]
    bid = await pulse_store.save_brief(state, brief, terms)
    assert await pulse_store.save_brief(state, {**brief, "headline": "h2"}, terms) == bid
    got = await pulse_store.get_brief_by_window(state, "daily", datetime(2026, 9, 26))
    assert got["headline"] == "h2" and got["action_cards"] == [{"title": "t"}]
    assert got["window_start"] == "2026-09-26T00:00:00"
    [term] = await pulse_store.list_trend_terms(state, bid)
    assert term["is_new"] is True and term["example_ids"] == [1, 2]
    await pulse_store.mark_brief_slack_sent(state, bid, datetime(2026, 9, 27, 8))
    assert (await pulse_store.list_briefs(state))[0]["slack_sent_at"] == "2026-09-27T08:00:00"


@pytest.mark.asyncio
async def test_feed_search_and_trend_rows(state):
    mid = await _triaged(state, "f1", category="complaint")
    page = await review.feed(state, q="TEXT F1")
    assert page["total"] == 1 and page["items"][0]["id"] == mid
    assert page["items"][0]["text_truncated"] is False
    rows = await pulse_store.fetch_rows(state, datetime(2026, 9, 20), datetime(2026, 9, 27))
    assert [r["id"] for r in rows] == [mid]
    assert await pulse_store.count_relevant(state, datetime(2026, 9, 20), datetime(2026, 9, 27)) == 1


@pytest.mark.asyncio
async def test_dashboard_summary_and_usage_on_postgres(state, monkeypatch):
    from harvey import dashboard

    class NoQuota:
        async def get_utilization(self):
            return None

    monkeypatch.setenv("PULSE_DATABASE_URL", TEST_URL)
    monkeypatch.setattr(dashboard, "_quota_client", NoQuota())
    await state.upsert_mention(_mention("q1"))
    await state.record_usage_event(agent="triage", input_tokens=3, cost_usd=0.123456)

    summary = await dashboard.get_summary(user={})
    assert summary["total"] == 1 and summary["mentions"]["new"] == 1
    usage = await dashboard.get_usage(user={})
    assert usage["totals"]["today"]["input_tokens"] == 3
    assert usage["totals"]["today"]["cost_usd"] == 0.1235
    assert [r["agent"] for r in usage["by_agent"]] == ["triage"]
    assert len(usage["by_day"]) == 1 and len(usage["by_day"][0]["day"]) == 10


@pytest.mark.asyncio
async def test_privileges_and_row_level_security(state):
    import asyncpg

    conn = await _raw(TEST_URL)
    try:
        await conn.execute("SET ROLE pulse_app")
        await conn.execute("INSERT INTO settings (key, value) VALUES ('k', 'v')")
        assert await conn.fetchval("SELECT value FROM settings WHERE key = 'k'") == "v"
        await conn.execute("INSERT INTO audit_log (event, actor) VALUES ('collected', 't')")
        for sql in ("UPDATE audit_log SET actor = 'x'", "DELETE FROM audit_log",
                    "TRUNCATE audit_log", "INSERT INTO schema_version (version) VALUES (99)"):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(sql)
        await conn.execute("RESET ROLE")
        for api_role in ("anon", "authenticated"):
            if await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", api_role):
                await conn.execute(f"SET ROLE {api_role}")
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await conn.fetch("SELECT * FROM pulse.mentions")
                await conn.execute("RESET ROLE")
    finally:
        await conn.close()
