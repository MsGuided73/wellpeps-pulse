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

Rules of engagement (docs/RULES-OF-ENGAGEMENT.md, harvey/engagement.py) come
first, before any model call:
- a community whose rules prohibit brand participation: no reply; the
  mention stays ``triaged`` with a ``skipped`` audit event (never retried);
- a stop rule (Guide §22: WellPeps already replied twice in the thread, the
  person repeats an individual medical request after a boundary reply, an
  abusive thread): the graceful close or an empty draft, ``needs_human``;
- a ``boundary_only`` situation (dose, labs, adverse event, emergency, media,
  complaint, ...): the guide's approved response verbatim, disclosure first,
  no drafter or reviewer call, ``needs_human`` (a human confirms it fits;
  adverse events and emergencies need a clinical approver). With
  ``Drafter(acknowledgements=True)`` one short acknowledgement sentence about
  the post's stated issue may sit between the disclosure and the verbatim
  response (one haiku call, harvey/agents/acknowledger.py; deterministic
  checks plus "no new filter hit", else the verbatim reply);
- otherwise the drafter gets the situation's template, preferred claims, the
  persona's disclosure, the community's link / promotion limits and, for the
  competitor / switching protocol, its brand mode and the unmet need; the
  filter checks the draft against the same community context.
- a thread with another WellPeps reply already waiting for review gets no
  second one (``skipped``; Competitor/Switching Protocol §9).
The 80/20 share never gates a reply (a planning metric, Protocol §1).

Smart Patient's Guide (binding user instruction 2026-10-07, harvey/guides.py):
in answering situations the filter also checks the draft points to the
relevant guide (``guidance.guide_requirement()``). A draft without it gets ONE
automatic redraft with that feedback (within MAX_DRAFTER_CALLS); still missing
-> ``needs_human``. A guide in a boundary / excluded reply is red.
"""

import inspect
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from harvey import communities, engagement, guides, knowledge, links
from harvey.batching import run_by_thread
from harvey.agents.acknowledger import SKIP_CATEGORIES as ACK_SKIP_CATEGORIES
from harvey.agents.acknowledger import SKIP_SITUATIONS as ACK_SKIP_SITUATIONS
from harvey.compliance import GateResult, ReplyContext, compliance_filter
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
    skipped: int = 0             # no reply: the community prohibits brand participation
    approved_responses: int = 0  # verbatim guide responses / graceful closes (no model call)
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


async def _review(reviewer, proposal, mention: Mention, gate: GateResult, guidance=None):
    """(verdict, reason lines, reviewer label, raw reasons)."""
    if gate.tier == "red":
        lines = [_hit_line(h) for h in gate.hits]
        return ReviewVerdict.REJECT, lines, SKIPPED_RED, [
            {"rule_id": h.rule_id, "explanation": h.reason} for h in gate.hits
        ]
    by_id = knowledge.claims_by_id()
    claims = [by_id[cid] for cid in proposal.claim_ids if cid in by_id]
    result = await reviewer.review(proposal.reply, mention.platform.value, mention, claims, guidance=guidance)
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
        link=links.link_record(proposal.reply),
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
        link=links.link_record(proposal.reply),
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


def _filter(proposal, mention: Mention, context: ReplyContext | None = None, guide=None) -> GateResult:
    return compliance_filter(proposal.reply, mention.platform.value, proposal.claim_ids,
                             require_publishable=False, context=context, guide=guide)


def _requirement(guidance):
    method = getattr(guidance, "guide_requirement", None)
    return method() if callable(method) else None


def _needs_redraft(gate: GateResult) -> bool:
    """Red, or the required Smart Patient's Guide reference is missing."""
    return gate.tier == "red" or guides.missing(gate)


async def _redraft_if_red(drafter, mention, triage, proposal, report: DraftReport,
                          context: ReplyContext | None = None, guidance=None):
    """(final proposal, its gate or None, superseded (proposal, gate) or None).

    A red first draft gets exactly one rewrite with the filter's reasons,
    within the MAX_DRAFTER_CALLS budget for the mention.
    """
    if not proposal.reply:
        return proposal, None, None
    requirement = _requirement(guidance)
    gate = _filter(proposal, mention, context, requirement)
    used = int(getattr(proposal, "calls", 1) or 1)
    if not _needs_redraft(gate) or used >= MAX_DRAFTER_CALLS:
        return proposal, gate, None
    feedback = [_hit_line(h) for h in gate.hits if h in gate.red_hits or h.rule_id == guides.MISSING_RULE]
    logger.info(f"draft for mention {mention.id} was {'red' if gate.tier == 'red' else 'missing its guide'}; "
                f"redrafting once ({len(feedback)} reason(s))")
    report.redrafted += 1
    second = await drafter.draft(mention, triage, feedback=feedback, max_calls=MAX_DRAFTER_CALLS - used,
                                 guidance=guidance)
    return second, (_filter(second, mention, context, requirement) if second.reply else None), (proposal, gate)


