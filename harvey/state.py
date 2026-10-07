"""State manager. All of WellPeps Pulse's memory lives here.

Backends: SQLite by default (``data/pulse.db`` / ``PULSE_DB_PATH``; the test
suite), or Postgres (the Supabase ``pulse`` schema) when ``PULSE_DATABASE_URL``
is set and no explicit path is given. SQL is written once in portable,
SQLite-flavoured form and translated by ``harvey.db.dialect``; see
db/postgres/README.md. On Postgres the schema comes from db/postgres/*.sql,
not from MIGRATIONS, and init_db only verifies ``pulse.schema_version``.

Concurrency (SQLite): the DB runs in WAL mode (set persistently at init) so the
dashboard can read while the heartbeat writes. Every connection gets a busy
timeout so concurrent writers wait instead of raising "database is locked".

Migrations: schema changes are applied via a linear, idempotent migration
list tracked with SQLite's ``PRAGMA user_version``. Pulse starts a fresh
database (data/pulse.db), so the list restarts at v1.

The audit_log table is append-only: SQLite triggers abort any UPDATE or
DELETE (Postgres triggers also block TRUNCATE), so the record of what was drafted, reviewed, approved and posted
cannot be rewritten after the fact.
"""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from harvey import db as dbmod
from harvey.db import postgres
from harvey.db.connection import SqliteConnection
from harvey.db.dialect import sql_utc
from harvey.models import (
    AuditEvent,
    Draft,
    Escalation,
    Mention,
    MentionStatus,
    Triage,
)
from harvey.paths import DB_PATH
from harvey.urls import normalize_url

# How long (seconds) a connection waits on a locked database before failing.
BUSY_TIMEOUT_SECONDS = 30.0

# Triage categories that go to a named owner (mirrors
# harvey.agents.triager.SEVERE_CATEGORIES; tested). Since the rules of
# engagement (docs/RULES-OF-ENGAGEMENT.md) some of them still get the guide's
# approved boundary reply drafted next to the escalation, so which mentions
# are drafted is decided by config/engagement_guide.yaml, not by this list.
NO_DRAFT_CATEGORIES = ("adverse_event", "legal_regulatory", "privacy", "billing_fraud")


def _protocol_blob(triage: Triage) -> dict:
    """What triage.protocol_json stores: the model's protocol inputs and the
    computed record (harvey/protocol.py). Never post text."""
    return {
        "inputs": {"intents": list(triage.intents), "unmet_need": triage.unmet_need,
                   "need_clarity": triage.need_clarity, "useful_contribution": triage.useful_contribution},
        "record": dict(triage.protocol or {}),
    }


def _draftable_where() -> tuple[str, tuple]:
    from harvey import engagement  # late: engagement imports compliance and knowledge

    return engagement.draftable_where()

# Allowed mention status transitions. Anything not listed raises ValueError.
# Terminal states (rejected, posted, dropped) have no outgoing edges.
#
# Escalation can happen after triage, not only from ``new``:
# - triaged -> escalated: re-escalation of an already-triaged mention, e.g. a
#   follow-up comment reveals an adverse event, or a viral negative (which
#   stays ``triaged``) turns out to be a legal threat.
# - drafted / in_review -> escalated: an editor escalates from the review
#   desk (Phase 7), or a late signal arrives while a draft is pending. The
#   draft is then moot; the escalation owner decides what happens next.
ALLOWED_TRANSITIONS: dict[MentionStatus, frozenset[MentionStatus]] = {
    MentionStatus.NEW: frozenset({
        MentionStatus.TRIAGED, MentionStatus.DROPPED, MentionStatus.ESCALATED,
    }),
    MentionStatus.TRIAGED: frozenset({
        MentionStatus.DRAFTED, MentionStatus.DROPPED, MentionStatus.ESCALATED,
    }),
    MentionStatus.DRAFTED: frozenset({MentionStatus.IN_REVIEW, MentionStatus.ESCALATED}),
    MentionStatus.IN_REVIEW: frozenset({
        MentionStatus.APPROVED, MentionStatus.REJECTED, MentionStatus.DRAFTED,
        MentionStatus.ESCALATED,
    }),
    # approved -> in_review: a human edits an approved, not-yet-posted reply;
    # the edit voids the approval and it must be approved again.
    MentionStatus.APPROVED: frozenset({MentionStatus.POSTED, MentionStatus.IN_REVIEW}),
    MentionStatus.ESCALATED: frozenset({MentionStatus.TRIAGED, MentionStatus.DROPPED}),
}


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _utcnow() -> datetime:
    """Naive UTC now (matches how timestamps are stored in the DB)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ts(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _ago(**delta) -> str:
    """A naive-UTC cutoff, formatted like SQLite's datetime('now', '-N unit')."""
    return sql_utc(_utcnow() - timedelta(**delta))


def _today_bounds() -> tuple[str, str]:
    """[start, end) of the current UTC day (what date('now') used to mean)."""
    start = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return sql_utc(start), sql_utc(start + timedelta(days=1))


def usage_windows() -> list[tuple[str, str, tuple]]:
    """(label, WHERE clause, params) for the today / 7-day / 30-day usage rollups (UTC)."""
    return [
        ("today", "created_at >= ? AND created_at < ?", _today_bounds()),
        ("week", "created_at >= ?", (_ago(days=7),)),
        ("month", "created_at >= ?", (_ago(days=30),)),
    ]


