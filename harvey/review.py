"""Human review desk: read models for the dashboard and every human action
on a mention (Phase 7).

Every action is attributed to the signed-in user's email in the append-only
audit log. Nothing here posts anywhere: "copied" and "mark posted" only
record that a human copied an approved reply and posted it by hand.

Approval gate ("no claim ID, no publish"): the mention is ``in_review``, the
latest draft has text, re-running the deterministic compliance filter is not
red, the draft cites at least one claim, and (when
``review.require_publishable_claims``) every cited claim is signed off.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

from harvey import knowledge
from harvey.compliance import compliance_filter
from harvey.config import ESCALATION_KINDS
from harvey.escalation import SEVERE_KINDS, escalate
from harvey.models import (
    AuditEvent,
    AuditEventType,
    Category,
    Draft,
    MentionStatus,
    Platform,
    ReviewVerdict,
    Triage,
    Urgency,
)

logger = logging.getLogger("harvey.review")

PREVIEW_CHARS = 2000
MAX_LIMIT = 500
MAX_EDIT_CHARS = 10000
MAX_CLAIMS = 20
MAX_REASON_CHARS = 1000
NOT_RERUN = "edited by a human; adversarial reviewer not re-run"
MANUAL_REASON = "manual"
# Statuses a human may escalate from (review desk / feed).
ESCALATABLE = frozenset({MentionStatus.TRIAGED, MentionStatus.DRAFTED, MentionStatus.IN_REVIEW})
# Category a manual escalation kind stands for (escalation_kind inverts it).
_CATEGORY_FOR_KIND = {
    "adverse_event": Category.ADVERSE_EVENT,
    "legal": Category.LEGAL_REGULATORY,
    "privacy": Category.PRIVACY,
    "billing_fraud": Category.BILLING_FRAUD,
    "viral_negative": Category.COMPLAINT,
}


class ReviewError(Exception):
    """An action that can't happen; ``status`` is the HTTP status to return."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace(" ", "T"))
    except ValueError:
        return None


async def _rows(state, sql: str, params: tuple = ()) -> list[dict]:
    async with state.connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


# --- Reads ---------------------------------------------------------------------------------------


async def urgent(state, now: datetime | None = None) -> dict:
    """Open escalations with their mention: breached first, then soonest SLA."""
    now = now or _utcnow()
    rows = await _rows(state, (
        "SELECT e.id, e.mention_id, e.kind, e.owner, e.sla_due_at, e.breached, e.notified_at, "
        "e.created_at, m.platform, m.url AS permalink, m.status, t.category, t.urgency "
        "FROM escalations e JOIN mentions m ON m.id = e.mention_id "
        "LEFT JOIN triage t ON t.mention_id = e.mention_id WHERE e.acked_at IS NULL"
    ))
    items = []
    for row in rows:
        due = _parse_ts(row["sla_due_at"])
        breached = bool(row["breached"]) or (due is not None and due <= now)
        items.append({
            **{k: row[k] for k in ("id", "mention_id", "kind", "owner", "sla_due_at", "platform",
                                   "permalink", "status", "category", "urgency", "created_at")},
            "breached": breached,
            "notified": row["notified_at"] is not None,
            "seconds_left": int((due - now).total_seconds()) if due else None,
        })
    items.sort(key=lambda r: (not r["breached"], r["seconds_left"] is None,
                              r["seconds_left"] or 0, r["id"]))
    return {"now": now.isoformat(), "items": items}


def _enum(value: str | None, enum, name: str) -> str | None:
    if value in (None, ""):
        return None
    try:
        return enum(value).value
    except ValueError:
        raise ReviewError(400, f"unknown {name} '{value}'")