@dataclass(frozen=True)
class _Fixed:
    """A reply Pulse writes without a model: an approved response or the close."""

    reply: str
    claim_ids: list
    rationale: str = ""
    needs_human_reason: str | None = None
    model: str = engagement.APPROVED_RESPONSE_MODEL
    dropped_claim_ids: tuple = ()
    calls: int = 0


async def _skip(state, mention: Mention, reason: str, report: DraftReport) -> None:
    """No reply at all; the mention stays ``triaged`` (audited once, never retried)."""
    await state.append_audit(AuditEvent(
        mention_id=mention.id, event=AuditEventType.SKIPPED, actor=DRAFTER_ACTOR,
        verdict={"reason": reason}, permalink=mention.url,
    ))
    report.skipped += 1
    logger.info(f"mention {mention.id}: no reply ({reason})")


async def _record_fixed(state, mention: Mention, fixed: _Fixed, context: ReplyContext,
                        reasons: list[str], report: DraftReport, guide=None) -> None:
    """An approved response / graceful close: filtered with the community
    context, never sent to the model reviewer, always ``needs_human``."""
    gate = _filter(fixed, mention, context, guide)
    raw = [{"rule_id": "ENGAGEMENT", "explanation": r} for r in reasons]
    verdict = ReviewVerdict.REJECT if gate.tier == "red" else ReviewVerdict.NEEDS_HUMAN
    lines = [*reasons, *([_hit_line(h) for h in gate.red_hits] if gate.tier == "red" else [])]
    await _record_reply(state, mention, fixed, gate, verdict, lines, FILTER_ACTOR, raw)
    report.drafted += 1
    report.approved_responses += 1
    _count(report, verdict, gate.tier)


def _approved_reasons(situation, triage) -> list[str]:
    response = engagement.approved_response_id(situation, triage)
    source = knowledge.claims_by_id()[response].source
    reasons = [f"approved response, verbatim ({source}; situation: {situation.label}): a human confirms "
               "it fits this post before approving"]
    if engagement.requires_clinical_approval(triage):
        reasons.append("clinical approval required: only a clinical or admin user may approve this reply")
    if situation.escalate:
        reasons.append(f"also escalated ({situation.escalate}); follow the internal escalation procedure")
    return reasons


def _hit_keys(gate: GateResult) -> set[tuple[str, str]]:
    return {(h.rule_id, h.reason) for h in gate.hits}


async def _acknowledged(drafter, mention: Mention, situation, triage, text: str, ids: list,
                        context: ReplyContext) -> tuple[str, str, str]:
    """(reply, model label, review note): the approved reply with one validated
    acknowledgement sentence after the disclosure (harvey/agents/acknowledger.py),
    or the verbatim reply whenever the clause is off, missing or adds any filter hit."""
    verbatim = (text, engagement.APPROVED_RESPONSE_MODEL, "")
    acknowledge = getattr(drafter, "acknowledge", None)
    if not getattr(drafter, "acknowledgements", False) or acknowledge is None \
            or situation.id in ACK_SKIP_SITUATIONS \
            or getattr(getattr(triage, "category", None), "value", "") in ACK_SKIP_CATEGORIES:
        return verbatim
    try:
        clause, problems = await acknowledge(mention, situation.label)
    except Exception as exc:
        clause, problems = "", [f"error: {type(exc).__name__}"]
    composed = engagement.acknowledged_reply(situation, triage, clause) if clause else None
    if composed is None:
        why = "; ".join(problems) or "the approved response carries its own disclosure"
        return (text, engagement.APPROVED_RESPONSE_MODEL,
                f"acknowledgement not used ({why}): the approved response is verbatim")
    added = _hit_keys(_filter(_Fixed(reply=composed[0], claim_ids=ids), mention, context)) \
        - _hit_keys(_filter(_Fixed(reply=text, claim_ids=ids), mention, context))
    if added:
        rules = ", ".join(sorted({rule for rule, _ in added}))
        return (text, engagement.APPROVED_RESPONSE_MODEL,
                f"acknowledgement not used (compliance filter: {rules}): the approved response is verbatim")
    model = getattr(drafter, "acknowledgement_model", lambda: "")() or "model"
    return (composed[0], f"{engagement.APPROVED_RESPONSE_MODEL}+ack:{model}",
            f"acknowledgement sentence \"{clause}\" was written by a model ({model}) and passed the "
            "deterministic checks; the approved response after it is verbatim")


