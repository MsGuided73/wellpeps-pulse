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

Rules from WellPeps' community engagement guidelines (2026-10-06,
docs/RULES-OF-ENGAGEMENT.md): R40-R42 are patterns in compliance_rules.yaml;
these are checked here in code:
- R42  a cited claim with an open FINALIZE item -> yellow (approval is blocked
       separately in harvey/review.py).
- R43  a "naked" link (fewer than ``link_rules.min_words_with_link`` words
       besides the link and the disclosure sentence) -> red; the same link
       already used in the community within the window -> yellow (context).
- R44  community rules (config/communities.yaml, via ``ReplyContext``):
       prohibited -> red; unknown -> yellow and any link red;
       with_permission without permission -> yellow, any link or promotion red;
       links_allowed false -> any link red.
- R47  a Brand Ambassador / partner disclosure form -> yellow (Pulse drafts as
       the official account or an identified employee).
- R48  the reply names a competitor -> red (Competitor Mentions and Provider
       Switching Protocol §1, §7: respond to the unmet need rather than repeat
       or attack the named provider). R49 (adopting a competitor accusation,
       inferring clinical failure, switching / response-time / lab promises,
       DM migration, vote manipulation) are patterns in compliance_rules.yaml.

The 80/20 guideline is a planning metric, not a per-reply quota (Protocol §1;
Operations Manual §2.1): no reply is flagged on it (harvey/engagement.py
reports it on the review desk and the Analytics card).
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal, NamedTuple

from harvey import knowledge, links, promotion
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
class ReplyContext:
    """Where the reply would be posted (built by harvey/engagement.py from
    config/communities.yaml and the reply history). The default is "not in a
    registered community and no history", which adds no hits."""

    community_id: str = ""
    # allowed | with_permission | prohibited | unknown | none (no community in the permalink)
    participation: str = "none"
    permission_obtained: bool = False
    links_allowed: bool | None = None          # None = unknown
    repeated_link_ids: frozenset[str] = frozenset()

    @property
    def link_forbidden(self) -> str:
        """Why a link may not be used here ("" = it may)."""
        if self.participation == "prohibited":
            return "brand participation is prohibited"
        if self.participation == "unknown":
            return "community rules are unverified"
        if self.participation == "with_permission" and not self.permission_obtained:
            return "admin/moderator permission has not been obtained"
        if self.links_allowed is False:
            return "the community does not allow links"
        return ""

    @property
    def promotion_forbidden(self) -> str:
        if self.participation == "prohibited":
            return "brand participation is prohibited"
        if self.participation == "with_permission" and not self.permission_obtained:
            return "admin/moderator permission has not been obtained"
        return ""


@dataclass(frozen=True)
class GateResult:
    ok: bool
    tier: TierName
    hits: list[Hit] = field(default_factory=list)
    # The hits that made the result red (a subset of ``hits``). Hit ``kind``
    # names the rule family, not the tier, so callers that need "what blocks"
    # read this instead of guessing from kinds.
    red_hits: tuple[Hit, ...] = ()


@lru_cache(maxsize=None)
def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_MATCH_CHARS else text[-_MAX_MATCH_CHARS:]


def _first_match(text: str, rule: PatternRule) -> re.Match[str] | None:
    """The first match of ``rule`` that no ``except_pattern`` match covers."""
    exempt = [m.span() for m in _rx(rule.except_pattern).finditer(text)] if rule.except_pattern else []
    for found in _rx(rule.pattern).finditer(text):
        start, end = found.span()
        if not any(a <= start and end <= b for a, b in exempt):
            return found
    return None


def _pattern_hits(text: str, rules: list[PatternRule], kind: str, toggles: dict[str, bool]) -> list[Hit]:
    hits = []
    for rule in rules:
        if rule.unless_toggle and toggles.get(rule.unless_toggle, False):
            continue
        found = _first_match(text, rule)
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


# Ordinary English words that are also competitor names or aliases: a
# capitalised one at the start of a sentence is not read as the competitor.
_COMMON_WORDS = frozenset({"found", "keeps", "sequence", "hers", "eden", "rex", "hone", "fridays", "emerge",
                           "willow", "sesame", "maximus", "fountain", "elysium"})


@lru_cache(maxsize=None)
def _competitor_rx(names: tuple[str, ...]) -> re.Pattern[str] | None:
    # Case-sensitive: "Ro" the company, not "ro" inside a word; "Found", not "found".
    terms = sorted({n.strip() for n in names if n.strip() and not n.strip().islower()}, key=len, reverse=True)
    if not terms:
        return None
    return re.compile(r"(?<![\w.@/])(?:" + "|".join(re.escape(t) for t in terms) + r")(?!\w)")


def _competitor_hits(text: str) -> list[Hit]:
    """R48: the reply names a competitor (Protocol §1, §7)."""
    comps = knowledge.competitors().all()
    rx = _competitor_rx(tuple(t for c in comps for t in (c.name, *c.aliases)))
    if rx is None:
        return []
    scan = links.mask_registry_links(text)
    for found in rx.finditer(scan):
        word = found.group(0)
        before = scan[:found.start()].rstrip()
        sentence_start = not before or before[-1] in ".!?\n"
        if word.lower() in _COMMON_WORDS and sentence_start:
            continue
        return [Hit("R48", "competitor", _clip(word),
                    "names a competitor: respond to the unmet need rather than repeat or attack the named "
                    "provider (Competitor/Switching Protocol §1, §7)")]
    return []


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


