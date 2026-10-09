"""Human review desk: read models for the dashboard and every human action
on a mention (Phase 7).

Every action is attributed to the signed-in user's email in the append-only
audit log. Nothing here posts anywhere: "copied" and "mark posted" only
record that a human copied an approved reply and posted it by hand.

Approval gate ("no claim ID, no publish"): the mention is ``in_review``, the
latest draft has text, re-running the deterministic compliance filter is not
red, no registry link in the text is still ``live: false`` (config/links.yaml),
the draft cites at least one claim, and (when
``review.require_publishable_claims``) every cited claim is signed off.

Rules of engagement (docs/RULES-OF-ENGAGEMENT.md) add: the filter runs with
the community's rules (config/communities.yaml); no cited claim may have an
open FINALIZE item; the competitor / switching protocol's live decision must
allow a public reply (not HOLD, DO NOT ENGAGE or MONITOR ONLY, recomputed
with the current config); and an adverse-event or emergency reply needs a
clinical approver (role clinical or admin; ``approve_clinical``).
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from harvey import auth, communities, engagement, explain, knowledge, links, protocol
from harvey.compliance import ReplyContext, compliance_filter
from harvey.config import ESCALATION_KINDS
from harvey.escalation import SAFETY_WATCH, SEVERE_KINDS, escalate
from harvey.sandbox import urls as sandbox_urls
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
    "safety_watch": Category.ADVERSE_EVENT,
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
        async with db.execute(sql, params) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


# --- Reads ---------------------------------------------------------------------------------------


async def urgent(state, now: datetime | None = None) -> dict:
    """Open escalations with their mention: breached first, then soonest SLA.

    Each item carries what the Urgent card shows: a short excerpt of the post,
    its title/author/times, and ``why`` (harvey.explain). Dashboard only.
    """
    now = now or _utcnow()
    rows = await _rows(state, (
        "SELECT e.id, e.mention_id, e.kind, e.owner, e.sla_due_at, e.breached, e.notified_at, "
        "e.created_at, m.platform, m.url AS permalink, m.status, m.text, m.title, m.posted_at, "
        "m.collected_at, m.author_handle, t.category, t.urgency, t.urgency_reason "
        "FROM escalations e JOIN mentions m ON m.id = e.mention_id "
        "LEFT JOIN triage t ON t.mention_id = e.mention_id WHERE e.acked_at IS NULL"
    ))
    evidence, manual_by = await _escalation_context(state, [r["mention_id"] for r in rows])
    items = []
    for row in rows:
        due = _parse_ts(row["sla_due_at"])
        breached = bool(row["breached"]) or (due is not None and due <= now)
        triage = {k: row[k] for k in ("urgency_reason", "category", "urgency")}             if row["urgency"] is not None else None
        items.append({
            **{k: row[k] for k in ("id", "mention_id", "kind", "owner", "sla_due_at", "platform",
                                   "permalink", "status", "category", "urgency", "created_at",
                                   "title", "posted_at", "collected_at", "author_handle")},
            "excerpt": explain.excerpt(row["text"]),
            "why": explain.urgency_explanation(row["text"] or "", triage,
                                               evidence=evidence.get(row["mention_id"], ""),
                                               manual_by=manual_by.get(row["id"])),
            "breached": breached,
            "notified": row["notified_at"] is not None,
            "notified_at": row["notified_at"],
            "seconds_left": int((due - now).total_seconds()) if due else None,
        })
    items.sort(key=lambda r: (not r["breached"], r["seconds_left"] is None,
                              r["seconds_left"] or 0, r["id"]))
    return {"now": now.isoformat(), "items": items}


def _json(value) -> dict:
    try:
        data = json.loads(value) if isinstance(value, str) else (value or {})
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def _escalation_context(state, mention_ids: list[int]) -> tuple[dict, dict]:
    """(mention_id -> latest safety-screen evidence, escalation_id -> manual actor)
    from the audit log, in one query."""
    if not mention_ids:
        return {}, {}
    marks = ", ".join("?" for _ in mention_ids)
    rows = await _rows(state, (
        f"SELECT mention_id, event, actor, verdict_json FROM audit_log WHERE mention_id IN ({marks}) "
        "AND event IN ('triaged', 'escalated') ORDER BY id"
    ), tuple(int(m) for m in mention_ids))
    evidence, manual_by = {}, {}
    for row in rows:
        verdict = _json(row["verdict_json"])
        if row["event"] == AuditEventType.TRIAGED.value:
            quote = (verdict.get("safety_screen") or {}).get("evidence")
            if quote:
                evidence[row["mention_id"]] = str(quote)
        elif verdict.get("action") == "manual" and verdict.get("escalation_id") is not None:
            manual_by[verdict["escalation_id"]] = row["actor"]
    return evidence, manual_by


def _enum(value: str | None, enum, name: str) -> str | None:
    if value in (None, ""):
        return None
    try:
        return enum(value).value
    except ValueError:
        raise ReviewError(400, f"unknown {name} '{value}'")


FEED_SORTS = {"newest": "DESC", "oldest": "ASC"}


async def feed(state, *, status=None, platform=None, competitor=None, product=None, drug=None,
               category=None, urgency=None, q=None, limit: int = 100, offset: int = 0,
               sort: str = "newest") -> dict:
    """Paginated mentions (newest collected first by default), with triage tags and a text preview."""
    if sort not in FEED_SORTS:
        raise ReviewError(400, f"unknown sort '{str(sort)[:20]}'; use newest or oldest")
    direction = FEED_SORTS[sort]
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
        "t.subtype, t.protocol_decision, t.protocol_route, t.opportunity_score, "
        f"t.relevant {base} ORDER BY m.collected_at {direction}, m.id {direction} LIMIT ? OFFSET ?"
    ), (*params, limit, offset))
    for row in rows:
        row["text_truncated"] = bool(row["text_truncated"])
        row["owned_channel"] = bool(row["owned_channel"])
        row["relevant"] = None if row["relevant"] is None else bool(row["relevant"])
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


async def detail(state, mention_id: int, config, role: str | None = None) -> dict:
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
                                 mention.platform.value, config, mention=mention, triage=triage, role=role)
    info = await engagement.engagement_info(state, mention, triage, drafts[0] if drafts else None)
    info["handoff"] = _handoff(escalations, triage)
    return {
        "why": _detail_why(mention, triage, escalations, audit),
        "mention": mention.model_dump(mode="json"),
        "triage": triage.model_dump(mode="json") if triage else None,
        "latest_draft": drafts[0].model_dump(mode="json") if drafts else None,
        "drafts": [d.model_dump(mode="json") for d in drafts],
        "escalations": escalations,
        "audit": [e.model_dump(mode="json") for e in audit],
        "claims": _claim_info(claim_ids),
        "tracked_link": tracked_link(drafts[0] if drafts else None),
        "approval": {"allowed": not blockers, "blockers": blockers,
                     "clinical_required": engagement.requires_clinical_approval(triage)},
        "engagement": info,
    }


def _handoff(escalations: list[dict], triage: Triage | None) -> dict | None:
    """Whether an escalation actually reached its owner (Protocol §5: never
    claim "escalated" unless the configured handoff succeeded)."""
    if escalations:
        current = next((e for e in reversed(escalations) if not e.get("acked_at")), escalations[-1])
        paged = bool(current.get("notified_at"))
        return {"kind": current.get("kind"), "paged": paged, "acked": bool(current.get("acked_at")),
                "status": ("acknowledged" if current.get("acked_at") else "paged, waiting for the owner"
                           if paged else "NOT handed off: the page did not go out (retrying; use the fallback "
                                         "contact)")}
    route = getattr(triage, "protocol_route", "") if triage is not None else ""
    if route == protocol.ROUTE_SUPPORT:
        return {"kind": "support", "paged": False, "acked": False,
                "status": "NOT handed off: no support escalation queue yet (FINALIZE support_channel); route it "
                          "by hand and record the handoff"}
    return None


def _detail_why(mention, triage, escalations: list[dict], audit) -> dict | None:
    """``why`` for the drawer / review desk: the open (else latest) escalation
    decides whether it was manual."""
    current = next((e for e in reversed(escalations) if not e.get("acked_at")),
                   escalations[-1] if escalations else None)
    manual_by = None
    if current is not None:
        manual_by = next((e.actor for e in audit if e.event is AuditEventType.ESCALATED
                          and e.verdict.get("action") == "manual"
                          and e.verdict.get("escalation_id") == current["id"]), None)
    return explain.urgency_explanation(mention.text, triage, evidence=explain.screen_evidence(audit),
                                       manual_by=manual_by)


def tracked_link(draft: Draft | None) -> dict | None:
    """The registry link in the draft's text, with the registry's current
    label and live flag (for the review desk chip)."""
    if draft is None:
        return None
    return links.describe(links.link_record(draft.text) or draft.link)


# --- Approval gate ------------------------------------------------------------------------------------


def _community_context(mention) -> ReplyContext | None:
    if mention is None:
        return None
    status = communities.status_for_url(mention.url)
    return ReplyContext(community_id=status.id, participation=status.participation,
                        permission_obtained=status.permission_obtained, links_allowed=status.links_allowed)


def _protocol_blocker(mention, triage: Triage | None) -> str:
    """The protocol's decision, recomputed with the current config (e.g. after
    a community's rules were verified), when it allows no public reply."""
    if mention is None or triage is None or not triage.protocol_decision:
        return ""
    prior = bool((triage.protocol or {}).get("prior_clinical_in_thread"))
    live = protocol.applied(triage, mention, prior_clinical=prior)
    if live.protocol_decision in protocol.NO_DRAFT_DECISIONS:
        label = protocol.LABELS[live.protocol_decision]
        why = "; ".join(live.protocol.get("blockers") or live.protocol.get("rationale") or [])
        return f"competitor/switching protocol says {label}: no public reply ({why})"
    return ""