async def feed(state, *, status=None, platform=None, competitor=None, product=None, drug=None,
               category=None, urgency=None, q=None, limit: int = 100, offset: int = 0) -> dict:
    """Paginated mentions, newest first, with triage tags and a text preview."""
    where, params = [], []
    for column, value in (
        ("m.status", _enum(status, MentionStatus, "status")),
        ("m.platform", _enum(platform, Platform, "platform")),
        ("t.category", _enum(category, Category, "category")),
        ("t.urgency", _enum(urgency, Urgency, "urgency")),
        ("t.competitor", competitor or None),
        ("t.product", product or None),
        ("t.drug", drug or None),
    ):
        if value is not None:
            where.append(f"{column} = ?")
            params.append(value)
    if q and q.strip():
        where.append("(instr(lower(m.text), lower(?)) > 0 OR instr(lower(m.title), lower(?)) > 0)")
        params += [q.strip()[:200]] * 2
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    base = f"FROM mentions m LEFT JOIN triage t ON t.mention_id = m.id {clause}"
    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))
    total = (await _rows(state, f"SELECT COUNT(*) AS n {base}", tuple(params)))[0]["n"]
    rows = await _rows(state, (
        "SELECT m.id, m.platform, m.external_id, m.url, m.author_handle, m.title, "
        f"substr(m.text, 1, {PREVIEW_CHARS}) AS text, length(m.text) > {PREVIEW_CHARS} AS text_truncated, "
        "m.lang, m.posted_at, m.collected_at, m.owned_channel, m.status, "
        "t.category, t.urgency, t.competitor, t.product, t.drug, t.subject_type, t.sentiment, "
        f"t.relevant {base} ORDER BY m.collected_at DESC, m.id DESC LIMIT ? OFFSET ?"
    ), (*params, limit, offset))
    for row in rows:
        row["text_truncated"] = bool(row["text_truncated"])
        row["owned_channel"] = bool(row["owned_channel"])
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


def _claim_info(claim_ids) -> dict:
    by_id = knowledge.claims_by_id()
    publishable = knowledge.publishable_claim_ids()
    return {cid: {"text": by_id[cid].text, "tier": by_id[cid].tier, "publishable": cid in publishable,
                  "known": True} if cid in by_id else {"text": "", "tier": "", "publishable": False,
                                                        "known": False}
            for cid in claim_ids}


def all_claims() -> list[dict]:
    publishable = knowledge.publishable_claim_ids()
    return [{"id": c.id, "text": c.text, "tier": c.tier, "products": list(c.products),
             "publishable": c.id in publishable} for c in knowledge.claims()]


async def detail(state, mention_id: int, config) -> dict:
    mention = await state.get_mention(mention_id)
    if mention is None:
        raise ReviewError(404, "mention not found")
    triage = await state.get_triage(mention_id)
    drafts = await state.list_drafts(mention_id)
    escalations = await _rows(state, "SELECT * FROM escalations WHERE mention_id = ? ORDER BY id",
                              (mention_id,))
    for esc in escalations:
        esc["breached"] = bool(esc["breached"])
    audit = await state.list_audit(mention_id)
    claim_ids = list(dict.fromkeys(cid for d in drafts for cid in d.claim_ids))
    blockers = approval_blockers(mention.status, drafts[0] if drafts else None,
                                 mention.platform.value, config)
    return {
        "mention": mention.model_dump(mode="json"),
        "triage": triage.model_dump(mode="json") if triage else None,
        "latest_draft": drafts[0].model_dump(mode="json") if drafts else None,
        "drafts": [d.model_dump(mode="json") for d in drafts],
        "escalations": escalations,
        "audit": [e.model_dump(mode="json") for e in audit],
        "claims": _claim_info(claim_ids),
        "approval": {"allowed": not blockers, "blockers": blockers},
    }


# --- Approval gate ------------------------------------------------------------------------------------


def approval_blockers(status: MentionStatus, draft: Draft | None, platform: str, config) -> list[str]:
    """Human-readable reasons approval is refused; empty when it may proceed."""
    if status is not MentionStatus.IN_REVIEW:
        return [f"only a mention waiting in review can be approved (this one is {status.value})"]
    if draft is None or not draft.text.strip():
        return ["there is no draft reply to approve; write one with Save edit first"]
    blockers = []
    gate = compliance_filter(draft.text, platform, draft.claim_ids, require_publishable=False)
    if gate.tier == "red":
        reasons = "; ".join(f"{h.rule_id}: {h.reason}" for h in gate.hits if h.rule_id != "CLAIMS")
        blockers.append("compliance filter is red" + (f" ({reasons})" if reasons else ""))
    if not draft.claim_ids:
        blockers.append("the draft cites no approved claim IDs (no claim ID, no publish)")
    elif config.review.require_publishable_claims:
        publishable = knowledge.publishable_claim_ids()
        pending = [cid for cid in draft.claim_ids if cid not in publishable]
        if pending:
            blockers.append("claims not approved for publishing (PENDING sign-off or expired): "
                            + ", ".join(pending))
    return blockers


