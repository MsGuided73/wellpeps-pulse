"""SQL for Pulse trends, briefs and the language bank (Phase 8).

Kept apart from ``harvey.state`` so the state manager doesn't keep growing;
every function takes a ``StateManager`` and uses its ``connect()``.

Timestamps are naive UTC ISO strings (``YYYY-MM-DDTHH:MM:SS``): SQLite
compares them as strings, Postgres as ``timestamp`` values, and both hand
them back in that same ISO form (see harvey.db.dialect). A mention's time
is ``posted_at`` when the platform gave one, else ``collected_at``.
"""

import json
from datetime import datetime

# Triaged, relevant mentions only: never untriaged or dropped ones.
RELEVANT_WHERE = "t.relevant = TRUE AND m.status NOT IN ('new', 'dropped')"
MENTION_TIME = "COALESCE(m.posted_at, m.collected_at)"
LANGUAGE_SORTS = {"count": "count DESC, last_seen DESC, id ASC",
                  "recent": "last_seen DESC, count DESC, id ASC"}
MAX_PAGE = 500


def _ts(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _loads(raw, default):
    try:
        parsed = json.loads(raw) if raw else default
    except (json.JSONDecodeError, TypeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


async def _rows(state, sql: str, params: tuple = ()) -> list[dict]:
    async with state.connect() as db:
        async with db.execute(sql, params) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


# ── Mentions for trends ──


async def fetch_rows(state, start: datetime, end: datetime) -> list[dict]:
    """Relevant triaged mentions in [start, end), newest first, with triage fields."""
    return await _rows(state, (
        f"SELECT m.id, m.title, m.text, {MENTION_TIME} AS at, t.subject_type, t.competitor, "
        "t.product, t.drug, t.category, t.sentiment, t.sentiment_score "
        "FROM mentions m JOIN triage t ON t.mention_id = m.id "
        f"WHERE {RELEVANT_WHERE} AND {MENTION_TIME} >= ? AND {MENTION_TIME} < ? "
        f"ORDER BY {MENTION_TIME} DESC, m.id DESC"
    ), (_ts(start), _ts(end)))


async def count_relevant(state, start: datetime, end: datetime) -> int:
    rows = await _rows(state, (
        "SELECT COUNT(*) AS n FROM mentions m JOIN triage t ON t.mention_id = m.id "
        f"WHERE {RELEVANT_WHERE} AND {MENTION_TIME} >= ? AND {MENTION_TIME} < ?"
    ), (_ts(start), _ts(end)))
    return rows[0]["n"]


# ── Language bank ──


async def unbanked_mentions(state) -> list[dict]:
    """Relevant triaged mentions not yet banked, oldest first."""
    return await _rows(state, (
        f"SELECT m.id, {MENTION_TIME} AS at, t.product, t.drug, t.category, t.sentiment, "
        "t.phrases_json FROM mentions m JOIN triage t ON t.mention_id = m.id "
        "LEFT JOIN language_bank_mentions b ON b.mention_id = m.id "
        f"WHERE b.mention_id IS NULL AND {RELEVANT_WHERE} ORDER BY at ASC, m.id ASC"
    ))


async def bank_mention(state, mention: dict, phrases: list[tuple[str, str]]) -> bool:
    """Upsert ``phrases`` ([(verbatim, norm)]) for one mention; mark it banked.

    One transaction: the phrases and the "banked" marker land together, so a
    crash never double-counts. Returns False when it was already banked.
    """
    scope = mention.get("product") or mention.get("drug") or ""
    seen = mention.get("at")
    async with state.connect() as db:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO language_bank_mentions (mention_id) VALUES (?)", (mention["id"],))
        if not cursor.rowcount:
            await db.rollback()
            return False
        for phrase, norm in phrases:
            await db.execute(
                """INSERT INTO language_bank
                   (phrase, phrase_norm, scope, product, drug, category, sentiment,
                    example_mention_id, count, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                   ON CONFLICT(phrase_norm, scope) DO UPDATE SET
                     count = language_bank.count + 1,
                     first_seen = CASE
                       WHEN excluded.first_seen IS NULL THEN NULL
                       WHEN language_bank.first_seen IS NULL
                         OR excluded.first_seen < language_bank.first_seen THEN excluded.first_seen
                       ELSE language_bank.first_seen END,
                     last_seen = CASE
                       WHEN excluded.last_seen IS NULL THEN NULL
                       WHEN language_bank.last_seen IS NULL
                         OR excluded.last_seen > language_bank.last_seen THEN excluded.last_seen
                       ELSE language_bank.last_seen END""",
                (phrase, norm, scope, mention.get("product") or "", mention.get("drug") or "",
                 mention.get("category") or "", mention.get("sentiment") or "", mention["id"],
                 seen, seen),
            )
        await db.commit()
    return True


async def top_phrases(state, since: datetime, limit: int = 20) -> list[dict]:
    """Most-used phrases seen since ``since`` (for the brief payload)."""
    return await _rows(state, (
        "SELECT phrase, count, category, scope FROM language_bank "
        "WHERE last_seen >= ? ORDER BY count DESC, last_seen DESC, id ASC LIMIT ?"
    ), (_ts(since), int(limit)))


async def search_language_bank(state, q: str | None = None, category: str | None = None,
                               drug: str | None = None, sort: str = "count",
                               limit: int = 100, offset: int = 0) -> dict:
    """Paginated language bank; ``q`` matches the phrase, ``drug`` the scope's drug."""
    where, params = [], []
    if q and q.strip():
        where.append("instr(phrase_norm, lower(?)) > 0")
        params.append(q.strip()[:200])
    if category:
        where.append("category = ?")
        params.append(category)
    if drug:
        where.append("lower(drug) = lower(?)")
        params.append(drug)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    order = LANGUAGE_SORTS.get(sort, LANGUAGE_SORTS["count"])
    limit = max(1, min(int(limit), MAX_PAGE))
    offset = max(0, int(offset))
    total = (await _rows(state, f"SELECT COUNT(*) AS n FROM language_bank {clause}", tuple(params)))[0]["n"]
    items = await _rows(state, (
        "SELECT id, phrase, phrase_norm, scope, product, drug, category, sentiment, "
        f"example_mention_id, count, first_seen, last_seen FROM language_bank {clause} "
        f"ORDER BY {order} LIMIT ? OFFSET ?"
    ), (*params, limit, offset))
    return {"items": items, "total": total, "limit": limit, "offset": offset}


# ── Briefs ──


def _brief_from_row(row: dict) -> dict:
    brief = dict(row)
    brief["action_cards"] = _loads(brief.pop("action_cards_json", None), [])
    brief["watchlist"] = _loads(brief.pop("watchlist_json", None), [])
    brief["data"] = _loads(brief.pop("data_json", None), {})
    return brief


async def get_brief(state, brief_id: int) -> dict | None:
    rows = await _rows(state, "SELECT * FROM briefs WHERE id = ?", (int(brief_id),))
    return _brief_from_row(rows[0]) if rows else None


async def get_brief_by_window(state, period: str, window_start: datetime) -> dict | None:
    rows = await _rows(state, "SELECT * FROM briefs WHERE period = ? AND window_start = ?",
                       (period, _ts(window_start)))
    return _brief_from_row(rows[0]) if rows else None


async def save_brief(state, brief: dict, terms: list[dict]) -> int:
    """Insert or replace the brief for (period, window_start) and its trend terms."""
    async with state.connect() as db:
        await db.execute(
            """INSERT INTO briefs
               (period, window_start, window_end, status, headline, summary_md,
                action_cards_json, watchlist_json, data_json, model, slack_sent_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
               ON CONFLICT(period, window_start) DO UPDATE SET
                 window_end = excluded.window_end, status = excluded.status,
                 headline = excluded.headline, summary_md = excluded.summary_md,
                 action_cards_json = excluded.action_cards_json,
                 watchlist_json = excluded.watchlist_json, data_json = excluded.data_json,
                 model = excluded.model, slack_sent_at = NULL, created_at = excluded.created_at""",
            (brief["period"], _ts(brief["window_start"]), _ts(brief["window_end"]), brief["status"],
             brief["headline"], brief["summary_md"], json.dumps(brief["action_cards"]),
             json.dumps(brief["watchlist"]), json.dumps(brief["data"]), brief.get("model", ""),
             _ts(brief["created_at"])),
        )
        async with db.execute("SELECT id FROM briefs WHERE period = ? AND window_start = ?",
                              (brief["period"], _ts(brief["window_start"]))) as cursor:
            (brief_id,) = await cursor.fetchone()
        await db.execute("DELETE FROM trend_terms WHERE brief_id = ?", (brief_id,))
        await db.executemany(
            """INSERT INTO trend_terms (brief_id, rank, term, count, baseline_count, velocity,
                                        score, is_new, example_ids_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(brief_id, rank, t["term"], int(t["count"]), int(t["baseline_count"]),
              float(t["velocity"]), float(t["score"]), bool(t["is_new"]),
              json.dumps(list(t["example_ids"])))
             for rank, t in enumerate(terms, start=1)],
        )
        await db.commit()
    return brief_id


async def list_briefs(state, period: str | None = None, limit: int = 30) -> list[dict]:
    """Newest window first; light rows (no aggregates)."""
    where, params = ("WHERE period = ?", (period,)) if period else ("", ())
    return await _rows(state, (
        "SELECT id, period, window_start, window_end, status, headline, model, slack_sent_at, "
        f"created_at FROM briefs {where} ORDER BY window_start DESC, id DESC LIMIT ?"
    ), (*params, max(1, min(int(limit), MAX_PAGE))))


async def list_trend_terms(state, brief_id: int) -> list[dict]:
    rows = await _rows(state, (
        "SELECT rank, term, count, baseline_count, velocity, score, is_new, example_ids_json "
        "FROM trend_terms WHERE brief_id = ? ORDER BY rank ASC"
    ), (int(brief_id),))
    for row in rows:
        row["is_new"] = bool(row["is_new"])
        row["example_ids"] = _loads(row.pop("example_ids_json", None), [])
    return rows


async def mark_brief_slack_sent(state, brief_id: int, at: datetime) -> None:
    async with state.connect() as db:
        await db.execute("UPDATE briefs SET slack_sent_at = ? WHERE id = ?", (_ts(at), int(brief_id)))
        await db.commit()