async def _draft_one(state, drafter, reviewer, mention: Mention, report: DraftReport) -> None:
    triage = await state.get_triage(mention.id)
    situation = engagement.situation_of(triage)
    records = await engagement.recent_replies(state)
    context = engagement.context_from(mention, records)
    if context.participation == "prohibited":
        await _skip(state, mention, f"brand participation is prohibited in community {context.community_id} "
                                    "(config/communities.yaml); WellPeps approval never overrides a "
                                    "community rule", report)
        return
    stop = engagement.stop_decision_from(mention, triage, records)
    if stop is not None:
        if stop.action == "skip":
            await _skip(state, mention, stop.reason, report)
            return
        if stop.action == "close":
            text, ids = engagement.close_reply()
            await _record_fixed(state, mention, _Fixed(reply=text, claim_ids=ids, rationale=stop.reason),
                                context, [stop.reason], report)
            return
        await _record_empty(state, mention, _Fixed(reply="", claim_ids=[], needs_human_reason=stop.reason,
                                                   model=""))
        _count(report, ReviewVerdict.NEEDS_HUMAN, None)
        return
    if situation.reply == "boundary_only":
        text, ids = engagement.approved_reply(situation, triage)
        reasons = _approved_reasons(situation, triage)
        text, model, note = await _acknowledged(drafter, mention, situation, triage, text, ids, context)
        if note:
            reasons.append(note)
        await _record_fixed(state, mention, _Fixed(reply=text, claim_ids=ids, rationale=reasons[0], model=model),
                            context, reasons, report,
                            guide=guides.requirement_for(situation, context, triage, mention))
        return
    guidance = engagement.guidance_for(situation, context, triage, mention)
    first = await drafter.draft(mention, triage, guidance=guidance)
    proposal, gate, superseded = await _redraft_if_red(drafter, mention, triage, first, report,
                                                       context, guidance)
    attempt = 2 if superseded else 1
    if not proposal.reply:
        if superseded:
            await _record_superseded(state, mention, *superseded)
        await _record_empty(state, mention, proposal, attempt)
        _count(report, ReviewVerdict.NEEDS_HUMAN, None)
        return
    verdict, lines, label, raw = await _review(reviewer, proposal, mention, gate, guidance)
    if gate is not None and guides.missing(gate) and verdict is ReviewVerdict.PASS:
        why = next(_hit_line(h) for h in gate.hits if h.rule_id == guides.MISSING_RULE)
        verdict, lines, raw = (ReviewVerdict.NEEDS_HUMAN, [*lines, f"still {why} after one redraft"],
                               [*raw, {"rule_id": guides.MISSING_RULE,
                                       "explanation": f"still missing the guide reference after one redraft: {why}"}])
    # Nothing is stored until the review is done, so a crash leaves the
    # mention ``triaged`` with no partial drafts; it is retried next cycle.
    if superseded:
        await _record_superseded(state, mention, *superseded)
    await _record_reply(state, mention, proposal, gate, verdict, lines, label, raw, attempt)
    report.drafted += 1
    _count(report, verdict, gate.tier)


async def draft_batch(
    state, drafter, reviewer, limit: int = 10, budget_ok: BudgetHook | None = None,
    concurrency: int = 1, only: Callable[[Mention], bool] | None = None,
) -> DraftReport:
    """Draft, filter, and review up to ``limit`` mentions; see module doc.

    ``budget_ok`` is checked before each mention (a mention costs a drafter
    call and usually a reviewer call). A mention that fails before its
    draft is saved stays ``triaged`` and is retried next cycle.
    ``concurrency`` > 1 drafts different threads side by side, one mention
    per thread at a time (harvey/batching.py; scripts/seed_demo.py --claude).
    ``only``: draft just the draftable mentions it accepts (the rest wait).
    """
    report = DraftReport()
    mentions = [m for m in await state.list_draftable_mentions(limit=limit) if only is None or only(m)]

    async def one(mention: Mention) -> bool:
        if not await _within_budget(budget_ok):
            report.budget_exhausted = True
            logger.info("drafting paused: Claude budget exhausted")
            return False
        try:
            await _draft_one(state, drafter, reviewer, mention, report)
        except Exception as exc:
            report.errors += 1
            logger.error(f"drafting failed for mention {mention.id}: {exc}", exc_info=True)
            return True
        report.processed += 1
        return True

    if concurrency <= 1:
        for mention in mentions:
            if not await one(mention):
                break
        return report
    await run_by_thread(mentions, lambda m: communities.thread_key(m.url), one, concurrency)
    return report