def _forms_in(opening: str, forms) -> bool:
    return any(_form_rx(f.strip()).search(opening) for f in forms if f.strip())


def has_disclosure(text: str, disclosure: Disclosure | None = None) -> bool:
    """The first sentence carries one of the approved disclosure forms
    (an influencer / ambassador form counts too; see R47)."""
    disclosure = disclosure or knowledge.compliance_rules().disclosure
    opening = first_sentence(text)
    return _forms_in(opening, disclosure.forms) or _forms_in(opening, disclosure.influencer_forms)


def _influencer_form_hits(text: str, disclosure: Disclosure) -> list[Hit]:
    opening = first_sentence(text)
    if disclosure.influencer_forms and _forms_in(opening, disclosure.influencer_forms) \
            and not _forms_in(opening, disclosure.forms):
        return [Hit("R47", "disclosure", _clip(opening),
                    "Brand Ambassador / partner disclosure form: Pulse drafts as the official account "
                    "or an identified employee (\"I work with WellPeps.\")")]
    return []


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


_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’-]*")


def naked_link(text: str, disclosure: Disclosure | None = None, min_words: int | None = None) -> bool:
    """R43: the reply carries a link but little else (Guide §14: "provide a
    useful answer rather than posting a naked link"). The disclosure sentence
    and the link itself don't count as an answer."""
    if not _LINK_RE.search(text or ""):
        return False
    disclosure = disclosure or knowledge.compliance_rules().disclosure
    minimum = min_words or knowledge.engagement_guide().link_rules.min_words_with_link
    body = _LINK_RE.sub(" ", text)
    opening = first_sentence(body)
    if opening and (_forms_in(opening, disclosure.forms) or _forms_in(opening, disclosure.influencer_forms)):
        body = body.strip()[len(opening):]
    return len(_WORD_RE.findall(body)) < minimum


def _finalize_hits(claim_ids: list[str]) -> list[Hit]:
    return [Hit("R42", "finalize", cid, f"FINALIZE: {missing}")
            for cid, missing in knowledge.unresolved_finalize(claim_ids)]


def _context_hits(text: str, claim_ids: list[str], context: "ReplyContext | None") -> tuple[list[Hit], list[Hit]]:
    """(red, yellow) from the community registry and the reply history."""
    if context is None:
        return [], []
    red: list[Hit] = []
    yellow: list[Hit] = []
    where = context.community_id or "this community"
    has_link = bool(_LINK_RE.search(text))
    if context.participation == "prohibited":
        red.append(Hit("R44", "community", where, "brand participation is prohibited in this community; don't reply"))
    elif context.participation == "unknown":
        yellow.append(Hit("R44", "community", where,
                          "community rules unverified: check them and record them in config/communities.yaml"))
    elif context.participation == "with_permission" and not context.permission_obtained:
        yellow.append(Hit("R44", "community", where,
                          "the community requires admin/moderator permission, which has not been obtained"))
    link_reason = context.link_forbidden
    if has_link and link_reason and context.participation != "prohibited":
        red.append(Hit("R44", "community", where, f"no link here: {link_reason}"))
    promo_reason = context.promotion_forbidden
    if promo_reason and context.participation != "prohibited":
        elements = [e for e in promotion.promotional_elements(text, claim_ids) if e != "link"]
        if elements:
            red.append(Hit("R44", "community", ", ".join(elements)[:_MAX_MATCH_CHARS],
                           f"no promotion here: {promo_reason}"))
    for use in links.find_links(text):
        if use.link is not None and use.link.id in context.repeated_link_ids:
            yellow.append(Hit("R43", "links", use.link.id,
                              "repeated link: already shared in this community within the repeat window"))
            break
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
    context: ReplyContext | None = None,
) -> GateResult:
    """Check a draft reply. Deterministic; no Claude calls.

    ``context``: where the reply would be posted (community rules, repeated
    links, 80/20 share); see ``ReplyContext``. None checks the text only.

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
    context_red, context_yellow = _context_hits(text, claim_ids, context)
    naked = ([Hit("R43", "links", "", "naked link: answer the question in words first; a link alone is not a reply")]
             if naked_link(text, rules.disclosure) else [])
    red = [
        *_claim_hits(claim_ids, require_publishable),
        *_disclosure_hits(scan, rules.disclosure, toggles),
        *_pattern_hits(scan, rules.prohibited, "prohibited", toggles),
        *_pattern_hits(scan, rules.patient_confirmation, "patient_confirmation", toggles),
        *(_medication_hits(scan, forbid_meds) if forbid_meds else []),
        *link_red,
        *naked,
        *context_red,
        *_competitor_hits(scan),
        *_limit_hits(text, platform, length=len(full_text)),
    ]
    yellow = [
        *_pattern_hits(scan, rules.yellow, "yellow", toggles),
        *([] if forbid_meds else _medication_hits(scan, forbid=False)),
        *link_yellow,
        *_finalize_hits(claim_ids),
        *_influencer_form_hits(scan, rules.disclosure),
        *context_yellow,
    ]
    if platform == "tiktok":
        yellow.append(Hit("R34", "platform", "tiktok", "TikTok gets the strictest standard; needs separate approval"))

    tier: TierName = "red" if red else ("yellow" if yellow else "green")
    return GateResult(ok=tier != "red", tier=tier, hits=[*red, *yellow], red_hits=tuple(red))
