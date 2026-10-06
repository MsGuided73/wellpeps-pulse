"""Public links registry helpers: tracked (UTM) URLs, finding registry links
in reply text, and ``pulse links check``.

A Pulse reply may carry at most one link, only from ``config/links.yaml``,
and only when a claim it cites names that link (``link_id``). At draft time
the registry URL is rewritten with UTM parameters so marketing can join
replies to site conversions later (Google Analytics / Shopify):

    utm_source=<platform>  utm_medium=social_reply  utm_campaign=pulse
    utm_content=m<mention id>  utm_term=<community, e.g. the subreddit>

Everything here is deterministic except ``check_links``, which requests each
registry URL (HEAD, then GET when HEAD isn't allowed). The check never edits
links.yaml: ``live: true`` is flipped by a human once the page is deployed.
"""

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from harvey import knowledge
from harvey.models.knowledge import PublicLink

UTM_MEDIUM = "social_reply"
UTM_CAMPAIGN = "pulse"
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
CHECK_TIMEOUT_SECONDS = 10.0
CHECK_SETTING = "links_check"
MAX_COMMUNITY_CHARS = 64

# Same shapes the compliance filter counts as links (R6): a scheme URL, a
# www. host, or a bare domain with a common TLD and optional path.
LINK_RE = re.compile(
    r"https?://\S+|www\.\S+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|co|org|net|io|health|us|ly|app)\b(?:/\S*)?",
    re.IGNORECASE,
)
_TRAILING = ".,;:!?)]}>'\"’”"
_SUBREDDIT_RE = re.compile(r"(?:^|/)r/([A-Za-z0-9_]{2,21})(?:/|$)")


def utm_content(mention_id: int | None) -> str:
    return f"m{int(mention_id)}" if mention_id is not None else ""


def community_slug(permalink: str) -> str:
    """The community a mention lives in, as a short lowercase slug.

    Reddit only for now (the subreddit from the permalink); "" when unknown.
    """
    path = urlsplit(permalink or "").path
    found = _SUBREDDIT_RE.search(path)
    return found.group(1).lower()[:MAX_COMMUNITY_CHARS] if found else ""


def tracked_url(url: str, *, platform: str, mention_id: int | None, community: str = "") -> str:
    """``url`` with Pulse's UTM parameters.

    Other query parameters are kept in order; any existing ``utm_*`` key we
    set is replaced, never duplicated. Values are URL-encoded.
    """
    parts = urlsplit(url)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in UTM_KEYS]
    utm = [
        ("utm_source", (platform or "").strip().lower()),
        ("utm_medium", UTM_MEDIUM),
        ("utm_campaign", UTM_CAMPAIGN),
        ("utm_content", utm_content(mention_id)),
        ("utm_term", (community or "").strip().lower()[:MAX_COMMUNITY_CHARS]),
    ]
    query = urlencode(kept + [(k, v) for k, v in utm if v])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def tracked_url_for(link: PublicLink, mention) -> str:
    """The registry link tracked for one mention (platform, id, community)."""
    return tracked_url(link.url, platform=mention.platform.value, mention_id=mention.id,
                       community=community_slug(mention.url))


# --- Finding links in text ----------------------------------------------------------


def _clean(raw: str) -> str:
    return raw.rstrip(_TRAILING)


def _key(url: str) -> tuple[str, str] | None:
    """(host without www., path without trailing slash), lowercase; None if no host."""
    candidate = url if re.match(r"^https?://", url, re.IGNORECASE) else f"https://{url}"
    try:
        parts = urlsplit(candidate)
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if not host:
        return None
    host = host[4:] if host.startswith("www.") else host
    return host, parts.path.rstrip("/").lower()


def registry_link(url: str) -> PublicLink | None:
    """The registry entry ``url`` points at (query/fragment ignored), else None.

    The host must also be in ``allowed_domains``.
    """
    key = _key(_clean(url))
    if key is None or not knowledge.host_allowed(key[0], knowledge.allowed_link_domains()):
        return None
    return next((link for link in knowledge.links() if _key(link.url) == key), None)


@dataclass(frozen=True)
class LinkUse:
    raw: str                     # exactly as found in the text (trailing punctuation stripped)
    link: PublicLink | None      # the registry entry, or None


def find_links(text: str) -> list[LinkUse]:
    """Every link in ``text``, with its registry entry when it has one."""
    found = []
    for match in LINK_RE.finditer(text or ""):
        raw = _clean(match.group(0))
        if raw:
            found.append(LinkUse(raw=raw, link=registry_link(raw)))
    return found


