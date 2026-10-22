"""What a rep actually posted (owner request 2026-10-21, R-30).

Pulse never posts. A rep posts by hand (Pulse's approved reply, an edited
version, or their own words) and records it here: the text actually sent, who
sent it, when, and optionally the link to the comment. The record is a
``posted`` audit event (append-only: a second record is a correction, never an
overwrite), with the compliance filter's result on the text that went out, so
a reply that skipped review is still visible to compliance.
"""

from datetime import datetime, timedelta, timezone

from harvey import community_override, engagement
from harvey.compliance import compliance_filter
from harvey.models import AuditEvent, AuditEventType, MentionStatus
from harvey.review import MAX_EDIT_CHARS, ReviewError, _community_context, _http_url, _mention_or_404, _move

MAX_RESPONDER_CHARS = 100
_CLAIMS_RULE = "CLAIMS"
FUTURE_SKEW = timedelta(minutes=10)      # a rep's clock may run a little ahead
MAX_AGE = timedelta(days=366)
# Recording a post moves the mention to "posted" from these statuses; an
# escalated or dropped post keeps its status (the escalation stays open).
_TO_POSTED = (MentionStatus.TRIAGED, MentionStatus.IN_REVIEW, MentionStatus.APPROVED, MentionStatus.REJECTED)
# Not yet triaged, or a draft being written right now: nothing to record against.
_REFUSED = (MentionStatus.NEW, MentionStatus.DRAFTED)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_posted_at(value, now: datetime | None = None) -> datetime:
    """An ISO date-time (an offset or "Z" is converted; none means UTC) as naive
    UTC. Refused when unreadable, in the future, or more than a year ago."""
    now = now or _utcnow()
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except ValueError:
        raise ReviewError(400, "posted_at must be the date and time the reply went out (ISO format)")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    if parsed > now + FUTURE_SKEW:
        raise ReviewError(400, "posted_at is in the future")
    if parsed < now - MAX_AGE:
        raise ReviewError(400, "posted_at is more than a year ago")
    return parsed.replace(microsecond=0)


def _same_text(a: str, b: str) -> bool:
    return " ".join((a or "").split()) == " ".join((b or "").split())


async def record_post(state, mention_id: int, *, text: str, responder: str, posted_at, actor: str,
                      posted_url: str = "", allow_sandbox: bool = False, now: datetime | None = None) -> dict:
    """Record a reply a rep posted by hand. ``actor``: the signed-in user doing
    the recording; ``responder``: the team member who posted it (may differ)."""
    text = (text or "").strip()
    if not text:
        raise ReviewError(400, "the reply text is empty: paste what was posted")
    if len(text) > MAX_EDIT_CHARS:
        raise ReviewError(400, f"the reply is longer than {MAX_EDIT_CHARS} characters")
    responder = " ".join((responder or "").split())
    if not responder or len(responder) > MAX_RESPONDER_CHARS:
        raise ReviewError(400, f"say who posted the reply (at most {MAX_RESPONDER_CHARS} characters)")
    when = parse_posted_at(posted_at, now)
    posted_url = _http_url(posted_url, allow_sandbox)
    mention = await _mention_or_404(state, mention_id)
    if mention.status in _REFUSED:
        raise ReviewError(409, f"this post is still being processed ({mention.status.value}); try again shortly")

    draft = await state.get_latest_draft(mention_id)
    matches = draft is not None and _same_text(text, draft.text)
    claim_ids = list(draft.claim_ids) if matches else []
    gate = compliance_filter(text, mention.platform.value, claim_ids, require_publishable=False,
                             context=_community_context(mention))
    # A reply in a rep's own words cites no claim IDs, so the "no claim ID" rule
    # would flag every one: count only what the text itself says.
    content = [h for h in gate.hits if h.rule_id != _CLAIMS_RULE]
    content_red = [h for h in gate.red_hits if h.rule_id != _CLAIMS_RULE]
    tier = "red" if content_red else ("yellow" if content else "green")
    previous = mention.status
    # The record first: if the status change then fails, the record still exists.
    await state.append_audit(AuditEvent(
        mention_id=mention_id, draft_id=draft.id if draft else None, event=AuditEventType.POSTED, actor=actor,
        claim_ids=claim_ids, final_text=text, permalink=mention.url,
        filter_result={"ok": tier != "red", "tier": tier, "hits": [h._asdict() for h in content],
                       "claim_rule_counted": False},
        verdict={"manual": True, "recorded": True, "responder": responder, "posted_at": when.isoformat(),
                 "posted_url": posted_url, "matches_draft": matches,
                 "community_permission": engagement.public_permission(mention),
                 "community_warning": community_override.warning(mention) or engagement.posting_hold(mention),
                 "previous_status": previous.value},
    ))
    status = previous
    if previous in _TO_POSTED:
        await _move(state, mention_id, MentionStatus.POSTED)
        status = MentionStatus.POSTED
    return {"status": status.value, "text": text, "responder": responder, "posted_at": when.isoformat(), "posted_url": posted_url,
            "matches_draft": matches, "tier": tier, "recorded_by": actor}


def records_from(audit) -> list[dict]:
    """Posted replies for a mention, newest first, from its audit events: the
    ones recorded here and older "mark posted" events (the approved text)."""
    out = []
    for event in audit:
        if event.event is not AuditEventType.POSTED:
            continue
        v = event.verdict or {}
        recorded = bool(v.get("recorded"))
        out.append({
            "text": event.final_text, "responder": v.get("responder") or event.actor,
            "posted_at": v.get("posted_at") or (event.at.isoformat() if event.at else None),
            "posted_url": v.get("posted_url") or "", "recorded_by": event.actor,
            "recorded_at": event.at.isoformat() if event.at else None, "recorded": recorded,
            "matches_draft": v.get("matches_draft", True), "tier": (event.filter_result or {}).get("tier", ""),
        })
    return list(reversed(out))


async def records(state, mention_id: int) -> list[dict]:
    return records_from(await state.list_audit(mention_id))
