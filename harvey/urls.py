"""Permalink normalization.

``normalize_url`` produces the ``mentions.url_norm`` dedupe key: two links to
the same post that differ only in tracking junk, case of the host, a fragment,
or a trailing slash must collapse to one row. Pure function, no I/O.
"""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query parameters that identify the click, not the content.
TRACKING_PARAMS = frozenset({"fbclid", "gclid", "igshid", "si"})
TRACKING_PREFIXES = ("utm_",)


def _is_tracking(name: str) -> bool:
    lowered = name.lower()
    return lowered in TRACKING_PARAMS or lowered.startswith(TRACKING_PREFIXES)


def normalize_url(url: str) -> str:
    """Canonical form of a permalink for dedupe.

    Lowercases scheme and host, drops the fragment, drops tracking params
    (utm_*, fbclid, gclid, igshid, si), sorts the remaining params, and
    strips a trailing slash. Path case is preserved (it is significant on
    many platforms). Blank input returns "".
    """
    raw = (url or "").strip()
    if not raw:
        return ""

    parts = urlsplit(raw)
    kept = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking(k)
    )
    path = parts.path.rstrip("/")
    return urlunsplit((
        parts.scheme.lower(),
        parts.netloc.lower(),
        path,
        urlencode(kept),
        "",
    ))