def mask_registry_links(text: str, placeholder: str = "[link]") -> str:
    """``text`` with every registry link replaced by ``placeholder``.

    The compliance patterns scan this, so words in our own URLs (a guide
    path, a subreddit in utm_term) are not read as reply wording.
    """
    def swap(match: re.Match) -> str:
        raw = match.group(0)
        clean = _clean(raw)
        return placeholder + raw[len(clean):] if registry_link(clean) else raw
    return LINK_RE.sub(swap, text or "")


def track_registry_links(text: str, mention) -> str:
    """Rewrite every registry link in ``text`` to its tracked URL for ``mention``.

    The drafter calls this on the model's reply, so the URL a human copies is
    always the canonical tracked one, whatever the model typed.
    """
    def swap(match: re.Match) -> str:
        raw = match.group(0)
        clean = _clean(raw)
        link = registry_link(clean)
        return tracked_url_for(link, mention) + raw[len(clean):] if link else raw
    return LINK_RE.sub(swap, text or "")


def _query_value(url: str, key: str) -> str:
    try:
        query = urlsplit(url if "://" in url else f"https://{url}").query
    except ValueError:
        return ""
    return next((v for k, v in parse_qsl(query) if k == key), "")


def link_record(text: str) -> dict | None:
    """What a draft stores in ``drafts.link_json``: the first registry link."""
    use = next((u for u in find_links(text) if u.link is not None), None)
    if use is None:
        return None
    return {"id": use.link.id, "label": use.link.label, "url": use.raw,
            "utm_content": _query_value(use.raw, "utm_content"),
            "utm_term": _query_value(use.raw, "utm_term")}


def describe(record: dict | None) -> dict | None:
    """A stored link record plus the registry's *current* label and live flag."""
    if not record or not record.get("id"):
        return None
    link = knowledge.links_by_id().get(record["id"])
    return {**record, "label": link.label if link else record.get("label", ""),
            "live": bool(link and link.live), "known": link is not None}


def not_live(text: str) -> list[PublicLink]:
    """Registry links in ``text`` that are not live yet (approval blockers)."""
    seen: dict[str, PublicLink] = {}
    for use in find_links(text):
        if use.link is not None and not use.link.live:
            seen.setdefault(use.link.id, use.link)
    return list(seen.values())


# --- pulse links check ---------------------------------------------------------------


@dataclass(frozen=True)
class LinkCheck:
    id: str
    url: str
    live_in_config: bool
    status: int | None
    ok: bool
    final_url: str = ""
    error: str = ""


async def _probe(client: httpx.AsyncClient, url: str) -> httpx.Response:
    response = await client.head(url)
    if response.status_code in (405, 501):
        response = await client.get(url)
    return response


async def check_links(entries=None, *, transport: httpx.AsyncBaseTransport | None = None,
                      timeout: float = CHECK_TIMEOUT_SECONDS) -> list[LinkCheck]:
    """Request every registry URL (follows redirects); 2xx at the end is ok."""
    entries = list(knowledge.links() if entries is None else entries)
    results = []
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, transport=transport,
                                 headers={"User-Agent": "WellPeps-Pulse-link-check"}) as client:
        for link in entries:
            try:
                response = await _probe(client, link.url)
            except httpx.HTTPError as exc:
                results.append(LinkCheck(link.id, link.url, link.live, None, False,
                                         error=type(exc).__name__))
                continue
            results.append(LinkCheck(link.id, link.url, link.live, response.status_code,
                                     200 <= response.status_code < 300, final_url=str(response.url)))
    return results


def suggestions(results: list[LinkCheck]) -> list[str]:
    """What a human should change in links.yaml (the check never edits it)."""
    lines = []
    for r in results:
        if r.ok and not r.live_in_config:
            lines.append(f"{r.id} responds {r.status}: set `live: true` (and checked_at) in config/links.yaml")
        elif not r.ok and r.live_in_config:
            lines.append(f"{r.id} is marked live but failed ({r.status or r.error}): "
                         "set `live: false` in config/links.yaml until it is fixed")
    return lines


async def record_check(state, results: list[LinkCheck], now: datetime | None = None) -> dict:
    """Store the last check in the settings table (key ``links_check``)."""
    at = (now or datetime.now(timezone.utc).replace(tzinfo=None)).isoformat(timespec="seconds")
    payload = {"checked_at": at, "results": [asdict(r) for r in results]}
    await state.set_setting(CHECK_SETTING, json.dumps(payload))
    return payload