def approval_blockers(status: MentionStatus, draft: Draft | None, platform: str, config, *,
                      mention=None, triage: Triage | None = None, role: str | None = None) -> list[str]:
    """Human-readable reasons approval is refused; empty when it may proceed.
    ``role``: the approver's role (None = not known yet: the clinical gate is
    reported separately as ``clinical_required``)."""
    if status is not MentionStatus.IN_REVIEW:
        return [f"only a mention waiting in review can be approved (this one is {status.value})"]
    if draft is None or not draft.text.strip():
        return ["there is no draft reply to approve; write one with Save edit first"]
    blockers = []
    if role is not None and engagement.requires_clinical_approval(triage) and not auth.can(role, "approve_clinical"):
        blockers.append("clinical approval required: an adverse-event or emergency reply may be approved only by "
                        "a clinical or admin user")
    protocol_block = _protocol_blocker(mention, triage)
    if protocol_block:
        blockers.append(protocol_block)
    gate = compliance_filter(draft.text, platform, draft.claim_ids, require_publishable=False,
                             context=_community_context(mention))
    if gate.tier == "red":
        reasons = "; ".join(f"{h.rule_id}: {h.reason}" for h in gate.hits if h.rule_id != "CLAIMS")
        blockers.append("compliance filter is red" + (f" ({reasons})" if reasons else ""))
    for link in links.not_live(draft.text):
        blockers.append(f"tracked link \"{link.label}\" ({link.id}) is not live yet: deploy the page, "
                        "confirm it with `pulse links check`, then set live: true in config/links.yaml")
    for cid, missing in knowledge.unresolved_finalize(draft.claim_ids):
        blockers.append(f"FINALIZE: {cid} depends on something WellPeps has not provided yet ({missing})")
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
        tier=gate.tier, link=links.link_record(text),
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


