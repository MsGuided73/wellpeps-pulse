"""Which community a mention lives in, which thread, and that community's
rules (config/communities.yaml).

WellPeps approval never overrides a platform, group, subreddit or community
rule (Compliance Training Module 9). Pulse keeps a registry of each
community's rules (Pre-LegitScript strategy, Channel 3) and reads the
community and thread from the mention's permalink.

The registry is config/communities.yaml plus an overlay: what an admin recorded
in the dashboard (table ``community_permissions``, POST
/api/communities/{id}/permission). The recorded value wins over the YAML. The
overlay is a process-wide cache that the async ``ensure_overlay`` /
``refresh_overlay`` fill from the database (the dashboard on every request, the
triage and draft batches at their start), so the synchronous lookups below,
and every caller of them, see recorded values without doing I/O.
"""

import logging
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

from harvey import knowledge
from harvey.db.dialect import sql_utc
from harvey.models.knowledge import CommunityEntry
from harvey.sandbox import urls as sandbox_urls

logger = logging.getLogger("harvey.communities")

_REDDIT_COMMUNITY = re.compile(r"^/r/([A-Za-z0-9_]{2,21})(?:/|$)")
_REDDIT_THREAD = re.compile(r"^/r/([A-Za-z0-9_]{2,21})/comments/([A-Za-z0-9]{1,16})(?:/|$)")
_FACEBOOK_GROUP = re.compile(r"^/groups/([A-Za-z0-9._-]{1,80})(?:/|$)")
_FACEBOOK_THREAD = re.compile(r"^/groups/([A-Za-z0-9._-]{1,80})/(?:posts|permalink)/([0-9A-Za-z]{1,40})(?:/|$)")

NONE = "none"   # the permalink names no community (owned channel, review site, ...)


def _parts(url: str):
    try:
        parts = urlsplit((url or "").strip())
        return (parts.hostname or "").lower(), parts.path
    except ValueError:
        return "", ""


def _is(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def community_id(url: str) -> str:
    """"<platform>:<name>" for the community in a permalink, else ""."""
    ref = sandbox_urls.parse(url)
    if ref is not None:
        return f"sandbox:c/{ref.community}" if ref.kind == "forum" else ""
    host, path = _parts(url)
    if _is(host, "reddit.com"):
        found = _REDDIT_COMMUNITY.match(path)
        return f"reddit:r/{found.group(1).lower()}" if found else ""
    if _is(host, "facebook.com"):
        found = _FACEBOOK_GROUP.match(path)
        return f"facebook:groups/{found.group(1).lower()}" if found else ""
    return ""


def thread_key(url: str) -> str:
    """A key shared by every comment in the same thread ("" when unknown).

    Reddit /r/<sub>/comments/<post id>, Facebook /groups/<g>/posts/<id>, and
    the DEMO sandbox thread. Used by the stop rules (Guide §22).
    """
    ref = sandbox_urls.parse(url)
    if ref is not None:
        return f"sandbox:{ref.kind}:{ref.community}:{ref.thread_id}"
    host, path = _parts(url)
    if _is(host, "reddit.com"):
        found = _REDDIT_THREAD.match(path)
        return f"reddit:{found.group(1).lower()}:{found.group(2).lower()}" if found else ""
    if _is(host, "facebook.com"):
        found = _FACEBOOK_THREAD.match(path)
        return f"facebook:{found.group(1).lower()}:{found.group(2).lower()}" if found else ""
    return ""


@dataclass(frozen=True)
class CommunityStatus:
    id: str                       # "" when the permalink names no community
    participation: str            # allowed | with_permission | prohibited | unknown | none
    links_allowed: bool | None    # None = unknown
    permission_obtained: bool = False
    registered: bool = False      # has an entry in communities.yaml (or a recorded permission)
    name: str = ""

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "participation": self.participation,
                "links_allowed": self.links_allowed, "permission_obtained": self.permission_obtained,
                "registered": self.registered}


# --- Recorded permissions (overlay) -------------------------------------------------------------------

PARTICIPATION_VALUES = ("allowed", "not_allowed", "unknown")
LINKS_VALUES = ("true", "false", "unknown")
OVERLAY_TTL_SECONDS = 15.0
MAX_CONTACT_CHARS = 200
MAX_NOTE_CHARS = 1000
RECORDABLE_PLATFORMS = ("reddit", "facebook")
_RECORDABLE_ID = re.compile(r"^(?:%s):[a-z0-9_./-]{1,100}$" % "|".join(RECORDABLE_PLATFORMS))
_TOPIC = re.compile(r"\btopic\s+([a-z_]+)")
# What the YAML calls each recorded brand_participation.
_YAML_PARTICIPATION = {"allowed": "allowed", "not_allowed": "prohibited", "unknown": "unknown"}


class PermissionInvalid(ValueError):
    """A permission record that can't be stored (maps to HTTP 400)."""