def _loads(raw, default):
    """Decode a *_json column, falling back to ``default`` on junk."""
    try:
        parsed = json.loads(raw) if raw else default
    except (json.JSONDecodeError, TypeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


# ── Schema migrations ─────────────────────────────────────────────────
# Each entry is an idempotent SQL script. The index into this list + 1 is
# the schema version stored in PRAGMA user_version. Never edit or reorder
# released migrations — append new ones.

MIGRATIONS: list[str] = [
    # ── v1: WellPeps Pulse base schema ──
    """
    -- ── Kept from Harvey: operational plumbing ──

    -- Key/value store for operational flags (kill switch, counters).
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT DEFAULT '',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- What ran, when, how much it produced and cost.
    CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY,
        stage TEXT DEFAULT '',
        status TEXT DEFAULT 'running',
        provider TEXT DEFAULT '',
        records INTEGER DEFAULT 0,
        cost_usd REAL DEFAULT 0.0,
        params_json TEXT DEFAULT '{}',
        error TEXT DEFAULT '',
        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        ended_at TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_runs_stage_started ON runs(stage, started_at);

    CREATE TABLE IF NOT EXISTS actions (
        id TEXT PRIMARY KEY,
        action_type TEXT,
        agent TEXT,
        details_json TEXT DEFAULT '{}',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_actions_created_at ON actions(created_at);

    -- Daily Claude call counter: the Brain's budget fallback when the
    -- subscription quota endpoint is unreadable.
    CREATE TABLE IF NOT EXISTS usage_log (
        id TEXT PRIMARY KEY,
        date TEXT UNIQUE,
        claude_calls INTEGER DEFAULT 0,
        usage_percent REAL DEFAULT 0.0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Per-call usage accounting (tokens, cost, attribution).
    CREATE TABLE IF NOT EXISTS usage_events (
        id TEXT PRIMARY KEY,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        agent TEXT DEFAULT '',
        task TEXT DEFAULT '',
        session_id TEXT DEFAULT '',
        request_key TEXT DEFAULT '',
        model TEXT DEFAULT '',
        input_tokens INTEGER DEFAULT 0,
        output_tokens INTEGER DEFAULT 0,
        cache_read_tokens INTEGER DEFAULT 0,
        cache_creation_tokens INTEGER DEFAULT 0,
        cost_usd REAL DEFAULT 0.0,
        duration_ms INTEGER DEFAULT 0,
        num_turns INTEGER DEFAULT 0,
        is_error INTEGER DEFAULT 0,
        source TEXT DEFAULT 'result_json'
    );
    CREATE INDEX IF NOT EXISTS idx_usage_events_created ON usage_events(created_at);
    CREATE INDEX IF NOT EXISTS idx_usage_events_agent ON usage_events(agent);
    CREATE INDEX IF NOT EXISTS idx_usage_events_session ON usage_events(session_id);
    CREATE UNIQUE INDEX IF NOT EXISTS uq_usage_events_request
        ON usage_events(request_key) WHERE request_key != '';

    -- ── Listening ──

    -- A configured feed (subreddit search, review page, owned account...).
    CREATE TABLE IF NOT EXISTS sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        collector TEXT NOT NULL,
        name TEXT DEFAULT '',
        config_json TEXT DEFAULT '{}',
        enabled INTEGER DEFAULT 1,
        owned INTEGER DEFAULT 0,
        last_cursor TEXT DEFAULT '',
        last_run_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- One public post / comment / review. The permalink is mandatory.
    CREATE TABLE IF NOT EXISTS mentions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER REFERENCES sources(id),
        platform TEXT NOT NULL,
        external_id TEXT DEFAULT '',
        url TEXT NOT NULL,
        url_norm TEXT NOT NULL,
        author_handle TEXT DEFAULT '',
        parent_external_id TEXT DEFAULT '',
        text TEXT DEFAULT '',
        title TEXT DEFAULT '',
        lang TEXT DEFAULT '',
        posted_at TIMESTAMP,
        collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        engagement_json TEXT DEFAULT '{}',
        owned_channel INTEGER DEFAULT 0,
        status TEXT DEFAULT 'new',
        run_id TEXT DEFAULT ''
    );
    CREATE UNIQUE INDEX IF NOT EXISTS uq_mentions_platform_external
        ON mentions(platform, external_id) WHERE external_id != '';
    CREATE UNIQUE INDEX IF NOT EXISTS uq_mentions_url_norm ON mentions(url_norm);
    CREATE INDEX IF NOT EXISTS idx_mentions_status ON mentions(status, collected_at);
    CREATE INDEX IF NOT EXISTS idx_mentions_collected ON mentions(collected_at);
    CREATE INDEX IF NOT EXISTS idx_mentions_platform ON mentions(platform, collected_at);

    CREATE TABLE IF NOT EXISTS triage (
        mention_id INTEGER PRIMARY KEY REFERENCES mentions(id),
        relevant INTEGER DEFAULT 1,
        subject_type TEXT DEFAULT '',
        subject TEXT DEFAULT '',
        competitor TEXT DEFAULT '',
        product TEXT DEFAULT '',
        category TEXT DEFAULT 'other',
        sentiment TEXT DEFAULT '',
        urgency TEXT DEFAULT 'normal',
        urgency_reason TEXT DEFAULT '',
        reply_appropriate INTEGER DEFAULT 0,
        phrases_json TEXT DEFAULT '[]',
        model TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_triage_category ON triage(category);
    CREATE INDEX IF NOT EXISTS idx_triage_urgency ON triage(urgency);

    CREATE TABLE IF NOT EXISTS escalations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mention_id INTEGER NOT NULL REFERENCES mentions(id),
        kind TEXT DEFAULT '',
        owner TEXT DEFAULT '',
        notified_at TIMESTAMP,
        sla_due_at TIMESTAMP,
        acked_at TIMESTAMP,
        acked_by TEXT DEFAULT '',
        breached INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_escalations_mention ON escalations(mention_id);
    CREATE INDEX IF NOT EXISTS idx_escalations_open ON escalations(acked_at, sla_due_at);

    -- ── Drafting & review ──

    CREATE TABLE IF NOT EXISTS drafts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mention_id INTEGER NOT NULL REFERENCES mentions(id),
        version INTEGER NOT NULL,
        text TEXT DEFAULT '',
        claim_ids_json TEXT DEFAULT '[]',
        model TEXT DEFAULT '',
        filter_ok INTEGER,
        filter_hits_json TEXT DEFAULT '[]',
        review_verdict TEXT,
        review_reasons_json TEXT DEFAULT '[]',
        tier TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(mention_id, version)
    );

    -- Append-only record of every decision. Triggers below forbid edits.
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mention_id INTEGER,
        draft_id INTEGER,
        event TEXT NOT NULL,
        actor TEXT NOT NULL DEFAULT '',
        claim_ids_json TEXT DEFAULT '[]',
        filter_result_json TEXT DEFAULT '{}',
        verdict_json TEXT DEFAULT '{}',
        final_text TEXT DEFAULT '',
        permalink TEXT DEFAULT '',
        at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_audit_mention ON audit_log(mention_id, id);
    CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_log(at);

    CREATE TRIGGER IF NOT EXISTS trg_audit_log_no_update
    BEFORE UPDATE ON audit_log
    BEGIN
        SELECT RAISE(ABORT, 'audit_log is append-only: UPDATE not allowed');
    END;

    CREATE TRIGGER IF NOT EXISTS trg_audit_log_no_delete
    BEFORE DELETE ON audit_log
    BEGIN
        SELECT RAISE(ABORT, 'audit_log is append-only: DELETE not allowed');
    END;

    -- ── Intelligence ──

    CREATE TABLE IF NOT EXISTS briefs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT DEFAULT 'daily',
        period_start TIMESTAMP,
        period_end TIMESTAMP,
        body_md TEXT DEFAULT '',
        data_json TEXT DEFAULT '{}',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_briefs_kind_created ON briefs(kind, created_at);

    CREATE TABLE IF NOT EXISTS trend_terms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        term TEXT NOT NULL,
        platform TEXT DEFAULT '',
        day TEXT NOT NULL,
        count INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(term, platform, day)
    );
    CREATE INDEX IF NOT EXISTS idx_trend_terms_day ON trend_terms(day);

    CREATE TABLE IF NOT EXISTS language_bank (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phrase TEXT NOT NULL UNIQUE,
        category TEXT DEFAULT '',
        example_mention_id INTEGER,
        count INTEGER DEFAULT 0,
        first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- ── Access ──

    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL UNIQUE,
        display_name TEXT DEFAULT '',
        role TEXT DEFAULT 'reviewer',
        password_hash TEXT DEFAULT '',
        active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP NOT NULL,
        last_seen_at TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
    """,
    # ── v2: numeric sentiment from the triage agent (Phase 4) ──
    # ALTER TABLE has no IF NOT EXISTS; user_version guarantees it runs once.
    """
    ALTER TABLE triage ADD COLUMN sentiment_score REAL DEFAULT 0.0;
    """,
    # ── v3: the drug discussed, separate from the WellPeps product (SKU) ──
    """
    ALTER TABLE triage ADD COLUMN drug TEXT DEFAULT '';
    CREATE INDEX IF NOT EXISTS idx_triage_drug ON triage(drug);
    """,
    # ── v4: dashboard auth (Phase 7) ──
    # sessions.id holds sha256(token), never the token. csrf_token is the
    # per-session double-submit value returned by /api/me.
    """
    ALTER TABLE sessions ADD COLUMN csrf_token TEXT DEFAULT '';
    ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP;
    """,
    # ── v5: Pulse trends, briefs and language bank (Phase 8) ──
    # The v1 briefs / trend_terms / language_bank placeholders were never
    # written by any code path, so they are empty everywhere: rebuild them in
    # the shape Phase 8 needs. language_bank_mentions records which mentions
    # were banked, so banking is idempotent per mention.
    """
    DROP TABLE IF EXISTS trend_terms;
    DROP TABLE IF EXISTS briefs;
    DROP TABLE IF EXISTS language_bank;

    CREATE TABLE briefs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        period TEXT NOT NULL,                 -- daily | weekly
        window_start TIMESTAMP NOT NULL,      -- naive UTC
        window_end TIMESTAMP NOT NULL,
        status TEXT DEFAULT 'ok',             -- ok | fallback
        headline TEXT DEFAULT '',
        summary_md TEXT DEFAULT '',
        action_cards_json TEXT DEFAULT '[]',
        watchlist_json TEXT DEFAULT '[]',
        data_json TEXT DEFAULT '{}',          -- the aggregates the brief was built from
        model TEXT DEFAULT '',
        slack_sent_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(period, window_start)
    );

    CREATE TABLE trend_terms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        brief_id INTEGER NOT NULL REFERENCES briefs(id),
        rank INTEGER NOT NULL,
        term TEXT NOT NULL,
        count INTEGER DEFAULT 0,
        baseline_count INTEGER DEFAULT 0,
        velocity REAL DEFAULT 0.0,
        score REAL DEFAULT 0.0,
        is_new INTEGER DEFAULT 0,
        example_ids_json TEXT DEFAULT '[]',
        UNIQUE(brief_id, term)
    );

    CREATE TABLE language_bank (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phrase TEXT NOT NULL,                 -- first verbatim form seen
        phrase_norm TEXT NOT NULL,            -- lowercased, whitespace collapsed
        scope TEXT NOT NULL DEFAULT '',       -- WellPeps product, else drug, else ''
        product TEXT DEFAULT '',
        drug TEXT DEFAULT '',
        category TEXT DEFAULT '',
        sentiment TEXT DEFAULT '',
        example_mention_id INTEGER,
        count INTEGER DEFAULT 0,
        first_seen TIMESTAMP,
        last_seen TIMESTAMP,
        UNIQUE(phrase_norm, scope)
    );
    CREATE INDEX idx_language_bank_count ON language_bank(count);
    CREATE INDEX idx_language_bank_last_seen ON language_bank(last_seen);

    CREATE TABLE language_bank_mentions (
        mention_id INTEGER PRIMARY KEY REFERENCES mentions(id),
        banked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """,
    # ── v6: password management ──
    # must_change_password: set by an admin reset and (by default) on users an
    # admin creates; while set, the dashboard allows only /api/me,
    # /api/me/password and /api/logout. password_changed_at is naive UTC.
    """
    ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN password_changed_at TIMESTAMP;
    """,
    # ── v7: tracked reply links ──
    # link_json: the registry link a draft carries (harvey/links.link_record:
    # id, label, url, utm_content, utm_term), or NULL. Reply analytics read it.
    """
    ALTER TABLE drafts ADD COLUMN link_json TEXT;
    """,
    # ── v8: rules of engagement and the competitor / switching protocol ──
    # subtype: the situation inside a category that decides how Pulse engages
    # (harvey.models.mention.TRIAGE_SUBTYPES; config/engagement_guide.yaml;
    # docs/RULES-OF-ENGAGEMENT.md), "" when none.
    # protocol_decision / protocol_route / opportunity_score / protocol_json:
    # the Competitor Mentions and Provider Switching Protocol's classification,
    # escalation route, opportunity score (NULL when gated) and structured
    # record (harvey/protocol.py; no post text). "" / NULL / '{}' outside the
    # protocol's scope.
    """
    ALTER TABLE triage ADD COLUMN subtype TEXT NOT NULL DEFAULT '';
    ALTER TABLE triage ADD COLUMN protocol_decision TEXT NOT NULL DEFAULT '';
    ALTER TABLE triage ADD COLUMN protocol_route TEXT NOT NULL DEFAULT '';
    ALTER TABLE triage ADD COLUMN opportunity_score INTEGER;
    ALTER TABLE triage ADD COLUMN protocol_json TEXT NOT NULL DEFAULT '{}';
    """,
]


class StateManager:
    """Pulse's state on SQLite (default) or Postgres.

    - ``StateManager(path)``: SQLite at ``path``, always (tests rely on this).
    - ``StateManager(database_url=url)``: Postgres.
    - ``StateManager()``: Postgres if PULSE_DATABASE_URL is set, else SQLite
      at DB_PATH. ``from_env(path)`` is the same with a SQLite path override.
      Both refuse the SQLite fallback when PULSE_REQUIRE_POSTGRES is on.
    """

    def __init__(self, db_path: str | None = None, database_url: str | None = None):
        if database_url is None and db_path is None:
            database_url = dbmod.env_database_url() or None
            if database_url is None:
                dbmod.check_sqlite_allowed()
        if database_url is not None and not dbmod.is_postgres_url(database_url):
            raise ValueError("database_url must be a postgres:// or postgresql:// URL")
        self.database_url = database_url.strip() if database_url else None
        self.backend = "postgres" if self.database_url else "sqlite"
        self.db_path = None if self.database_url else (db_path or str(DB_PATH))

    @classmethod
    def from_env(cls, db_path=None) -> "StateManager":
        """Postgres when PULSE_DATABASE_URL is set, else SQLite at ``db_path``."""
        url = dbmod.env_database_url()
        if url:
            return cls(database_url=url)
        dbmod.check_sqlite_allowed()
        return cls(str(db_path) if db_path else None)

    @property
    def location(self) -> str:
        """Where the data lives, safe to log (no password)."""
        return dbmod.redact_url(self.database_url) if self.database_url else str(self.db_path)

    @asynccontextmanager
    async def _sqlite_raw(self):
        """A raw aiosqlite connection with sane concurrency settings.

        `timeout` maps to SQLite's busy handler, so writers wait for locks
        (e.g. while the dashboard holds a read) instead of erroring.
        Foreign keys are enforced per connection.
        """
        db = await aiosqlite.connect(self.db_path, timeout=BUSY_TIMEOUT_SECONDS)
        try:
            await db.execute("PRAGMA foreign_keys=ON")
            db.row_factory = aiosqlite.Row
            yield db
        finally:
            await db.close()

    @asynccontextmanager
    async def _connect(self):
        """A backend-neutral connection (see harvey.db.connection)."""
        if self.database_url:
            async with postgres.connect(self.database_url) as db:
                yield db
        else:
            async with self._sqlite_raw() as raw:
                yield SqliteConnection(raw)

    def connect(self):
        """Public connection context for sibling modules (auth, review)."""
        return self._connect()

    async def close(self) -> None:
        """Release pooled Postgres connections (no-op on SQLite)."""
        if self.database_url:
            await postgres.close_pool(self.database_url)

    async def init_db(self):
        """Create/upgrade the schema. Safe to call on every startup.

        Postgres: never migrates; fails with a pointer to db/postgres/README.md
        unless pulse.schema_version >= len(MIGRATIONS).
        """
        if self.database_url:
            await postgres.verify_schema(self.database_url, len(MIGRATIONS), self.location)
            return
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        async with self._sqlite_raw() as db:
            # WAL is persistent in the DB file: readers (dashboard) never
            # block the writer (heartbeat) and vice versa.
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")

            async with db.execute("PRAGMA user_version") as cursor:
                (version,) = await cursor.fetchone()

            for target, script in enumerate(MIGRATIONS, start=1):
                if version < target:
                    await db.executescript(script)
                    await db.execute(f"PRAGMA user_version = {target}")
                    await db.commit()

    # ── Run log (what ran, when, what it produced and cost) ──

    async def start_run(
        self, stage: str, provider: str = "", params: dict | None = None
    ) -> str:
        run_id = _new_id()
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO runs (id, stage, provider, params_json, status) "
                "VALUES (?, ?, ?, ?, 'running')",
                (run_id, stage, provider, json.dumps(params or {})),
            )
            await db.commit()
        return run_id

    async def finish_run(
        self,
        run_id: str,
        status: str = "completed",
        records: int = 0,
        cost_usd: float = 0.0,
        error: str = "",
    ):
        async with self._connect() as db:
            await db.execute(
                "UPDATE runs SET status = ?, records = ?, cost_usd = ?, "
                "error = ?, ended_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, int(records), float(cost_usd), error[:500], run_id),
            )
            await db.commit()

    async def sweep_stale_runs(self, older_than_hours: int = 6) -> int:
        """A killed collector can't close its own run — never trust it to."""
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE runs SET status = 'stale', ended_at = CURRENT_TIMESTAMP "
                "WHERE status = 'running' AND started_at < ?",
                (_ago(hours=int(older_than_hours)),),
            )
            await db.commit()
            return cursor.rowcount

    async def get_runs(self, limit: int = 25) -> list[dict]:
        async with self._connect() as db:
            async with db.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (int(limit),)
            ) as cursor:
                return [dict(r) for r in await cursor.fetchall()]

    # ── Settings (operational flags: kill switch, counters) ──

    async def get_setting(self, key: str, default: str = "") -> str:
        async with self._connect() as db:
            async with db.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else default

    async def set_setting(self, key: str, value: str):
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO settings (key, value, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(key) DO UPDATE SET
                       value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
                (key, str(value)),
            )
            await db.commit()

    async def increment_setting(self, key: str, by: int = 1) -> int:
        current = await self.get_setting(key, "0")
        try:
            value = int(current) + by
        except ValueError:
            value = by
        await self.set_setting(key, str(value))
        return value

    # ── Actions Log ──

    async def log_action(self, action_type: str, agent: str, details: dict | None = None):
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO actions (id, action_type, agent, details_json) VALUES (?, ?, ?, ?)",
                (_new_id(), action_type, agent, json.dumps(details or {})),
            )
            await db.commit()

    # ── Sources (one row per collector feed) ──

    async def ensure_source(
        self, collector: str, name: str = "", config: dict | None = None,
        owned: bool = False,
    ) -> int:
        """Id of the source row for ``collector``, creating it on first use."""
        existing = await self.get_source_by_collector(collector)
        if existing:
            return existing["id"]
        async with self._connect() as db:
            cursor = await db.execute(
                "INSERT INTO sources (collector, name, config_json, owned) "
                "VALUES (?, ?, ?, ?) RETURNING id",
                (collector, name or collector, json.dumps(config or {}), bool(owned)),
            )
            (source_id,) = await cursor.fetchone()
            await db.commit()
            return source_id

    async def get_source_by_collector(self, collector: str) -> dict | None:
        async with self._connect() as db:
            async with db.execute(
                "SELECT * FROM sources WHERE collector = ? ORDER BY id LIMIT 1",
                (collector,),
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_sources(self) -> list[dict]:
        async with self._connect() as db:
            async with db.execute("SELECT * FROM sources ORDER BY id") as cursor:
                return [dict(r) for r in await cursor.fetchall()]

    async def touch_source(self, source_id: int, cursor_value: str | None = None):
        """Stamp last_run_at (and optionally the collector's cursor)."""
        async with self._connect() as db:
            if cursor_value is None:
                await db.execute(
                    "UPDATE sources SET last_run_at = ? WHERE id = ?",
                    (_ts(_utcnow()), int(source_id)),
                )
            else:
                await db.execute(
                    "UPDATE sources SET last_run_at = ?, last_cursor = ? WHERE id = ?",
                    (_ts(_utcnow()), cursor_value, int(source_id)),
                )
            await db.commit()

    # ── Mentions ──

    @staticmethod
    def _mention_from_row(row) -> Mention:
        d = dict(row)
        d["engagement"] = _loads(d.pop("engagement_json", None), {})
        d["owned_channel"] = bool(d.get("owned_channel"))
        return Mention(**d)

    async def upsert_mention(self, mention: Mention) -> tuple[int, bool]:
        """Insert a mention unless it is already known.

        Dedupes on (platform, external_id) when external_id is set, and on
        the normalized permalink always. Returns (id, created); on a
        duplicate, id is the existing row's and nothing is changed.
        """
        url_norm = normalize_url(mention.url)
        platform = mention.platform.value
        async with self._connect() as db:
            cursor = await db.execute(
                """INSERT OR IGNORE INTO mentions
                   (source_id, platform, external_id, url, url_norm,
                    author_handle, parent_external_id, text, title, lang,
                    posted_at, collected_at, engagement_json, owned_channel,
                    status, run_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   RETURNING id""",
                (
                    mention.source_id, platform, mention.external_id,
                    mention.url, url_norm, mention.author_handle,
                    mention.parent_external_id, mention.text, mention.title,
                    mention.lang, _ts(mention.posted_at),
                    _ts(mention.collected_at), json.dumps(mention.engagement),
                    bool(mention.owned_channel), mention.status.value,
                    mention.run_id,
                ),
            )
            inserted = await cursor.fetchone()
            await db.commit()
            if inserted is not None:
                return inserted[0], True

            async with db.execute(
                """SELECT id FROM mentions
                   WHERE (platform = ? AND external_id = ? AND external_id != '')
                      OR url_norm = ?
                   ORDER BY id LIMIT 1""",
                (platform, mention.external_id, url_norm),
            ) as cur:
                row = await cur.fetchone()
        if row is None:  # pragma: no cover - IGNORE implies a conflicting row
            raise RuntimeError("mention insert ignored but no conflicting row found")
        return row[0], False

    async def get_mention(self, mention_id: int) -> Mention | None:
        async with self._connect() as db:
            async with db.execute(
                "SELECT * FROM mentions WHERE id = ?", (int(mention_id),)
            ) as cursor:
                row = await cursor.fetchone()
        return self._mention_from_row(row) if row else None

    async def list_mentions(
        self,
        status: MentionStatus | str | None = None,
        limit: int = 100,
        platform: str | None = None,
        oldest_first: bool = False,
    ) -> list[Mention]:
        """Mentions newest first (oldest first when asked), optionally
        filtered by status/platform."""
        where, params = [], []
        if status:
            where.append("status = ?")
            params.append(MentionStatus(status).value)
        if platform:
            where.append("platform = ?")
            params.append(str(getattr(platform, "value", platform)))
        sql = "SELECT * FROM mentions"
        if where:
            sql += " WHERE " + " AND ".join(where)
        order = "ASC" if oldest_first else "DESC"
        sql += f" ORDER BY collected_at {order}, id {order} LIMIT ?"
        params.append(max(int(limit), 0))
        async with self._connect() as db:
            async with db.execute(sql, tuple(params)) as cursor:
                rows = await cursor.fetchall()
        return [self._mention_from_row(r) for r in rows]

    async def list_draftable_mentions(self, limit: int = 10) -> list[Mention]:
        """Mentions the draft batch takes, oldest first: triaged and relevant,
        and a situation in config/engagement_guide.yaml that drafts (see
        ``harvey.engagement.draftable_where``)."""
        where, params = _draftable_where()
        async with self._connect() as db:
            async with db.execute(
                f"SELECT m.* FROM mentions m JOIN triage t ON t.mention_id = m.id "
                f"WHERE {where} "
                f"ORDER BY m.collected_at ASC, m.id ASC LIMIT ?",
                (*params, max(int(limit), 0)),
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._mention_from_row(r) for r in rows]

    async def count_draftable(self) -> int:
        where, params = _draftable_where()
        async with self._connect() as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM mentions m JOIN triage t ON t.mention_id = m.id "
                f"WHERE {where}", params,
            ) as cursor:
                (count,) = await cursor.fetchone()
        return count

    async def set_mention_status(self, mention_id: int, status: MentionStatus | str):
        """Move a mention along its lifecycle.

        Raises ValueError on an unknown status, a missing mention, or a
        transition not in ALLOWED_TRANSITIONS.
        """
        target = MentionStatus(status)  # ValueError on unknown status
        async with self._connect() as db:
            async with db.execute(
                "SELECT status FROM mentions WHERE id = ?", (int(mention_id),)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise ValueError(f"mention {mention_id} not found")
            current = MentionStatus(row[0])
            if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
                raise ValueError(
                    f"mention {mention_id}: {current.value} -> {target.value} not allowed"
                )
            # Compare-and-set so a concurrent writer can't slip past the check.
            cursor = await db.execute(
                "UPDATE mentions SET status = ? WHERE id = ? AND status = ?",
                (target.value, int(mention_id), current.value),
            )
            await db.commit()
            if cursor.rowcount == 0:
                raise ValueError(f"mention {mention_id} changed status concurrently")

    # ── Triage ──

    _TRIAGE_COLUMNS = (
        "relevant", "subject_type", "subject", "competitor", "product", "drug",
        "category", "subtype", "sentiment", "sentiment_score", "urgency", "urgency_reason",
        "reply_appropriate", "phrases_json", "model", "created_at",
        "protocol_decision", "protocol_route", "opportunity_score", "protocol_json",
    )

    async def save_triage(self, triage: Triage):
        """Store (or replace) the triage result for a mention.

        Every column is written, so the upsert leaves the same row the old
        INSERT OR REPLACE did.
        """
        updates = ", ".join(f"{c} = excluded.{c}" for c in self._TRIAGE_COLUMNS)
        async with self._connect() as db:
            await db.execute(
                f"""INSERT INTO triage
                   (mention_id, {", ".join(self._TRIAGE_COLUMNS)})
                   VALUES ({", ".join("?" for _ in range(len(self._TRIAGE_COLUMNS) + 1))})
                   ON CONFLICT(mention_id) DO UPDATE SET {updates}""",
                (
                    triage.mention_id, bool(triage.relevant),
                    triage.subject_type, triage.subject, triage.competitor,
                    triage.product, triage.drug, triage.category.value, triage.subtype or "",
                    triage.sentiment,
                    float(triage.sentiment_score), triage.urgency.value, triage.urgency_reason,
                    bool(triage.reply_appropriate),
                    json.dumps(triage.phrases), triage.model,
                    _ts(triage.created_at),
                    triage.protocol_decision or "",
                    triage.protocol_route or "",
                    triage.opportunity_score,
                    json.dumps(_protocol_blob(triage)),
                ),
            )
            await db.commit()

    async def get_triage(self, mention_id: int) -> Triage | None:
        async with self._connect() as db:
            async with db.execute(
                "SELECT * FROM triage WHERE mention_id = ?", (int(mention_id),)
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        d = dict(row)
        d["phrases"] = _loads(d.pop("phrases_json", None), [])
        d["relevant"] = bool(d["relevant"])
        d["reply_appropriate"] = bool(d["reply_appropriate"])
        d["sentiment_score"] = float(d.get("sentiment_score") or 0.0)
        d["drug"] = d.get("drug") or ""
        d["subtype"] = d.get("subtype") or ""
        blob = _loads(d.pop("protocol_json", None), {})
        blob = blob if isinstance(blob, dict) else {}
        inputs = blob.get("inputs") if isinstance(blob.get("inputs"), dict) else {}
        d.update({k: inputs.get(k) for k in ("intents", "unmet_need", "need_clarity", "useful_contribution")
                  if inputs.get(k) is not None})
        d["protocol"] = blob.get("record") if isinstance(blob.get("record"), dict) else {}
        d["protocol_decision"] = d.get("protocol_decision") or ""
        d["protocol_route"] = d.get("protocol_route") or ""
        return Triage(**d)

    # ── Drafts ──

    async def add_draft(self, draft: Draft) -> int:
        """Insert a draft as the next version for its mention; returns its id."""
        async with self._connect() as db:
            # One statement: the version is computed and inserted atomically;
            # UNIQUE(mention_id, version) backstops any race.
            cursor = await db.execute(
                """INSERT INTO drafts
                   (mention_id, version, text, claim_ids_json, model,
                    filter_ok, filter_hits_json, review_verdict,
                    review_reasons_json, tier, created_at, link_json)
                   VALUES (?, (SELECT COALESCE(MAX(version), 0) + 1
                               FROM drafts WHERE mention_id = ?),
                           ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   RETURNING id""",
                (
                    draft.mention_id, draft.mention_id, draft.text,
                    json.dumps(draft.claim_ids), draft.model,
                    None if draft.filter_ok is None else bool(draft.filter_ok),
                    json.dumps(draft.filter_hits),
                    draft.review_verdict.value if draft.review_verdict else None,
                    json.dumps(draft.review_reasons), draft.tier,
                    _ts(draft.created_at),
                    json.dumps(draft.link) if draft.link else None,
                ),
            )
            (draft_id,) = await cursor.fetchone()
            await db.commit()
            return draft_id

    async def get_latest_draft(self, mention_id: int) -> Draft | None:
        drafts = await self.list_drafts(mention_id, limit=1)
        return drafts[0] if drafts else None

    async def list_drafts(self, mention_id: int, limit: int = 50) -> list[Draft]:
        """Draft versions for a mention, newest first."""
        async with self._connect() as db:
            async with db.execute(
                "SELECT * FROM drafts WHERE mention_id = ? ORDER BY version DESC LIMIT ?",
                (int(mention_id), max(int(limit), 0)),
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._draft_from_row(r) for r in rows]

    @staticmethod
    def _draft_from_row(row) -> Draft:
        d = dict(row)
        d["claim_ids"] = _loads(d.pop("claim_ids_json", None), [])
        d["filter_hits"] = _loads(d.pop("filter_hits_json", None), [])
        d["review_reasons"] = _loads(d.pop("review_reasons_json", None), [])
        d["link"] = _loads(d.pop("link_json", None), {}) or None
        if d["filter_ok"] is not None:
            d["filter_ok"] = bool(d["filter_ok"])
        return Draft(**d)

    # ── Audit log (append-only) ──

    async def append_audit(self, event: AuditEvent) -> int:
        async with self._connect() as db:
            cursor = await db.execute(
                """INSERT INTO audit_log
                   (mention_id, draft_id, event, actor, claim_ids_json,
                    filter_result_json, verdict_json, final_text, permalink, at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   RETURNING id""",
                (
                    event.mention_id, event.draft_id, event.event.value,
                    event.actor, json.dumps(event.claim_ids),
                    json.dumps(event.filter_result), json.dumps(event.verdict),
                    event.final_text, event.permalink, _ts(event.at),
                ),
            )
            (audit_id,) = await cursor.fetchone()
            await db.commit()
            return audit_id

    async def list_audit(self, mention_id: int) -> list[AuditEvent]:
        """Audit events for a mention, oldest first."""
        async with self._connect() as db:
            async with db.execute(
                "SELECT * FROM audit_log WHERE mention_id = ? ORDER BY id ASC",
                (int(mention_id),),
            ) as cursor:
                rows = await cursor.fetchall()
        events = []
        for row in rows:
            d = dict(row)
            d["claim_ids"] = _loads(d.pop("claim_ids_json", None), [])
            d["filter_result"] = _loads(d.pop("filter_result_json", None), {})
            d["verdict"] = _loads(d.pop("verdict_json", None), {})
            events.append(AuditEvent(**d))
        return events

    # ── Escalations ──

    async def create_escalation(self, escalation: Escalation) -> int:
        async with self._connect() as db:
            cursor = await db.execute(
                """INSERT INTO escalations
                   (mention_id, kind, owner, notified_at, sla_due_at,
                    acked_at, acked_by, breached, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   RETURNING id""",
                (
                    escalation.mention_id, escalation.kind, escalation.owner,
                    _ts(escalation.notified_at), _ts(escalation.sla_due_at),
                    _ts(escalation.acked_at), escalation.acked_by,
                    bool(escalation.breached), _ts(escalation.created_at),
                ),
            )
            (escalation_id,) = await cursor.fetchone()
            await db.commit()
            return escalation_id

    @staticmethod
    def _escalation_from_row(row) -> Escalation:
        d = dict(row)
        d["breached"] = bool(d["breached"])
        d["acked_by"] = d["acked_by"] or ""
        return Escalation(**d)

    async def get_escalation(self, escalation_id: int) -> Escalation | None:
        async with self._connect() as db:
            async with db.execute(
                "SELECT * FROM escalations WHERE id = ?", (int(escalation_id),)
            ) as cursor:
                row = await cursor.fetchone()
        return self._escalation_from_row(row) if row else None

    async def get_open_escalation(self, mention_id: int) -> Escalation | None:
        """The oldest unacknowledged escalation for a mention, if any."""
        async with self._connect() as db:
            async with db.execute(
                "SELECT * FROM escalations WHERE mention_id = ? AND acked_at IS NULL "
                "ORDER BY id ASC LIMIT 1",
                (int(mention_id),),
            ) as cursor:
                row = await cursor.fetchone()
        return self._escalation_from_row(row) if row else None

    async def mark_escalation_notified(self, escalation_id: int, at: datetime) -> bool:
        """Record the first successful page. False if it was already set."""
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE escalations SET notified_at = ? "
                "WHERE id = ? AND notified_at IS NULL",
                (_ts(at), int(escalation_id)),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def mark_escalation_breached(self, escalation_id: int) -> bool:
        """Flag an open escalation as past its SLA. False if already flagged."""
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE escalations SET breached = TRUE "
                "WHERE id = ? AND breached = FALSE AND acked_at IS NULL",
                (int(escalation_id),),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def ack_escalation(
        self, escalation_id: int, acked_by: str, at: datetime | None = None
    ) -> bool:
        """Acknowledge an open escalation. False if missing or already acked."""
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE escalations SET acked_at = ?, acked_by = ? "
                "WHERE id = ? AND acked_at IS NULL",
                (_ts(at or _utcnow()), acked_by, int(escalation_id)),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def list_open_escalations(self) -> list[Escalation]:
        """Unacknowledged escalations, most pressing SLA first."""
        async with self._connect() as db:
            async with db.execute(
                "SELECT * FROM escalations WHERE acked_at IS NULL "
                "ORDER BY sla_due_at IS NULL, sla_due_at ASC, id ASC"
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._escalation_from_row(r) for r in rows]

    # ── Usage Tracking ──

    async def get_usage_today(self) -> int:
        today = date.today().isoformat()
        async with self._connect() as db:
            async with db.execute(
                "SELECT claude_calls FROM usage_log WHERE date = ?", (today,)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def increment_usage(self):
        today = date.today().isoformat()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO usage_log (id, date, claude_calls)
                   VALUES (?, ?, 1)
                   ON CONFLICT(date) DO UPDATE SET claude_calls = usage_log.claude_calls + 1""",
                (_new_id(), today),
            )
            await db.commit()

    # ── Per-call usage accounting (usage_events) ──

    async def record_usage_event(
        self,
        *,
        agent: str = "",
        task: str = "",
        session_id: str = "",
        request_key: str = "",
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
        cost_usd: float = 0.0,
        duration_ms: int = 0,
        num_turns: int = 0,
        is_error: bool = False,
        source: str = "result_json",
        created_at: str | None = None,
    ) -> bool:
        """Insert one usage row (one model within one Claude call).

        Returns False when the row was skipped as a duplicate (request_key
        uniqueness makes reconciliation idempotent).
        """
        async with self._connect() as db:
            cursor = await db.execute(
                """INSERT OR IGNORE INTO usage_events
                   (id, created_at, agent, task, session_id, request_key, model,
                    input_tokens, output_tokens, cache_read_tokens,
                    cache_creation_tokens, cost_usd, duration_ms, num_turns,
                    is_error, source)
                   VALUES (?, COALESCE(?, CURRENT_TIMESTAMP), ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _new_id(), created_at, agent, task, session_id, request_key,
                    model, int(input_tokens or 0), int(output_tokens or 0),
                    int(cache_read_tokens or 0), int(cache_creation_tokens or 0),
                    float(cost_usd or 0.0), int(duration_ms or 0),
                    int(num_turns or 0), bool(is_error), source,
                ),
            )
            await db.commit()
            return cursor.rowcount > 0

    _USAGE_SUM = (
        "COUNT(DISTINCT CASE WHEN session_id != '' THEN session_id ELSE id END) AS calls, "
        "SUM(input_tokens) AS input_tokens, "
        "SUM(output_tokens) AS output_tokens, "
        "SUM(cache_read_tokens) AS cache_read_tokens, "
        "SUM(cache_creation_tokens) AS cache_creation_tokens, "
        "SUM(cost_usd) AS cost_usd"
    )

    @staticmethod
    def _usage_row_to_dict(row) -> dict:
        d = dict(row)
        for key, value in d.items():
            if value is None and key != "period":
                d[key] = 0
        if "cost_usd" in d:
            d["cost_usd"] = round(float(d["cost_usd"] or 0.0), 6)
        return d

    async def _usage_grouped(self, group_expr: str, alias: str, days: int) -> list[dict]:
        # GROUP BY 1: the alias shadows a real column (agent/task/model), and
        # Postgres would group by the column, not the CASE expression.
        sql = (
            f"SELECT {group_expr} AS {alias}, {self._USAGE_SUM} "
            f"FROM usage_events "
            f"WHERE created_at >= ? "
            f"GROUP BY 1 ORDER BY cost_usd DESC"
        )
        async with self._connect() as db:
            async with db.execute(sql, (_ago(days=int(days)),)) as cursor:
                return [self._usage_row_to_dict(r) for r in await cursor.fetchall()]

    async def usage_by_agent(self, days: int = 30) -> list[dict]:
        return await self._usage_grouped(
            "CASE WHEN agent = '' THEN 'other' ELSE agent END", "agent", days
        )

    async def usage_by_task(self, days: int = 30) -> list[dict]:
        return await self._usage_grouped(
            "CASE WHEN task = '' THEN 'other' ELSE task END", "task", days
        )

    async def usage_by_model(self, days: int = 30) -> list[dict]:
        return await self._usage_grouped(
            "CASE WHEN model = '' THEN 'unknown' ELSE model END", "model", days
        )

    async def usage_by_day(self, days: int = 30) -> list[dict]:
        sql = (
            f"SELECT date(created_at) AS day, {self._USAGE_SUM} "
            f"FROM usage_events "
            f"WHERE created_at >= ? "
            f"GROUP BY 1 ORDER BY day ASC"
        )
        async with self._connect() as db:
            async with db.execute(sql, (_ago(days=int(days)),)) as cursor:
                return [self._usage_row_to_dict(r) for r in await cursor.fetchall()]

    async def usage_totals(self) -> dict:
        """Rollups for today / last 7 days / last 30 days (UTC)."""
        totals = {}
        async with self._connect() as db:
            for label, where, params in usage_windows():
                async with db.execute(
                    f"SELECT {self._USAGE_SUM} FROM usage_events WHERE {where}", params
                ) as cursor:
                    row = await cursor.fetchone()
                    totals[label] = self._usage_row_to_dict(row) if row else {}
        return totals

    # ── Summary for the heartbeat, CLI, and dashboard ──

    async def get_state_summary(self) -> dict:
        """Mention counts per status (every status present, zero-filled),
        open escalations, mentions waiting for a draft, and today's Claude
        call count."""
        counts = {s.value: 0 for s in MentionStatus}
        async with self._connect() as db:
            async with db.execute(
                "SELECT status, COUNT(*) FROM mentions GROUP BY status"
            ) as cursor:
                for status, n in await cursor.fetchall():
                    counts[status] = n
            async with db.execute(
                "SELECT COUNT(*) FROM escalations WHERE acked_at IS NULL"
            ) as cursor:
                (open_escalations,) = await cursor.fetchone()
        return {
            "mentions": counts,
            "total": sum(counts.values()),
            "open_escalations": open_escalations,
            "draftable": await self.count_draftable(),
            "usage_today": await self.get_usage_today(),
        }