async def approve(state, mention_id: int, actor: str, config, draft_id: int | None = None,
                  role: str | None = None) -> None:
    """``role``: the approver's role. None counts as a reviewer (may approve
    routine replies, never a clinical-approval one)."""
    mention = await _mention_or_404(state, mention_id)
    draft = await state.get_latest_draft(mention_id)
    if draft_id is not None and (draft is None or draft.id != draft_id):
        raise ReviewError(409, "the draft changed since you opened it; reload and review the latest version")
    triage = await state.get_triage(mention_id)
    role = role or "reviewer"
    clinical = engagement.requires_clinical_approval(triage)
    if clinical and not auth.can(role, "approve_clinical"):
        raise ReviewError(403, "clinical approval required: an adverse-event or emergency reply may be approved "
                               "only by a clinical or admin user")
    if not clinical and not auth.can(role, "review"):
        raise ReviewError(403, "your role does not allow this")
    blockers = approval_blockers(mention.status, draft, mention.platform.value, config, mention=mention,
                                 triage=triage, role=role)
    if blockers:
        raise ReviewError(409, "approval refused: " + "; ".join(blockers))
    await _move(state, mention_id, MentionStatus.APPROVED)
    gate = compliance_filter(draft.text, mention.platform.value, draft.claim_ids,
                             require_publishable=config.review.require_publishable_claims)
    await state.append_audit(AuditEvent(
        mention_id=mention_id, draft_id=draft.id, event=AuditEventType.APPROVED, actor=actor,
        claim_ids=draft.claim_ids, final_text=draft.text, permalink=mention.url,
        filter_result={"ok": gate.ok, "tier": gate.tier, "hits": [h._asdict() for h in gate.hits]},
        verdict={"version": draft.version, "review_verdict": getattr(draft.review_verdict, "value", None),
                 "role": role, "clinical_approval": clinical},
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


def _http_url(url: str, allow_sandbox: bool = False) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if not url.lower().startswith(("http://", "https://")) or len(url) > 2000:
        raise ReviewError(400, "posted_url must be an http(s) link")
    if not allow_sandbox and sandbox_urls.is_sandbox_url(url):
        raise ReviewError(400, "a demo sandbox link is accepted only while the local demo sandbox is on")
    return url


async def mark_posted(state, mention_id: int, actor: str, posted_url: str = "",
                      allow_sandbox: bool = False) -> None:
    """``allow_sandbox``: the local DEMO sandbox is on for this request, so a
    sandbox comment link (http://127.0.0.1:.../sandbox/...) is a valid
    posted_url; otherwise such a link is refused."""
    posted_url = _http_url(posted_url, allow_sandbox)
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
    # The human's kind, not escalation_kind(): a manual escalation works on
    # any mention, including one not about WellPeps (user decision 2026-10-09
    # gates only the automatic routing).
    escalation = await escalate(state, notifier, mention, signal, config, kind=kind)
    if kind in SEVERE_KINDS or kind == SAFETY_WATCH:
        await _move(state, mention_id, MentionStatus.ESCALATED)
    await state.append_audit(AuditEvent(
        mention_id=mention_id, event=AuditEventType.ESCALATED, actor=actor, permalink=mention.url,
        verdict={"action": "manual", "kind": kind, "escalation_id": escalation.id,
                 "previous_status": mention.status.value},
    ))
    return escalation