@dataclass(frozen=True)
class Recorded:
    community_id: str
    brand_participation: str      # allowed | not_allowed | unknown
    links_allowed: str            # true | false | unknown
    moderator_contact: str = ""
    note: str = ""
    recorded_by: str = ""
    recorded_at: str = ""

    def as_dict(self) -> dict:
        return {"community_id": self.community_id, "brand_participation": self.brand_participation,
                "links_allowed": self.links_allowed, "moderator_contact": self.moderator_contact,
                "note": self.note, "recorded_by": self.recorded_by, "recorded_at": self.recorded_at}


_overlay: dict[str, Recorded] = {}
_overlay_key: str | None = None      # StateManager.location the overlay was loaded from
_overlay_loaded_at = 0.0             # time.monotonic() of the last load


def set_overlay(rows, key: str | None = "") -> None:
    """Replace the overlay (tests, and the loaders below)."""
    global _overlay, _overlay_key, _overlay_loaded_at
    _overlay = {r.community_id: r for r in rows}
    _overlay_key = key
    _overlay_loaded_at = time.monotonic()


def clear_overlay() -> None:
    """Forget every recorded permission (YAML only), e.g. between tests."""
    global _overlay, _overlay_key, _overlay_loaded_at
    _overlay, _overlay_key, _overlay_loaded_at = {}, None, 0.0


def recorded() -> dict[str, Recorded]:
    return dict(_overlay)


def _recorded_from_row(row: dict) -> Recorded:
    return Recorded(
        community_id=str(row["community_id"]).lower(), brand_participation=row["brand_participation"],
        links_allowed=row["links_allowed"], moderator_contact=row["moderator_contact"] or "",
        note=row["note"] or "", recorded_by=row["recorded_by"] or "", recorded_at=str(row["recorded_at"] or ""))


async def load_recorded(state) -> list[Recorded]:
    async with state.connect() as db:
        async with db.execute(
            "SELECT community_id, brand_participation, links_allowed, moderator_contact, note, "
            "recorded_by, recorded_at FROM community_permissions ORDER BY community_id"
        ) as cursor:
            return [_recorded_from_row(dict(r)) for r in await cursor.fetchall()]


async def refresh_overlay(state) -> None:
    """Reload the overlay from ``state``. A failed read keeps the last good overlay and logs it."""
    try:
        rows = await load_recorded(state)
    except Exception as exc:
        logger.warning("could not load recorded community permissions (%s); using the last loaded set",
                       type(exc).__name__)
        return
    set_overlay(rows, key=state.location)


async def ensure_overlay(state, max_age: float = OVERLAY_TTL_SECONDS) -> None:
    """``refresh_overlay`` unless it was loaded from this database in the last ``max_age`` seconds."""
    key = getattr(state, "location", None)
    if key is None or not hasattr(state, "connect"):
        return
    if _overlay_key == key and time.monotonic() - _overlay_loaded_at < max_age:
        return
    await refresh_overlay(state)


def normalize_links(value) -> str:
    """true | false | unknown from a bool, a string, or None."""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "unknown"
    text = str(value).strip().lower()
    if text in LINKS_VALUES:
        return text
    raise PermissionInvalid("links_allowed must be true, false or unknown")


def valid_community_id(cid: str) -> bool:
    return bool(_RECORDABLE_ID.match(cid)) or any(c.id == cid for c in knowledge.communities())


async def record_permission(state, community_id: str, brand_participation, links_allowed, moderator_contact="",
                            note="", recorded_by: str = "") -> tuple[Recorded, Recorded | None]:
    """Upsert one community's recorded rules; returns (new record, previous record or None).
    Raises ``PermissionInvalid`` for a value that is not allowed."""
    cid = (community_id or "").strip().lower()
    if not valid_community_id(cid):
        raise PermissionInvalid("unknown community id; use reddit:r/<name> or facebook:groups/<id>")
    if brand_participation not in PARTICIPATION_VALUES:
        raise PermissionInvalid(f"brand_participation must be one of: {', '.join(PARTICIPATION_VALUES)}")
    links = normalize_links(links_allowed)
    contact = moderator_contact if moderator_contact is not None else ""
    note = note if note is not None else ""
    if not isinstance(contact, str) or len(contact.strip()) > MAX_CONTACT_CHARS:
        raise PermissionInvalid(f"moderator_contact must be text of at most {MAX_CONTACT_CHARS} characters")
    if not isinstance(note, str) or len(note.strip()) > MAX_NOTE_CHARS:
        raise PermissionInvalid(f"note must be text of at most {MAX_NOTE_CHARS} characters")
    previous = next((r for r in await load_recorded(state) if r.community_id == cid), None)
    stamp = sql_utc(datetime.now(timezone.utc).replace(tzinfo=None))
    async with state.connect() as db:
        await db.execute(
            "INSERT INTO community_permissions (community_id, brand_participation, links_allowed, "
            "moderator_contact, note, recorded_by, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(community_id) DO UPDATE SET brand_participation = excluded.brand_participation, "
            "links_allowed = excluded.links_allowed, moderator_contact = excluded.moderator_contact, "
            "note = excluded.note, recorded_by = excluded.recorded_by, recorded_at = excluded.recorded_at",
            (cid, brand_participation, links, contact.strip(), note.strip(), recorded_by, stamp),
        )
        await db.commit()
    await refresh_overlay(state)
    return _overlay[cid], previous


