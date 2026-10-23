"""Every competitor a post names (R-44, owner request 2026-10-23).

Triage stores ONE competitor per post: the main subject. This module finds every name from
config/competitors.yaml in a post's title and text, deterministically, so the Analytics tab and
the Feed can count all of them.

Matching: names and aliases match case-insensitively ("henry meds", "mondae"), except names that
are also everyday words or very short ("Found", "Ro", "Keeps", "Gimme"): those match only as
written (capitalised) and not as the first word of a sentence. Each competitor counts once per
post. Pure functions; the payload carries counts and names only, never post text, handles,
URLs or mention ids (the analytics privacy rule; communities need ``MIN_COUNT`` posts).
"""

from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache
from typing import Iterable

from harvey import communities, knowledge

MIN_COUNT = 2            # a community is listed once at least this many posts name the competitor there
MIN_TONE_N = 3           # posts needed before a tone average is shown
TOP_NAMED_WITH = 3
TOP_COMMUNITIES = 3

# Names that are also everyday words (or too short to trust): matched only as written.
COMMON_WORDS = frozenset({
    "found", "keeps", "sequence", "hers", "eden", "rex", "hone", "fridays", "emerge", "willow", "sesame",
    "maximus", "fountain", "elysium", "calibrate", "superpower", "roman", "hymns", "henry", "defy", "fella",
    "joi", "blokes", "strut", "orderly", "mochi", "marek", "gimme", "novos", "function", "form",
})


def _noisy(term: str) -> bool:
    return term.lower() in COMMON_WORDS or (len(term) <= 3 and " " not in term)


@lru_cache(maxsize=8)
def _matchers(terms: tuple[tuple[str, str], ...]) -> tuple[re.Pattern | None, re.Pattern | None, dict[str, str]]:
    index = {term.lower(): name for term, name in terms}
    loose = sorted({t for t, _ in terms if not _noisy(t)}, key=len, reverse=True)
    strict = sorted({t for t, _ in terms if _noisy(t)}, key=len, reverse=True)

    def rx(words: list[str], flags: int) -> re.Pattern | None:
        if not words:
            return None
        return re.compile(r"(?<![\w.@/])(?:" + "|".join(re.escape(w) for w in words) + r")(?![\w])", flags)
    return rx(loose, re.IGNORECASE), rx(strict, 0), index


def _registry_terms() -> tuple[tuple[str, str], ...]:
    pairs = []
    for comp in knowledge.competitors().all():
        for term in (comp.name, *comp.aliases):
            term = (term or "").strip()
            if term:
                pairs.append((term, comp.name))
    return tuple(pairs)


def names_in(text: str) -> list[str]:
    """Canonical competitor names in ``text``, each once, in order of first appearance."""
    loose, strict, index = _matchers(_registry_terms())
    text = text or ""
    found: list[tuple[int, str]] = []
    if loose is not None:
        found += [(m.start(), index[m.group(0).lower()]) for m in loose.finditer(text)]
    if strict is not None:
        for m in strict.finditer(text):
            before = text[:m.start()].rstrip()
            if m.group(0).lower() in COMMON_WORDS and (not before or before[-1] in ".!?\n:"):
                continue
            found.append((m.start(), index[m.group(0).lower()]))
    seen: dict[str, None] = {}
    for _, name in sorted(found):
        seen.setdefault(name, None)
    return list(seen)


def named_in_row(row: dict) -> list[str]:
    """Every competitor a mention row names: title + text, plus triage's main subject."""
    names = names_in(f"{row.get('title') or ''}\n{row.get('text') or ''}")
    main = (row.get("competitor") or "").strip()
    return names if not main or main in names else [*names, main]


def matching_ids(rows: Iterable[dict], name: str) -> list[int]:
    """Ids of the rows that name ``name`` (canonicalised through the registry)."""
    wanted = knowledge.competitor_lookup().get((name or "").strip().lower(), (name or "").strip())
    return [int(r["id"]) for r in rows if wanted in named_in_row(r)]


def _community(url: str) -> str:
    cid = communities.community_id(url or "")
    return cid.split(":", 1)[1] if ":" in cid else ""


def _change(current: int, previous: int) -> float | None:
    return None if not previous else round((current - previous) / previous * 100, 1)


def rollup(current: list[dict], previous: list[dict], only: Iterable[str] = ()) -> dict:
    """Per competitor: posts naming it now and in the previous period, the change, share of all
    competitor mentions, complaint posts, posts where it is the main subject, average tone
    (needs ``MIN_TONE_N`` posts), competitors named alongside it and its top communities."""
    only = set(only)
    stats: dict[str, dict] = {}
    prev_counts: Counter = Counter()
    for row in previous:
        prev_counts.update(n for n in named_in_row(row) if not only or n in only)
    posts_any = 0
    for row in current:
        names = [n for n in named_in_row(row) if not only or n in only]
        if not names:
            continue
        posts_any += 1
        community = _community(row.get("url") or "")
        for name in names:
            s = stats.setdefault(name, {"posts": 0, "complaints": 0, "main": 0, "scores": [],
                                        "with": Counter(), "communities": Counter()})
            s["posts"] += 1
            s["complaints"] += int(row.get("category") == "complaint")
            s["main"] += int((row.get("competitor") or "") == name)
            if row.get("sentiment_score") is not None:
                s["scores"].append(float(row["sentiment_score"]))
            s["with"].update(n for n in names if n != name)
            if community:
                s["communities"][community] += 1
    total = sum(s["posts"] for s in stats.values())
    out = []
    for name, s in stats.items():
        scores = s["scores"]
        out.append({
            "name": name, "posts": s["posts"], "previous": prev_counts.get(name, 0),
            "change_pct": _change(s["posts"], prev_counts.get(name, 0)),
            "share": round(s["posts"] / total, 4) if total else 0.0,
            "complaints": s["complaints"], "main_subject": s["main"],
            "tone": round(sum(scores) / len(scores), 2) if len(scores) >= MIN_TONE_N else None,
            "named_with": [{"name": n, "posts": c} for n, c in
                           sorted(s["with"].items(), key=lambda x: (-x[1], x[0]))[:TOP_NAMED_WITH]],
            "communities": [{"name": c, "posts": k}
                            for c, k in sorted(s["communities"].items(), key=lambda x: (-x[1], x[0]))
                            if k >= MIN_COUNT][:TOP_COMMUNITIES],
        })
    out.sort(key=lambda c: (-c["posts"], c["name"]))
    gone = sorted(n for n, k in prev_counts.items() if k and n not in stats)
    return {"competitors": out, "posts_naming_any": posts_any, "total_mentions": total,
            "previous_mentions": sum(prev_counts.values()), "no_longer_named": gone[:10],
            "takeaway": _takeaway(out, posts_any)}


def _takeaway(rows: list[dict], posts_any: int) -> str:
    if not rows:
        return "No competitor was named in this period."
    top = rows[0]
    more = sum(1 for r in rows if r["posts"] > 1 and r["named_with"])
    line = (f"{len(rows)} competitor{'s' if len(rows) != 1 else ''} named in {posts_any} "
            f"post{'s' if posts_any != 1 else ''}; {top['name']} most often ({top['posts']}).")
    rising = [r for r in rows if r["change_pct"] is not None and r["change_pct"] > 0 and r["posts"] >= MIN_COUNT]
    if rising:
        best = max(rising, key=lambda r: r["change_pct"])
        line += f" {best['name']} is up {best['change_pct']:.0f}% on the previous period."
    if more:
        line += " Several are named side by side in comparison posts."
    return line
