"""Advisory community rules (owner decision 2026-10-21, R-30).

In advisory mode (``posting_holds: advisory`` in config/communities.yaml, or
PULSE_COMMUNITY_RULES=advisory) Pulse listens and drafts in every community.
Where a community's rules are unverified, prohibit brand replies, still need a
moderator's permission, or the community belongs to a competitor, the review
desk shows ``warning`` and approval needs the rep's explicit acknowledgement
(``harvey.review.approve(acknowledge_community=True)``). Everything else stays:
safety routing, clinical approval, the compliance filter, approved claims.

``reapply`` re-checks posts that were triaged while the rules were enforced, so
the ones held only for community rules can be drafted.
"""

import logging

from harvey import communities, engagement, protocol
from harvey.models import MentionStatus

logger = logging.getLogger("harvey.community_override")

REAPPLY_LIMIT = 5000
_HELD_RULES = ("unknown", "prohibited", "with_permission")


def warning(mention) -> str:
    """Why posting here goes against (or around) the community's rules, in
    plain words; "" when the rules allow it or the mode is enforced."""
    if mention is None or not communities.rules_advisory():
        return ""
    owner = engagement.competitor_community(mention)
    if owner:
        return (f"This is {owner}'s own community. WellPeps' rules treat it as listen-only, so posting here "
                "is your call.")
    status = communities.status_for_url(mention.url)
    if status.participation == "prohibited":
        return ("This community prohibits brand replies. A reply here goes against its rules and may be "
                "removed, or get the account banned.")
    if status.participation == "unknown":
        return ("This community's rules are unverified. Check them before you post, and record them in "
                "the Communities page.")
    if status.participation == "with_permission" and not status.permission_obtained:
        return "This community needs a moderator's permission for brand replies, and none is recorded."
    return ""


def _without_competitor_gate(triage):
    reason = triage.urgency_reason or ""
    gate = engagement.GATE_COMPETITOR_COMMUNITY
    if gate not in reason:
        return triage
    cleaned = reason.replace(f"; {gate}", "").replace(gate, "").strip().strip(";").strip()
    return triage.model_copy(update={"urgency_reason": cleaned})


def _held_for_rules(triage) -> bool:
    record = triage.protocol or {}
    return (triage.protocol_decision or "") in protocol.NO_DRAFT_DECISIONS \
        and (record.get("rules") or "") in _HELD_RULES


async def reapply(state) -> int:
    """Release triaged posts held only by community rules (advisory mode only).
    Recomputes the protocol for posts it held for unknown / prohibited rules and
    drops the competitor-community gate. Returns how many triage rows changed;
    the triage table is not the audit log, so this is an update, not a
    correction event. Idempotent."""
    if not communities.rules_advisory():
        return 0
    await communities.ensure_overlay(state)       # rules recorded in the dashboard
    changed = 0
    for mention in await state.list_mentions(status=MentionStatus.TRIAGED, limit=REAPPLY_LIMIT):
        triage = await state.get_triage(mention.id)
        if triage is None:
            continue
        updated = _without_competitor_gate(triage)
        if _held_for_rules(updated):
            prior = bool((updated.protocol or {}).get("prior_clinical_in_thread"))
            updated = protocol.applied(updated, mention, prior_clinical=prior)
        if updated.urgency_reason != triage.urgency_reason:
            # The competitor gate came first and hid the later ones (not ours,
            # not worth it, unverified fact): evaluate them now.
            updated = engagement.with_review_gate(updated, mention)
        if updated != triage:
            await state.save_triage(updated)
            changed += 1
    if changed:
        logger.info("advisory community rules: %d triaged post(s) re-checked", changed)
    return changed
