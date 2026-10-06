"""Deterministic compliance filter for draft public replies.

``compliance_filter`` checks a draft against ``config/compliance_rules.yaml``
and the claims library. It never calls Claude, so the same input always gives
the same result. Tiers follow reply-compliance-rules.md:

- red:    a hard block (R13-R39 "never write this", R31 privacy, missing or
          unknown claim IDs, limits, no disclosure in the first sentence
          (R2/R3), a link outside config/links.yaml or not backed by a
          cited claim's ``link_id``). ``ok`` is False.
- yellow: allowed as a draft but needs human compliance review (R10), incl.
          a registry link that is not live yet (approval is blocked on it
          separately, see harvey/review.py).
- green:  nothing found. A human still approves every post (R1).
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal, NamedTuple

from harvey import knowledge, links
from harvey.models.knowledge import Disclosure, PatternRule
from harvey.models.mention import MAX_MENTION_TEXT_CHARS

TierName = Literal["green", "yellow", "red"]

_MAX_MATCH_CHARS = 80
_LINK_RE = links.LINK_RE
_HASHTAG_RE = re.compile(r"(?<![\w#&])#\w+")


class Hit(NamedTuple):
    rule_id: str
    kind: str
    match: str
    reason: str


@dataclass(frozen=True)
class GateResult:
    ok: bool
    tier: TierName
    hits: list[Hit] = field(default_factory=list)


@lru_cache(maxsize=None)
def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_MATCH_CHARS else text[-_MAX_MATCH_CHARS:]


def _pattern_hits(text: str, rules: list[PatternRule], kind: str, toggles: dict[str, bool]) -> list[Hit]:
    hits = []
    for rule in rules:
        if rule.unless_toggle and toggles.get(rule.unless_toggle, False):
            continue
        found = _rx(rule.pattern).search(text)
        if found:
            hits.append(Hit(rule.id, kind, _clip(found.group(0)), rule.reason))
    return hits


def _claim_hits(claim_ids: list[str], require_publishable: bool) -> list[Hit]:
    if not claim_ids:
        return [Hit("CLAIMS", "claims", "", "no claim ID, no publish")]
    known = knowledge.claims_by_id()
    publishable = knowledge.publishable_claim_ids() if require_publishable else set()
    hits = []
    for cid in claim_ids:
        if cid not in known:
            hits.append(Hit("CLAIMS", "claims", cid, "unknown claim ID"))
        elif require_publishable and cid not in publishable:
            hits.append(Hit("CLAIMS", "claims", cid, "claim not approved for publishing (PENDING or expired)"))
    return hits


@lru_cache(maxsize=None)
def _medication_rx(names: tuple[str, ...]) -> re.Pattern[str] | None:
    if not names:
        return None
    ordered = sorted(names, key=len, reverse=True)  # longest first: "Wegovy pill" before "Wegovy"
    alternation = "|".join(re.escape(n) for n in ordered)
    return re.compile(rf"(?<!\w)(?:{alternation})(?!\w)", re.IGNORECASE)


def names_medication(text: str) -> bool:
    """True if ``text`` names a generic or brand drug from products.yaml."""
    rx = _medication_rx(tuple(knowledge.medication_names()))
    return bool(rx and rx.search(links.mask_registry_links(text or "")))


def _medication_hits(text: str, forbid: bool) -> list[Hit]:
    rx = _medication_rx(tuple(knowledge.medication_names()))
    found = rx.search(text) if rx else None
    if not found:
        return []
    if forbid:
        return [Hit("R38", "medication_name", found.group(0), "medication or brand name in a reply (pre-certification limit)")]
    return [Hit("R10", "medication_name", found.group(0), "medication name needs compliance review")]


_SENTENCE_END = re.compile(r"[.!?](?=\s|$)|\n")


def first_sentence(text: str) -> str:
    """The reply's first sentence (or first line), stripped."""
    body = (text or "").strip()
    end = _SENTENCE_END.search(body)
    return body[:end.end()].strip() if end else body


