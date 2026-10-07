"""Which community a mention lives in, which thread, and that community's
rules (config/communities.yaml).

WellPeps approval never overrides a platform, group, subreddit or community
rule (Compliance Training Module 9). Pulse keeps a registry of each
community's rules (Pre-LegitScript strategy, Channel 3) and reads the
community and thread from the mention's permalink. Pure functions, no I/O.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from harvey import knowledge
from harvey.models.knowledge import CommunityEntry
from harvey.sandbox import urls as sandbox_urls

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
    registered: bool = False      # has an entry in communities.yaml
    name: str = ""

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "participation": self.participation,
                "links_allowed": self.links_allowed, "permission_obtained": self.permission_obtained,
                "registered": self.registered}


def lookup(cid: str) -> CommunityEntry | None:
    cid = (cid or "").lower()
    return next((c for c in knowledge.communities() if c.id == cid), None)


def status_for_url(url: str) -> CommunityStatus:
    """The rules that apply to a reply on ``url``."""
    cid = community_id(url)
    if not cid:
        return CommunityStatus(id="", participation=NONE, links_allowed=None)
    entry = lookup(cid)
    if entry is None:
        return CommunityStatus(id=cid, participation="unknown", links_allowed=None, name=cid.split(":", 1)[1])
    links_allowed = entry.links_allowed if isinstance(entry.links_allowed, bool) else None
    return CommunityStatus(id=cid, participation=entry.brand_participation, links_allowed=links_allowed,
                           permission_obtained=entry.permission_obtained, registered=True, name=entry.name)