# --- Actions ---------------------------------------------------------------------------------------------


async def _mention_or_404(state, mention_id: int):
    mention = await state.get_mention(mention_id)
    if mention is None:
        raise ReviewError(404, "mention not found")
    return mention


async def _move(state, mention_id: int, target: MentionStatus) -> None:
    try:
        await state.set_mention_status(mention_id, target)
    except ValueError as exc:
        raise ReviewError(409, str(exc))


@dataclass
class EditOutcome:
    draft_id: int
    version: int
    tier: str
    verdict: str


async def _review_edit(reviewer, text, claim_ids, mention, gate, config):
    if gate.tier == "red":
        return ReviewVerdict.REJECT, [f"{h.rule_id}: {h.reason}" for h in gate.hits], "skipped: filter red"
    if not (config.review.rerun_reviewer_on_edit and reviewer is not None):
        return ReviewVerdict.NEEDS_HUMAN, [NOT_RERUN], "not re-run"
    by_id = knowledge.claims_by_id()
    result = await reviewer.review(text, mention.platform.value, mention,
                                   [by_id[c] for c in claim_ids if c in by_id])
    reasons = [f"{r['rule_id']}: {r['explanation']}" for r in result.reasons]
    return result.verdict, reasons, "reviewer"


async def edit(state, mention_id: int, text: str, claim_ids: list[str], actor: str, config,
               reviewer=None) -> EditOutcome:
    """A human's draft version: re-filtered (and optionally re-reviewed)."""
    text = (text or "").strip()
    if not text:
        raise ReviewError(400, "the reply text is empty")
    if len(text) > MAX_EDIT_CHARS:
        raise ReviewError(400, f"the reply is longer than {MAX_EDIT_CHARS} characters")
    claim_ids = list(dict.fromkeys(c.strip() for c in claim_ids if isinstance(c, str) and c.strip()))
    if len(claim_ids) > MAX_CLAIMS:
        raise ReviewError(400, f"at most {MAX_CLAIMS} claim IDs")
    mention = await _mention_or_404(state, mention_id)
    if mention.status not in (MentionStatus.IN_REVIEW, MentionStatus.APPROVED):
        raise ReviewError(409, f"only drafts in review can be edited (status is {mention.status.value})")
    gate = compliance_filter(text, mention.platform.value, claim_ids, require_publishable=False)
    verdict, reasons, reviewed_by = await _review_edit(reviewer, text, claim_ids, mention, gate, config)
    hits = [f"{h.rule_id}: {h.reason}" + (f" [{h.match}]" if h.match else "") for h in gate.hits]
    draft_id = await state.add_draft(Draft(
        mention_id=mention_id, text=text, claim_ids=claim_ids, model=f"human:{actor}",
        filter_ok=gate.ok, filter_hits=hits, review_verdict=verdict, review_reasons=reasons,
        tier=gate.tier,
    ))
    if mention.status is MentionStatus.APPROVED:
        await _move(state, mention_id, MentionStatus.IN_REVIEW)  # an edit voids the approval
    await state.append_audit(AuditEvent(
        mention_id=mention_id, draft_id=draft_id, event=AuditEventType.EDITED, actor=actor,
        claim_ids=claim_ids, final_text=text, permalink=mention.url,
        filter_result={"ok": gate.ok, "tier": gate.tier, "hits": [h._asdict() for h in gate.hits]},
        verdict={"verdict": verdict.value, "reasons": reasons, "reviewer": reviewed_by,
                 "previous_status": mention.status.value},
    ))
    draft = await state.get_latest_draft(mention_id)
    return EditOutcome(draft_id=draft_id, version=draft.version, tier=gate.tier, verdict=verdict.value)


async def approve(state, mention_id: int, actor: str, config, draft_id: int | None = None) -> None:
    mention = await _mention_or_404(state, mention_id)
    draft = await state.get_latest_draft(mention_id)
    if draft_id is not None and (draft is None or draft.id != draft_id):
        raise ReviewError(409, "the draft changed since you opened it; reload and review the latest version")
    blockers = approval_blockers(mention.status, draft, mention.platform.value, config)
    if blockers:
        raise ReviewError(409, "approval refused: " + "; ".join(blockers))
    await _move(state, mention_id, MentionStatus.APPROVED)
    gate = compliance_filter(draft.text, mention.platform.value, draft.claim_ids,
                             require_publishable=config.review.require_publishable_claims)
    await state.append_audit(AuditEvent(
        mention_id=mention_id, draft_id=draft.id, event=AuditEventType.APPROVED, actor=actor,
        claim_ids=draft.claim_ids, final_text=draft.text, permalink=mention.url,
        filter_result={"ok": gate.ok, "tier": gate.tier, "hits": [h._asdict() for h in gate.hits]},
        verdict={"version": draft.version, "review_verdict": getattr(draft.review_verdict, "value", None)},
    ))


