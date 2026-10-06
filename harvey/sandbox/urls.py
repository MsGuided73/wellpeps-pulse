"""Sandbox permalinks.

    Demo Forum   /sandbox/f/c/<community>/<thread>[/comment/<id>#c<id>]
    Demo Photos  /sandbox/p/<thread>[/comment/<id>#c<id>]
    Demo Reviews /sandbox/r/<thread>[/comment/<id>#c<id>]

The comment id is in the path, not only in the ``#c<id>`` fragment, because
``harvey.urls.normalize_url`` drops fragments: two mentions that are comments
in the same thread must still get different ``url_norm`` dedupe keys. The
fragment stays so the browser scrolls to the comment.

A sandbox URL is an http(s) URL on a loopback host whose path starts with
/sandbox/. Pure functions, no I/O.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from harvey.auth import is_loopback

PREFIX = {"forum": "f", "photo": "p", "review": "r"}
COMMUNITY_RE = r"[a-z0-9_]{2,40}"
_PATH_RE = re.compile(
    rf"^/sandbox/(?:f/c/(?P<community>{COMMUNITY_RE})|(?P<kind>[pr]))/(?P<thread>\d{{1,9}})"
    r"(?:/comment/(?P<comment>\d{1,9}))?/?$"
)
_KIND_FOR = {"p": "photo", "r": "review"}


@dataclass(frozen=True)
class SandboxRef:
    kind: str
    community: str
    thread_id: int
    comment_id: int | None


def thread_path(kind: str, community: str, thread_id: int) -> str:
    if kind == "forum":
        return f"/sandbox/f/c/{community}/{int(thread_id)}"
    return f"/sandbox/{PREFIX[kind]}/{int(thread_id)}"


def comment_path(kind: str, community: str, thread_id: int, comment_id: int) -> str:
    cid = int(comment_id)
    return f"{thread_path(kind, community, thread_id)}/comment/{cid}#c{cid}"


def permalink(kind: str, community: str, thread_id: int, comment_id: int | None = None) -> str:
    if comment_id is None:
        return thread_path(kind, community, thread_id)
    return comment_path(kind, community, thread_id, comment_id)


def parse_path(path: str) -> SandboxRef | None:
    found = _PATH_RE.match(path or "")
    if not found:
        return None
    comment = found.group("comment")
    if found.group("community"):
        return SandboxRef("forum", found.group("community"), int(found.group("thread")),
                          int(comment) if comment else None)
    return SandboxRef(_KIND_FOR[found.group("kind")], "", int(found.group("thread")),
                      int(comment) if comment else None)


def parse(url: str) -> SandboxRef | None:
    """The thread/comment a sandbox URL points at; None for anything else."""
    try:
        parts = urlsplit((url or "").strip())
        host = parts.hostname or ""
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not is_loopback(host):
        return None
    return parse_path(parts.path)


def is_sandbox_url(url: str) -> bool:
    """Any loopback http(s) URL under /sandbox/ (threads, comments, guides)."""
    try:
        parts = urlsplit((url or "").strip())
        host = parts.hostname or ""
    except ValueError:
        return False
    return (parts.scheme.lower() in ("http", "https") and is_loopback(host)
            and parts.path.startswith("/sandbox/"))