def _name_of(cid: str) -> str:
    return cid.split(":", 1)[1] if ":" in cid else cid


def _merged(cid: str, entry: CommunityEntry | None, rec: Recorded | None) -> CommunityEntry | None:
    """The YAML entry with the recorded permission laid over it (the record wins).

    Only the permission fields change (brand_participation, links_allowed, permission_obtained,
    rules_checked_at, moderator_contact); id, platform, name and the YAML's notes (which carry the
    topic) always come from the configuration."""
    if rec is None:
        return entry
    try:
        checked = date.fromisoformat(rec.recorded_at[:10])
    except ValueError:
        checked = None
    update = {
        "brand_participation": _YAML_PARTICIPATION[rec.brand_participation],
        "links_allowed": {"true": True, "false": False}.get(rec.links_allowed, "unknown"),
        "permission_obtained": False,   # "allowed" is the recorded outcome; there is no separate permission step
        "rules_checked_at": checked,
    }
    if rec.moderator_contact:
        update["moderator_contact"] = rec.moderator_contact
    if entry is not None:
        return entry.model_copy(update=update)
    return CommunityEntry.model_construct(id=cid, platform=cid.split(":", 1)[0], name=_name_of(cid),
                                          moderator_contact="", notes="", demo=False, **update)


def lookup(cid: str) -> CommunityEntry | None:
    """The registry entry for a community id: config/communities.yaml with the recorded permission over it."""
    cid = (cid or "").lower()
    entry = next((c for c in knowledge.communities() if c.id == cid), None)
    return _merged(cid, entry, _overlay.get(cid))


def status_for_url(url: str) -> CommunityStatus:
    """The rules that apply to a reply on ``url``."""
    cid = community_id(url)
    if not cid:
        return CommunityStatus(id="", participation=NONE, links_allowed=None)
    entry = lookup(cid)
    if entry is None:
        return CommunityStatus(id=cid, participation="unknown", links_allowed=None, name=_name_of(cid))
    links_allowed = entry.links_allowed if isinstance(entry.links_allowed, bool) else None
    return CommunityStatus(id=cid, participation=entry.brand_participation, links_allowed=links_allowed,
                           permission_obtained=entry.permission_obtained, registered=True, name=entry.name)


# --- The dashboard listing (GET /api/communities) ------------------------------------------------------------


def _outward_participation(entry: CommunityEntry, rec: Recorded | None) -> str:
    """allowed | not_allowed | unknown, whichever side the value came from."""
    if rec is not None:
        return rec.brand_participation
    if entry.brand_participation == "allowed":
        return "allowed"
    if entry.brand_participation == "prohibited":
        return "not_allowed"
    if entry.brand_participation == "with_permission" and entry.permission_obtained:
        return "allowed"
    return "unknown"


def _outward_links(value) -> str:
    return "true" if value is True else "false" if value is False else "unknown"


async def mentions_by_community(state, days: int = 7) -> Counter:
    """Mentions collected in the last ``days`` days, per community id."""
    cutoff = sql_utc(datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days))
    async with state.connect() as db:
        async with db.execute("SELECT url FROM mentions WHERE collected_at >= ?", (cutoff,)) as cursor:
            urls = [r[0] for r in await cursor.fetchall()]
    return Counter(cid for cid in (community_id(u) for u in urls) if cid)


def view(cid: str, mentions_7d: int = 0) -> dict | None:
    """One community as the dashboard shows it (None when neither config nor a record knows it)."""
    cid = cid.lower()
    rec = _overlay.get(cid)
    entry = lookup(cid)
    if entry is None:
        return None
    configured = next((c for c in knowledge.communities() if c.id == cid), None)
    topic = _TOPIC.search(configured.notes or "") if configured is not None else None   # the YAML's, never the record's
    return {
        "id": cid, "platform": entry.platform, "name": entry.name, "topic": topic.group(1) if topic else "",
        "brand_participation": _outward_participation(entry, rec),
        "links_allowed": _outward_links(entry.links_allowed),
        "permission_obtained": (rec.brand_participation == "allowed") if rec is not None
        else bool(entry.permission_obtained),
        "rules_checked_at": entry.rules_checked_at.isoformat() if entry.rules_checked_at else None,
        "source": "recorded" if rec is not None else "config",
        "mentions_7d": int(mentions_7d),
        "moderator_contact": entry.moderator_contact or "",
        "note": rec.note if rec is not None else (entry.notes or ""),   # the recorded note, else the YAML's
        "recorded_by": rec.recorded_by if rec is not None else "",
        "recorded_at": rec.recorded_at if rec is not None else None,
        "participation": entry.brand_participation,
    }


async def listing(state) -> list[dict]:
    """Every configured or recorded community, busiest first."""
    await refresh_overlay(state)
    counts = await mentions_by_community(state)
    ids = list(dict.fromkeys([c.id for c in knowledge.communities()] + sorted(_overlay)))
    rows = [v for v in (view(cid, counts.get(cid, 0)) for cid in ids) if v is not None]
    return sorted(rows, key=lambda r: (-r["mentions_7d"], r["id"]))
