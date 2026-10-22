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
import os
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


# --- Rules mode: enforced or advisory (R-30) ----------------------------------------------------------
#
# Owner decision 2026-10-21: in advisory mode Pulse listens and drafts in every
# community, and a rep may post where the rules are unknown or prohibit brand
# replies after acknowledging the warning. The registry still says what the
# rules are (status_for_url); only drafting and the approval gate read them
# through drafting_status / rules_advisory.

RULES_MODES = ("enforced", "advisory")
RULES_ENV = "PULSE_COMMUNITY_RULES"
_HELD = ("prohibited", "with_permission")


def rules_advisory() -> bool:
    """True when community rules are advisory (``posting_holds: advisory`` in
    config/communities.yaml, or PULSE_COMMUNITY_RULES=advisory)."""
    raw = os.environ.get(RULES_ENV, "")
    mode = raw.strip().lower()
    if not mode:
        mode = knowledge.community_posting_holds()
    elif mode not in RULES_MODES:
        # A typo must never loosen the rules: fail closed.
        logger.warning("%s=%r is not one of %s; using enforced", RULES_ENV, raw, ", ".join(RULES_MODES))
        mode = "enforced"
    return mode == "advisory"


def drafting_status(status: "CommunityStatus") -> "CommunityStatus":
    """The status drafting and the compliance filter work from. In advisory
    mode a prohibition (or a permission still missing) reads as unverified
    rules: a cautious, educational draft with no link and no promotion."""
    if not rules_advisory() or status.participation not in _HELD:
        return status
    if status.participation == "with_permission" and status.permission_obtained:
        return status
    return CommunityStatus(id=status.id, participation="unknown", links_allowed=None,
                           permission_obtained=False, registered=status.registered, name=status.name)


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
    fields = {"id": cid, "platform": cid.split(":", 1)[0], "name": _name_of(cid), "moderator_contact": "",
              "notes": "", "demo": False, **update}     # a recorded moderator contact wins over the default
    return CommunityEntry.model_construct(**fields)


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


async def mentions_by_community(state, days: int | None = 7) -> Counter:
    """Mentions collected in the last ``days`` days (None = all time), per community id."""
    sql, params = "SELECT url FROM mentions", ()
    if days is not None:
        cutoff = sql_utc(datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days))
        sql, params = sql + " WHERE collected_at >= ?", (cutoff,)
    async with state.connect() as db:
        async with db.execute(sql, params) as cursor:
            urls = [r[0] for r in await cursor.fetchall()]
    return Counter(cid for cid in (community_id(u) for u in urls) if cid)


# --- The Communities page: group links, clearance checklist, what is blocked ----------------------------


def community_url(cid: str) -> str:
    """The group's own page ("" when Pulse can't build one)."""
    platform, _, name = (cid or "").partition(":")
    if platform == "reddit" and name.startswith("r/") and len(name) > 2:
        return f"https://www.reddit.com/{name}/"
    if platform == "facebook" and name.startswith("groups/") and len(name) > 7:
        return f"https://www.facebook.com/{name}/"
    return ""


def competitor_of(cid: str) -> str:
    """The competitor whose own community this is ("" = none), from the insights community index."""
    from harvey import insights  # late: insights imports this module

    name = (cid or "").partition(":")[2]
    key = insights.normalize_name(name.split("/", 1)[-1])
    return insights.community_index().get(key, "") if key else ""


GUIDE_BLOCK = "No Smart Patient's Guide in replies, not even by name"


def _blocks(participation: str, links: str, competitor: str, advisory: bool) -> list[str]:
    blocks: list[str] = []
    if participation != "allowed":
        if participation == "not_allowed":
            blocks.append("The rules don't allow brand replies here")
        blocks += [GUIDE_BLOCK, "No links in replies", "Drafts stay educational: no promotion"]
        blocks.append("A rep must acknowledge the community warning before approving a reply" if advisory
                      else "Replies here can't be approved")
    elif links == "false":
        blocks.append("No links: the guide is named without a link")
    elif links != "true":
        blocks.append("No links until you record whether links are allowed: the guide is named without a link")
    if competitor:
        blocks.append(f"This is {competitor}'s own community: "
                      + ("a rep must acknowledge it before approving a reply" if advisory
                         else "listen only, no replies"))
    return blocks


