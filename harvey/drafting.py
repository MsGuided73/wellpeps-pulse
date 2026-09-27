"""Draft batch: draft -> deterministic compliance filter -> adversarial
reviewer -> human review queue.

For each triaged, reply-appropriate, non-severe mention (oldest first):
1. The drafter proposes a reply built from approved claims.
2. ``compliance_filter`` checks it (drafts need known claim ids, not yet
   publishable ones; publishability is enforced at approval time).
3. A red filter result skips the reviewer: the draft is recorded as
   ``reject`` with the filter hits as reasons. Otherwise the adversarial
   reviewer gives the verdict.
4. The draft row is saved, then audit events ``drafted`` -> ``filtered`` ->
   ``reviewed``, then the mention moves triaged -> drafted -> in_review.

An empty reply (no claim fits) is saved as ``needs_human`` with the
drafter's reason and goes to review too. Nothing here approves or posts:
``in_review`` is as far as automation goes.
"""

import inspect
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from harvey import knowledge
from harvey.compliance import GateResult, compliance_filter
from harvey.models import (
    AuditEvent,
    AuditEventType,
    Draft,
    Mention,
    MentionStatus,
    ReviewVerdict,
)

logger = logging.getLogger("harvey.drafting")

DRAFTER_ACTOR = "drafter"
FILTER_ACTOR = "compliance_filter"
REVIEWER_ACTOR = "reviewer"
SYSTEM_ACTOR = "system"
SKIPPED_RED = "skipped: filter red"

BudgetHook = Callable[[], bool | Awaitable[bool]]


@dataclass
class DraftReport:
    processed: int = 0
    drafted: int = 0          # drafts with text
    needs_human: int = 0      # verdict needs_human (incl. empty replies)
    filtered_red: int = 0
    passed: int = 0
    rejected: int = 0
    errors: int = 0
    budget_exhausted: bool = False


async def _within_budget(hook: BudgetHook | None) -> bool:
    if hook is None:
        return True
    result = hook()
    if inspect.isawaitable(result):
        result = await result
    return bool(result)


def _hit_dicts(gate: GateResult) -> list[dict]:
    return [h._asdict() for h in gate.hits]


def _hit_line(hit) -> str:
    return f"{hit.rule_id}: {hit.reason}" + (f" [{hit.match}]" if hit.match else "")


def _reason_line(reason: dict) -> str:
    return f"{reason['rule_id']}: {reason['explanation']}"


async def _review(reviewer, proposal, mention: Mention, gate: GateResult):
    """(verdict, reason lines, reviewer label, raw reasons)."""
    if gate.tier == "red":
        lines = [_hit_line(h) for h in gate.hits]
        return ReviewVerdict.REJECT, lines, SKIPPED_RED, [
            {"rule_id": h.rule_id, "explanation": h.reason} for h in gate.hits
        ]
    by_id = knowledge.claims_by_id()
    claims = [by_id[cid] for cid in proposal.claim_ids if cid in by_id]
    result = await reviewer.review(proposal.reply, mention.platform.value, mention, claims)
    return result.verdict, [_reason_line(r) for r in result.reasons], REVIEWER_ACTOR, result.reasons


async def _advance(state, mention_id: int):
    await state.set_mention_status(mention_id, MentionStatus.DRAFTED)
    await state.set_mention_status(mention_id, MentionStatus.IN_REVIEW)


async def _record_empty(state, mention: Mention, proposal) -> None:
    reason = proposal.needs_human_reason or "no reply drafted"
    draft_id = await state.add_draft(Draft(
        mention_id=mention.id, text="", claim_ids=[], model=proposal.model,
        review_verdict=ReviewVerdict.NEEDS_HUMAN, review_reasons=[reason],
    ))
    await state.append_audit(AuditEvent(
        mention_id=mention.id, draft_id=draft_id, event=AuditEventType.DRAFTED,
        actor=DRAFTER_ACTOR,
        verdict={"model": proposal.model, "needs_human_reason": reason, "rationale": proposal.rationale,
                 "dropped_claim_ids": proposal.dropped_claim_ids},
        permalink=mention.url,
    ))
    await state.append_audit(AuditEvent(
        mention_id=mention.id, draft_id=draft_id, event=AuditEventType.REVIEWED,
        actor=SYSTEM_ACTOR, verdict={"verdict": ReviewVerdict.NEEDS_HUMAN.value, "reasons": [reason]},
        permalink=mention.url,
    ))
    await _advance(state, mention.id)


