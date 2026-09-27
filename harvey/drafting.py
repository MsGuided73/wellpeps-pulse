"""Draft batch: draft -> deterministic compliance filter -> adversarial
reviewer -> human review queue.

For each triaged, reply-appropriate, non-severe mention (oldest first):
1. The drafter proposes a reply built from approved claims.
2. ``compliance_filter`` checks it (drafts need known claim ids, not yet
   publishable ones; publishability is enforced at approval time).
3. A red result gets ONE rewrite: the drafter is re-prompted with the hit
   reasons and the new draft is filtered again. Drafter calls per mention
   are capped at ``MAX_DRAFTER_CALLS`` (2), so a first draft that already
   needed a parse retry is not redrafted. Both attempts are kept: the first
   as its own draft row, audited ``drafted`` + ``filtered`` (attempt 1).
4. A draft that is still red skips the reviewer: it is recorded as
   ``reject`` with the filter hits as reasons. Otherwise the adversarial
   reviewer gives the verdict.
5. The draft row is saved, then audit events ``drafted`` -> ``filtered`` ->
   ``reviewed`` (with the attempt number), then the mention moves
   triaged -> drafted -> in_review.

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
SUPERSEDED = "superseded: redrafted after a red filter result"
# Drafter model calls per mention, retries and the redraft included.
MAX_DRAFTER_CALLS = 2

BudgetHook = Callable[[], bool | Awaitable[bool]]


@dataclass
class DraftReport:
    processed: int = 0
    drafted: int = 0          # drafts with text
    needs_human: int = 0      # verdict needs_human (incl. empty replies)
    filtered_red: int = 0     # final drafts that were still red
    redrafted: int = 0        # mentions whose first draft was red and got one rewrite
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


def _attempt_verdict(proposal, attempt: int, **extra) -> dict:
    return {"model": proposal.model, "rationale": proposal.rationale,
            "needs_human_reason": extra.pop("needs_human_reason", proposal.needs_human_reason),
            "dropped_claim_ids": proposal.dropped_claim_ids, "attempt": attempt, **extra}


def _filter_result(gate: GateResult, attempt: int) -> dict:
    return {"ok": gate.ok, "tier": gate.tier, "hits": _hit_dicts(gate), "attempt": attempt}


async def _record_empty(state, mention: Mention, proposal, attempt: int = 1) -> None:
    reason = proposal.needs_human_reason or "no reply drafted"
    draft_id = await state.add_draft(Draft(
        mention_id=mention.id, text="", claim_ids=[], model=proposal.model,
        review_verdict=ReviewVerdict.NEEDS_HUMAN, review_reasons=[reason],
    ))
    await state.append_audit(AuditEvent(
        mention_id=mention.id, draft_id=draft_id, event=AuditEventType.DRAFTED,
        actor=DRAFTER_ACTOR,
        verdict=_attempt_verdict(proposal, attempt, needs_human_reason=reason),
        permalink=mention.url,
    ))
    await state.append_audit(AuditEvent(
        mention_id=mention.id, draft_id=draft_id, event=AuditEventType.REVIEWED,
        actor=SYSTEM_ACTOR, verdict={"verdict": ReviewVerdict.NEEDS_HUMAN.value, "reasons": [reason]},
        permalink=mention.url,
    ))
    await _advance(state, mention.id)


async def _record_superseded(state, mention: Mention, proposal, gate: GateResult) -> None:
    """The red first attempt: kept as its own draft row, drafted + filtered."""
    lines = [_hit_line(h) for h in gate.hits]
    draft_id = await state.add_draft(Draft(
        mention_id=mention.id, text=proposal.reply, claim_ids=proposal.claim_ids,
        model=proposal.model, filter_ok=gate.ok, filter_hits=lines,
        review_verdict=ReviewVerdict.REJECT, review_reasons=[SUPERSEDED, *lines], tier=gate.tier,
    ))
    common = {"mention_id": mention.id, "draft_id": draft_id, "permalink": mention.url}
    await state.append_audit(AuditEvent(
        **common, event=AuditEventType.DRAFTED, actor=DRAFTER_ACTOR,
        claim_ids=proposal.claim_ids, final_text=proposal.reply,
        verdict=_attempt_verdict(proposal, 1),
    ))
    await state.append_audit(AuditEvent(
        **common, event=AuditEventType.FILTERED, actor=FILTER_ACTOR,
        claim_ids=proposal.claim_ids,
        filter_result={**_filter_result(gate, 1), "redrafted": True},
    ))


async def _record_reply(state, mention: Mention, proposal, gate, verdict, lines, reviewer_label, raw,
                        attempt: int = 1) -> None:
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
        verdict=_attempt_verdict(proposal, attempt),
    ))
    await state.append_audit(AuditEvent(
        **common, event=AuditEventType.FILTERED, actor=FILTER_ACTOR,
        claim_ids=proposal.claim_ids,
        filter_result=_filter_result(gate, attempt),
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


def _filter(proposal, mention: Mention) -> GateResult:
    return compliance_filter(proposal.reply, mention.platform.value, proposal.claim_ids,
                             require_publishable=False)


async def _redraft_if_red(drafter, mention, triage, proposal, report: DraftReport):
    """(final proposal, its gate or None, superseded (proposal, gate) or None).

    A red first draft gets exactly one rewrite with the filter's reasons,
    within the MAX_DRAFTER_CALLS budget for the mention.
    """
    if not proposal.reply:
        return proposal, None, None
    gate = _filter(proposal, mention)
    used = int(getattr(proposal, "calls", 1) or 1)
    if gate.tier != "red" or used >= MAX_DRAFTER_CALLS:
        return proposal, gate, None
    feedback = [_hit_line(h) for h in gate.hits]
    logger.info(f"draft for mention {mention.id} was red; redrafting once ({len(feedback)} reason(s))")
    report.redrafted += 1
    second = await drafter.draft(mention, triage, feedback=feedback, max_calls=MAX_DRAFTER_CALLS - used)
    return second, (_filter(second, mention) if second.reply else None), (proposal, gate)


async def _draft_one(state, drafter, reviewer, mention: Mention, report: DraftReport) -> None:
    triage = await state.get_triage(mention.id)
    first = await drafter.draft(mention, triage)
    proposal, gate, superseded = await _redraft_if_red(drafter, mention, triage, first, report)
    attempt = 2 if superseded else 1
    if not proposal.reply:
        if superseded:
            await _record_superseded(state, mention, *superseded)
        await _record_empty(state, mention, proposal, attempt)
        _count(report, ReviewVerdict.NEEDS_HUMAN, None)
        return
    verdict, lines, label, raw = await _review(reviewer, proposal, mention, gate)
    # Nothing is stored until the review is done, so a crash leaves the
    # mention ``triaged`` with no partial drafts; it is retried next cycle.
    if superseded:
        await _record_superseded(state, mention, *superseded)
    await _record_reply(state, mention, proposal, gate, verdict, lines, label, raw, attempt)
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
