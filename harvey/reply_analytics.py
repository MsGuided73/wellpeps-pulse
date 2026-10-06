"""Replies & links: counts of approved / posted replies by platform and by
tracked link, and the utm_content list marketing joins with site analytics.

Our own replies only (never mention text, handles or permalinks), so no
privacy floor applies. No Claude calls, no external calls. A reply counts in
the window when its latest approval falls inside the last ``days`` days.
Status is the mention's current one: ``approved`` (waiting to be posted by
hand) or ``posted``.

Matching conversions: every tracked URL carries ``utm_content=m<mention
id>`` (harvey/links.py). In Google Analytics 4, add "Session manual ad
content" (``utm_content``) as a dimension in Explore, filter campaign
``pulse``, and join on the ``utm_content`` column of the CSV export.
"""

import csv
import io
import json
from datetime import datetime, timedelta, timezone

from harvey import links

DEFAULT_DAYS = 30
MAX_DAYS = 366
NO_LINK = "none"
CSV_COLUMNS = ("utm_content", "link_id", "platform", "utm_term", "status", "approved_at", "posted_at")
_CSV_RISKY = ("=", "+", "-", "@", "\t", "\r")

_SQL = (
    "SELECT m.id AS mention_id, m.platform, m.status, "
    "(SELECT d.link_json FROM drafts d WHERE d.mention_id = m.id ORDER BY d.version DESC LIMIT 1) AS link_json, "
    "(SELECT MAX(a.at) FROM audit_log a WHERE a.mention_id = m.id AND a.event = 'approved') AS approved_at, "
    "(SELECT MAX(a.at) FROM audit_log a WHERE a.mention_id = m.id AND a.event = 'posted') AS posted_at "
    "FROM mentions m WHERE m.status IN ('approved', 'posted') ORDER BY m.id"
)


class RepliesError(ValueError):
    """A bad request parameter (HTTP 400)."""


def parse_days(value) -> int:
    if value in (None, ""):
        return DEFAULT_DAYS
    try:
        days = int(value)
    except (TypeError, ValueError):
        raise RepliesError("days must be a whole number")
    if not 1 <= days <= MAX_DAYS:
        raise RepliesError(f"days must be between 1 and {MAX_DAYS}")
    return days


def _link(raw) -> dict:
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _iso(value) -> str:
    return str(value).replace(" ", "T") if value else ""


async def _rows(state) -> list[dict]:
    async with state.connect() as db:
        async with db.execute(_SQL) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


def _bump(table: dict, key: str, status: str) -> None:
    entry = table.setdefault(key, {"approved": 0, "posted": 0, "total": 0})
    entry[status] += 1
    entry["total"] += 1


async def replies(state, days: int = DEFAULT_DAYS, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = (now - timedelta(days=days)).isoformat()
    by_platform: dict[str, dict] = {}
    by_link: dict[str, dict] = {}
    rows = []
    for row in await _rows(state):
        approved_at = _iso(row["approved_at"])
        if not approved_at or approved_at < cutoff:
            continue
        status = row["status"]
        link = _link(row["link_json"])
        link_id = link.get("id") or NO_LINK
        _bump(by_platform, row["platform"], status)
        _bump(by_link, link_id, status)
        if link.get("utm_content"):
            rows.append({"utm_content": link["utm_content"], "link_id": link_id, "platform": row["platform"],
                         "utm_term": link.get("utm_term", ""), "status": status,
                         "approved_at": approved_at, "posted_at": _iso(row["posted_at"])})
    described = {lid: links.describe({"id": lid}) for lid in by_link if lid != NO_LINK}
    return {
        "days": days,
        "approved": sum(v["approved"] for v in by_platform.values()),
        "posted": sum(v["posted"] for v in by_platform.values()),
        "by_platform": [{"platform": k, **v} for k, v in sorted(by_platform.items(), key=lambda kv: -kv[1]["total"])],
        "by_link": [{"link_id": k, "label": (described.get(k) or {}).get("label", "No link"),
                     "live": bool((described.get(k) or {}).get("live")), **v}
                    for k, v in sorted(by_link.items(), key=lambda kv: -kv[1]["total"])],
        "rows": rows,
    }


def _cell(value) -> str:
    text = str(value or "")
    return "'" + text if text.startswith(_CSV_RISKY) else text  # no spreadsheet formulas


def to_csv(report: dict) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for row in report["rows"]:
        writer.writerow([_cell(row[c]) for c in CSV_COLUMNS])
    return out.getvalue()