async def _record_reply(state, mention: Mention, proposal, gate, verdict, lines, reviewer_label, raw) -> None:
    draft_id = await state.add_draft(Draft(
        mention_id=mention.id, text=proposal.reply, claim_ids=proposal.claim_ids,
        model=proposal.model, filter_ok=gate.ok, filter_hits=[_hit_line(h) for h in gate.hits],
        review_verdict=verdict, review_reasons=lines, tier=gate.tier,
    ))
    common = {"mention_id": mention.id, "draft_id": draft_id, "permalink": mention.url}
    await state.append_audit(AuditEvent(
        **common, event=AuditEventType.DRAFTED,
        actor=DRAFTER_ACTOR,
        claim_ids=proposal.claim_ids, final_text=proposal.reply,
        verdict={"model": proposal.model, "rationale": proposal.rationale, "needs_human_reason": proposal.needs_human_reason,
                 "dropped_claim_ids": proposal.dropped_claim_ids},
    ))
    await state.append_audit(AuditEvent(
        **common, event=AuditEventType.FILTERED, actor=FILTER_ACTOR,
        claim_ids=proposal.claim_ids,
        filter_result={"ok": gate.ok, "tier": gate.tier, "hits": _hit_dicts(gate)},
    ))
    await state.append_audit(AuditEvent(
        **common, event=AuditEventType.REVIEWED,
        actor=REVIEWER_ACTOR if reviewer_label == REVIEWER_ACTOR else FILTER_ACTOR,
        verdict={"verdict": verdict.value, "reasons": raw, "reviewer": reviewer_label},
    ))
    await _advance(state, mention.id)


def _count(report: DraftReport, verdict: ReviewVerdict, tier: str | None) -> None:
    if tier == "red":
        report.filtered_red += 1
    if verdict is ReviewVerdict.PASS:
        report.passed += 1
    elif verdict is ReviewVerdict.REJECT:
        report.rejected += 1
    else:
        report.needs_human += 1


async def _draft_one(state, drafter, reviewer, mention: Mention, report: DraftReport) -> None:
    triage = await state.get_triage(mention.id)
    proposal = await drafter.draft(mention, triage)
    if not proposal.reply:
        await _record_empty(state, mention, proposal)
        _count(report, ReviewVerdict.NEEDS_HUMAN, None)
        return
    gate = compliance_filter(proposal.reply, mention.platform.value, proposal.claim_ids,
                             require_publishable=False)
    verdict, lines, label, raw = await _review(reviewer, proposal, mention, gate)
    await _record_reply(state, mention, proposal, gate, verdict, lines, label, raw)
    report.drafted += 1
    _count(report, verdict, gate.tier)


async def draft_batch(
    state, drafter, reviewer, limit: int = 10, budget_ok: BudgetHook | None = None
) -> DraftReport:
    """Draft, filter, and review up to ``limit`` mentions; see module doc.

    ``budget_ok`` is checked before each mention (a mention costs a drafter
    call and usually a reviewer call). A mention that fails before its
    draft is saved stays ``triaged`` and is retried next cycle.
    """
    report = DraftReport()
    for mention in await state.list_draftable_mentions(limit=limit):
        if not await _within_budget(budget_ok):
            report.budget_exhausted = True
            logger.info("drafting paused: Claude budget exhausted")
            break
        try:
            await _draft_one(state, drafter, reviewer, mention, report)
        except Exception as exc:
            report.errors += 1
            logger.error(f"drafting failed for mention {mention.id}: {exc}", exc_info=True)
            continue
        report.processed += 1
    return report
