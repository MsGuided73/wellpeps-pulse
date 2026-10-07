"""Education vs promotion (the 80/20 rule), deterministic.

WellPeps' guidelines ask for roughly 80% education and at most 20% promotion
in community activity, and zero promotion where a community prohibits it
(Community Groups and Forums "80/20 Rule"; Compliance Training Module 1;
Operations Manual §2.1). Pulse classifies each reply by what it contains:

A reply is PROMOTIONAL when it has any of
- a link (Pulse may only link to config/links.yaml, the email-gated Smart
  Patient Guide pages, i.e. lead capture), or any other URL;
- a cited claim marked ``promotional`` in config/claims.yaml (pricing,
  guides, assessment, "share the link");
- a price ("$" followed by a digit);
- a call to action from ``eighty_twenty.promotion_signals`` in
  config/engagement_guide.yaml ("get started", "sign up", "free guide", ...).

Everything else is EDUCATION. The disclosure sentence and the WellPeps process
description are education ([OM] §2.1 lists the telehealth process under the
80%). No model calls.
"""

import re
from functools import lru_cache

from harvey import knowledge, links

PRICE_RE = re.compile(r"\$\s?\d")


@lru_cache(maxsize=None)
def _signal_rx(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


def promotional_elements(text: str, claim_ids=()) -> list[str]:
    """What makes ``text`` promotional (empty list = education)."""
    text = text or ""
    found: list[str] = []
    if links.LINK_RE.search(text):
        found.append("link")
    by_id = knowledge.claims_by_id()
    for cid in claim_ids or ():
        claim = by_id.get(cid)
        if claim is not None and claim.promotional:
            found.append(f"claim:{cid}")
    scan = links.mask_registry_links(text)
    if PRICE_RE.search(scan):
        found.append("price")
    signals = _signal_rx(tuple(knowledge.engagement_guide().eighty_twenty.promotion_signals))
    for rx in signals:
        match = rx.search(scan)
        if match and not PRICE_RE.fullmatch(match.group(0)):
            found.append(f"cta:{match.group(0).strip()[:40]}")
            break
    return found


def is_promotional(text: str, claim_ids=()) -> bool:
    return bool(promotional_elements(text, claim_ids))


def kind(text: str, claim_ids=()) -> str:
    """"promotion" or "education"."""
    return "promotion" if is_promotional(text, claim_ids) else "education"