def clearance(row: dict) -> dict:
    """The clearance checklist and, in plain words, what stays blocked until the community is cleared.

    ``row`` carries the listing's outward values: brand_participation (allowed | not_allowed | unknown),
    links_allowed ("true" | "false" | "unknown"), rules_checked_at, moderator_contact and competitor.
    Item states: done | todo | blocked | optional. Status: cleared | partial | not_checked | not_allowed."""
    participation = row.get("brand_participation") or "unknown"
    links = row.get("links_allowed") or "unknown"
    checked = bool(row.get("rules_checked_at"))
    items = [
        {"key": "rules_read", "label": "Read the community's posted rules",
         "state": "done" if checked else "todo"},
        {"key": "brand_replies", "label": "Brand replies allowed (or a moderator said yes)",
         "state": {"allowed": "done", "not_allowed": "blocked"}.get(participation, "todo")},
        {"key": "links", "label": "Know whether links are allowed",
         "state": "done" if links in ("true", "false") else "todo"},
        {"key": "moderator", "label": "Moderator contact on file (optional)",
         "state": "done" if (row.get("moderator_contact") or "").strip() else "optional"},
    ]
    if participation == "not_allowed":
        status = "not_allowed"
    elif participation == "allowed" and links in ("true", "false"):
        status = "cleared"
    elif participation == "unknown" and not checked:
        status = "not_checked"
    else:
        status = "partial"
    return {"status": status, "items": items,
            "blocks": _blocks(participation, links, row.get("competitor") or "", rules_advisory())}


def _seen_view(cid: str, mentions_7d: int, mentions_total: int) -> dict:
    """A community Pulse collected from that neither the registry nor a record knows yet."""
    row = {
        "id": cid, "platform": cid.split(":", 1)[0], "name": _name_of(cid), "topic": "",
        "brand_participation": "unknown", "links_allowed": "unknown", "permission_obtained": False,
        "rules_checked_at": None, "source": "seen", "mentions_7d": int(mentions_7d),
        "mentions_total": int(mentions_total), "moderator_contact": "", "note": "", "recorded_by": "",
        "recorded_at": None, "participation": "unknown", "url": community_url(cid), "competitor": competitor_of(cid),
    }
    row["clearance"] = clearance(row)
    return row


def view(cid: str, mentions_7d: int = 0, mentions_total: int | None = None) -> dict | None:
    """One community as the dashboard shows it (None when neither config nor a record knows it)."""
    cid = cid.lower()
    rec = _overlay.get(cid)
    entry = lookup(cid)
    if entry is None:
        return None
    configured = next((c for c in knowledge.communities() if c.id == cid), None)
    topic = _TOPIC.search(configured.notes or "") if configured is not None else None   # the YAML's, never the record's
    row = {
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
        "mentions_total": int(mentions_total if mentions_total is not None else mentions_7d),
        "url": community_url(cid),
        "competitor": competitor_of(cid),
    }
    row["clearance"] = clearance(row)
    return row


async def listing(state) -> list[dict]:
    """Every community Pulse knows of (configured, recorded, or seen in collected posts), busiest first."""
    await refresh_overlay(state)
    recent = await mentions_by_community(state)
    total = await mentions_by_community(state, days=None)
    ids = list(dict.fromkeys([c.id for c in knowledge.communities()] + sorted(_overlay) + sorted(total)))
    rows = []
    for cid in ids:
        row = view(cid, recent.get(cid, 0), total.get(cid, 0))
        rows.append(row if row is not None else _seen_view(cid, recent.get(cid, 0), total.get(cid, 0)))
    return sorted(rows, key=lambda r: (-r["mentions_total"], -r["mentions_7d"], r["id"]))
