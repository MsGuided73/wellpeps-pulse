"""Escalations: an owner, an SLA, and a Slack page for urgent mentions.

- ``escalate`` opens one escalation per mention (idempotent while it is
  unacknowledged), pages Slack, and audits it. The row is created even when
  Slack is down or unset; ``notified_at`` stays null ("not paged") and the
  sweep retries.
- ``sweep`` runs every heartbeat, quiet hours or not: it flags SLA breaches
  and re-pages them (with the backup owner), and retries unsent pages. Each
  escalation is paged at most once per sweep.
- ``ack`` records who took it.

PHI rule: every Slack message is built by ``build_page`` and carries only
the kind, platform, an urgency reason *code*, the permalink, a dashboard
link, the SLA due time, and owner names. Never the post text, the author
handle, extracted phrases, or model-written reasons.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from harvey.models import (
    AuditEvent,
    AuditEventType,
    Category,
    Escalation,
    Mention,
    Triage,
    Urgency,
)

logger = logging.getLogger("harvey.escalation")

ACTOR = "escalation"
UNASSIGNED = "UNASSIGNED — set an owner in harvey.yaml"
BREACH_PREFIX = "SLA BREACHED"
# Same string as harvey.agents.triager.FALLBACK_REASON (tested); not imported
# because the triager imports this module.
TRIAGE_FAILED = "triage_failed"

_KIND_BY_CATEGORY = {
    Category.ADVERSE_EVENT: "adverse_event",
    Category.LEGAL_REGULATORY: "legal",
    Category.PRIVACY: "privacy",
    Category.BILLING_FRAUD: "billing_fraud",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- Pure helpers ----------------------------------------------------------------


def escalation_kind(triage: Triage) -> str | None:
    """The escalation kind for a triage result, or None if it isn't one.

    Severe categories escalate at any urgency (the triager already forces
    their keyword matches to urgent). An urgent complaint about WellPeps
    itself is a viral negative.
    """
    if not triage.relevant:
        return None
    kind = _KIND_BY_CATEGORY.get(triage.category)
    if kind:
        return kind
    if (
        triage.category is Category.COMPLAINT
        and triage.urgency is Urgency.URGENT
        and triage.subject_type == "wellpeps"
    ):
        return "viral_negative"
    return None


def owner_for(kind: str, config) -> str:
    esc = config.escalation
    if kind == "adverse_event":
        return esc.clinical_owner.strip() or esc.owners.get(kind, "")
    return esc.owners.get(kind, "")


def reason_code(triage: Triage) -> str:
    """A fixed code for why this is urgent. Model text never leaves Pulse."""
    reason = triage.urgency_reason or ""
    if reason.startswith("override:"):
        return "keyword_override"
    if TRIAGE_FAILED in reason:
        return TRIAGE_FAILED
    return f"model_{triage.urgency.value}"


def _slack_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt_time(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else "n/a"


def build_page(
    mention: Mention,
    escalation: Escalation,
    triage: Triage | None,
    config,
    *,
    breached: bool = False,
) -> tuple[str, list[dict]]:
    """The one place a Slack message is composed. Link + category only."""
    prefix = f"{BREACH_PREFIX} — " if breached else ""
    headline = f"{prefix}Escalation #{escalation.id}: {escalation.kind} on {mention.platform.value}"
    owner = escalation.owner or UNASSIGNED
    body = [
        headline,
        f"Reason: {reason_code(triage) if triage else 'unknown'}",
        f"Owner: {owner}",
    ]
    if breached:
        body.append(f"Backup: {config.escalation.backup_owner.strip() or UNASSIGNED}")
    body.append(f"SLA due: {_fmt_time(escalation.sla_due_at)}")
    body.append(f"Post: {mention.url}")
    dashboard = config.notify.dashboard_url.strip().rstrip("/")
    if dashboard:
        body.append(f"Dashboard: {dashboard}/#escalation-{escalation.id}")
    text = "\n".join(body)

    mrkdwn = [f"*{_slack_escape(headline)}*"]
    mrkdwn += [_slack_escape(line) for line in body[1:] if not line.startswith(("Post:", "Dashboard:"))]
    mrkdwn.append(f"<{_slack_escape(mention.url)}|Open post>")
    if dashboard:
        mrkdwn.append(f"<{_slack_escape(dashboard)}/#escalation-{escalation.id}|Open in Pulse>")
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(mrkdwn)}}]
    return text, blocks


# --- escalate ------------------------------------------------------------------------------


async def _page(notifier, mention, escalation, triage, config, *, breached=False) -> bool:
    text, blocks = build_page(mention, escalation, triage, config, breached=breached)
    try:
        return bool(await notifier.send(text, blocks=blocks))
    except Exception as exc:  # the notifier shouldn't raise; be sure anyway
        logger.error(f"page for escalation {escalation.id} failed: {type(exc).__name__}")
        return False


async def escalate(state, notifier, mention: Mention, triage: Triage, config, now=None) -> Escalation:
    """Open (or return the already-open) escalation for a mention and page it."""
    kind = escalation_kind(triage)
    if kind is None:
        raise ValueError(f"mention {mention.id} is not an escalation ({triage.category.value})")
    existing = await state.get_open_escalation(mention.id)
    if existing is not None:
        return existing

    now = now or _utcnow()
    draft = Escalation(
        mention_id=mention.id,
        kind=kind,
        owner=owner_for(kind, config),
        sla_due_at=now + timedelta(minutes=config.escalation.sla_minutes),
        created_at=now,
    )
    escalation = draft.model_copy(update={"id": await state.create_escalation(draft)})

    paged = await _page(notifier, mention, escalation, triage, config)
    if paged:
        await state.mark_escalation_notified(escalation.id, now)
        escalation = escalation.model_copy(update={"notified_at": now})
    else:
        logger.warning(f"escalation {escalation.id} ({kind}) opened but not paged")

    await state.append_audit(AuditEvent(
        mention_id=mention.id, event=AuditEventType.ESCALATED, actor=ACTOR,
        verdict={
            "action": "opened",
            "escalation_id": escalation.id,
            "kind": kind,
            "owner": escalation.owner,
            "sla_due_at": escalation.sla_due_at.isoformat(),
            "paged": paged,
            "reason": triage.urgency_reason,
        },
        permalink=mention.url,
        at=now,
    ))
    return escalation


# --- sweep --------------------------------------------------------------------------------


@dataclass
class SweepReport:
    open: int = 0
    breached: int = 0
    paged: int = 0
    failed: int = 0
    errors: int = 0


async def _sweep_one(state, notifier, config, escalation: Escalation, now, report: SweepReport):
    due = escalation.sla_due_at is not None and now >= escalation.sla_due_at
    breach = due and not escalation.breached
    retry = escalation.notified_at is None
    if not (breach or retry):
        return

    mention = await state.get_mention(escalation.mention_id)
    if mention is None:
        logger.error(f"escalation {escalation.id}: mention {escalation.mention_id} is missing")
        report.errors += 1
        return
    triage = await state.get_triage(escalation.mention_id)

    if breach:
        await state.mark_escalation_breached(escalation.id)
        report.breached += 1
    # One page per escalation per sweep: a breach page doubles as the retry.
    paged = await _page(notifier, mention, escalation, triage, config, breached=breach)
    if paged:
        report.paged += 1
        if retry:
            await state.mark_escalation_notified(escalation.id, now)
    else:
        report.failed += 1

    if breach or paged:
        await state.append_audit(AuditEvent(
            mention_id=mention.id, event=AuditEventType.ESCALATED, actor=ACTOR,
            verdict={
                "action": "sla_breached" if breach else "paged_retry",
                "escalation_id": escalation.id,
                "kind": escalation.kind,
                "paged": paged,
                "backup_owner": config.escalation.backup_owner if breach else "",
            },
            permalink=mention.url,
            at=now,
        ))


async def sweep(state, notifier, config, now=None) -> SweepReport:
    """Flag SLA breaches, re-page them, and retry unsent pages."""
    now = now or _utcnow()
    open_escalations = await state.list_open_escalations()
    report = SweepReport(open=len(open_escalations))
    for escalation in open_escalations:
        try:
            await _sweep_one(state, notifier, config, escalation, now, report)
        except Exception as exc:
            report.errors += 1
            logger.error(f"sweep failed for escalation {escalation.id}: {exc}", exc_info=True)
    return report


# --- ack ------------------------------------------------------------------------------------


async def ack(state, escalation_id: int, user: str, now=None) -> bool:
    """Acknowledge an escalation. Acking twice (or an unknown id) is a no-op."""
    user = (user or "").strip()
    if not user:
        raise ValueError("ack needs the name of the person taking it")
    now = now or _utcnow()
    if not await state.ack_escalation(escalation_id, user, at=now):
        return False
    escalation = await state.get_escalation(escalation_id)
    mention = await state.get_mention(escalation.mention_id)
    await state.append_audit(AuditEvent(
        mention_id=escalation.mention_id, event=AuditEventType.ACKED, actor=user,
        verdict={"escalation_id": escalation_id, "kind": escalation.kind,
                 "breached": escalation.breached},
        permalink=mention.url if mention else "",
        at=now,
    ))
    return True