@lru_cache(maxsize=None)
def _form_rx(form: str) -> re.Pattern[str]:
    words = [re.escape(w).replace("'", "['’]") for w in form.split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", re.IGNORECASE)


def has_disclosure(text: str, disclosure: Disclosure | None = None) -> bool:
    """The first sentence carries one of the approved disclosure forms."""
    disclosure = disclosure or knowledge.compliance_rules().disclosure
    opening = first_sentence(text)
    return any(_form_rx(f.strip()).search(opening) for f in disclosure.forms if f.strip())


def _disclosure_hits(text: str, disclosure: Disclosure, toggles: dict[str, bool]) -> list[Hit]:
    """R2/R3: a brand reply opens with the disclosure; without it, third-person
    talk about WellPeps reads as astroturfing."""
    if not disclosure.required or has_disclosure(text, disclosure):
        return []
    return [
        Hit("R3", "disclosure", _clip(first_sentence(text)),
            "no approved disclosure in the first sentence (e.g. \"Disclosure: I work with WellPeps\")"),
        *_pattern_hits(text, disclosure.third_person, "disclosure", toggles),
    ]


def _link_hits(text: str, claim_ids: list[str]) -> tuple[list[Hit], list[Hit]]:
    """(red, yellow) for links: registry only, backed by a cited claim, live."""
    by_id = knowledge.claims_by_id()
    backed = {by_id[c].link_id for c in claim_ids if c in by_id and by_id[c].link_id}
    red, yellow = [], []
    for use in links.find_links(text):
        if use.link is None:
            red.append(Hit("R8", "links", _clip(use.raw), "link not in the public links registry (config/links.yaml)"))
        elif use.link.id not in backed:
            red.append(Hit("R8", "links", use.link.id, "link not backed by a cited claim (claims.yaml link_id)"))
        elif not use.link.live:
            yellow.append(Hit("R8", "links", use.link.id, "link not live yet"))
    return red, yellow


def _limit_hits(text: str, platform: str, length: int | None = None) -> list[Hit]:
    limits = knowledge.compliance_rules().limits
    hits = []
    links = _LINK_RE.findall(text)
    if len(links) > limits.max_links:
        hits.append(Hit("R6", "links", ", ".join(links), f"{len(links)} links; max {limits.max_links}"))
    tags = _HASHTAG_RE.findall(text)
    max_tags = limits.max_hashtags_for(platform)
    if len(tags) > max_tags:
        hits.append(Hit("R37", "hashtags", " ".join(tags), f"{len(tags)} hashtags; max {max_tags} on {platform}"))
    max_chars = limits.max_chars_for(platform)
    length = len(text) if length is None else length
    if length > max_chars:
        hits.append(Hit("LIMIT", "length", str(length), f"{length} chars; max {max_chars} on {platform}"))
    return hits


def compliance_filter(
    text: str,
    platform: str,
    claim_ids: list[str],
    *,
    require_publishable: bool = False,
) -> GateResult:
    """Check a draft reply. Deterministic; no Claude calls.

    Pattern scans see at most MAX_MENTION_TEXT_CHARS characters; the length
    limit is checked on the full text (anything that long is red anyway).
    """
    full_text = text
    text = text[:MAX_MENTION_TEXT_CHARS]
    # Our own registry URLs (guide paths, utm_term) are not reply wording.
    scan = links.mask_registry_links(text)
    rules = knowledge.compliance_rules()
    toggles = rules.toggles.model_dump()
    platform = platform.strip().lower()
    forbid_meds = rules.toggles.forbid_medication_names_in_replies

    link_red, link_yellow = _link_hits(text, claim_ids)
    red = [
        *_claim_hits(claim_ids, require_publishable),
        *_disclosure_hits(scan, rules.disclosure, toggles),
        *_pattern_hits(scan, rules.prohibited, "prohibited", toggles),
        *_pattern_hits(scan, rules.patient_confirmation, "patient_confirmation", toggles),
        *(_medication_hits(scan, forbid_meds) if forbid_meds else []),
        *link_red,
        *_limit_hits(text, platform, length=len(full_text)),
    ]
    yellow = [
        *_pattern_hits(scan, rules.yellow, "yellow", toggles),
        *([] if forbid_meds else _medication_hits(scan, forbid=False)),
        *link_yellow,
    ]
    if platform == "tiktok":
        yellow.append(Hit("R34", "platform", "tiktok", "TikTok gets the strictest standard; needs separate approval"))

    tier: TierName = "red" if red else ("yellow" if yellow else "green")
    return GateResult(ok=tier != "red", tier=tier, hits=[*red, *yellow])