async def reject(state, mention_id: int, reason: str, actor: str) -> None:
    reason = (reason or "").strip()
    if not reason:
        raise ReviewError(400, "a reason is required to reject")
    mention = await _mention_or_404(state, mention_id)
    if mention.status is not MentionStatus.IN_REVIEW:
        raise ReviewError(409, f"only a mention in review can be rejected (status is {mention.status.value})")
    draft = await state.get_latest_draft(mention_id)
    await _move(state, mention_id, MentionStatus.REJECTED)
    await state.append_audit(AuditEvent(
        mention_id=mention_id, draft_id=draft.id if draft else None, event=AuditEventType.REJECTED,
        actor=actor, verdict={"reason": reason[:MAX_REASON_CHARS]}, permalink=mention.url,
    ))


async def copied(state, mention_id: int, actor: str) -> None:
    """Record that a human copied the approved reply to post it by hand."""
    mention = await _mention_or_404(state, mention_id)
    if mention.status not in (MentionStatus.APPROVED, MentionStatus.POSTED):
        raise ReviewError(409, "only an approved reply can be copied for posting")
    draft = await state.get_latest_draft(mention_id)
    await state.append_audit(AuditEvent(
        mention_id=mention_id, draft_id=draft.id if draft else None, event=AuditEventType.COPIED,
        actor=actor, claim_ids=draft.claim_ids if draft else [],
        final_text=draft.text if draft else "", permalink=mention.url,
    ))


def _http_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if not url.lower().startswith(("http://", "https://")) or len(url) > 2000:
        raise ReviewError(400, "posted_url must be an http(s) link")
    return url


async def mark_posted(state, mention_id: int, actor: str, posted_url: str = "") -> None:
    posted_url = _http_url(posted_url)
    mention = await _mention_or_404(state, mention_id)
    if mention.status is not MentionStatus.APPROVED:
        raise ReviewError(409, f"only an approved reply can be marked posted (status is {mention.status.value})")
    draft = await state.get_latest_draft(mention_id)
    await _move(state, mention_id, MentionStatus.POSTED)
    await state.append_audit(AuditEvent(
        mention_id=mention_id, draft_id=draft.id if draft else None, event=AuditEventType.POSTED,
        actor=actor, claim_ids=draft.claim_ids if draft else [],
        final_text=draft.text if draft else "", permalink=mention.url,
        verdict={"manual": True, "posted_url": posted_url},
    ))


async def manual_escalate(state, notifier, mention_id: int, kind: str, actor: str, config):
    """A human escalates from the desk; pages through harvey.escalation.escalate."""
    if kind not in ESCALATION_KINDS:
        raise ReviewError(400, f"unknown escalation kind '{kind}'; use {', '.join(ESCALATION_KINDS)}")
    mention = await _mention_or_404(state, mention_id)
    if mention.status not in ESCALATABLE:
        raise ReviewError(409, f"cannot escalate a mention that is {mention.status.value}")
    triage = await state.get_triage(mention_id) or Triage(mention_id=mention_id)
    signal = triage.model_copy(update={
        "relevant": True, "category": _CATEGORY_FOR_KIND[kind], "urgency": Urgency.URGENT,
        "subject_type": "wellpeps" if kind == "viral_negative" else triage.subject_type,
        "urgency_reason": f"{MANUAL_REASON}: escalated by {actor}",
    })
    escalation = await escalate(state, notifier, mention, signal, config)
    if kind in SEVERE_KINDS:
        await _move(state, mention_id, MentionStatus.ESCALATED)
    await state.append_audit(AuditEvent(
        mention_id=mention_id, event=AuditEventType.ESCALATED, actor=actor, permalink=mention.url,
        verdict={"action": "manual", "kind": kind, "escalation_id": escalation.id,
                 "previous_status": mention.status.value},
    ))
    return escalation
