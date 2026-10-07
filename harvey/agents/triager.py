"""Triage agent: classify one mention, then apply a deterministic safety net.

One mention per Claude call (not batches): each answer is validated on its
own, a bad answer costs one retry for one mention, and the keyword override
and name normalization run per mention. Triage uses a small model (haiku,
see config.DEFAULT_MODELS), so per-call overhead stays low.

Flow for ``Triager.triage``:
1. Build the prompt (prompts/triage.md). The mention text is wrapped in
   nonce-tagged delimiters and the model is told to ignore instructions
   inside it. Only compact name lists from knowledge are injected: never
   prices, aliases, complaint notes, or anything cost-related.
2. Ask the brain for JSON and validate it. One retry on invalid output; then
   fall back to a conservative "needs a human" triage so nothing is dropped.
3. Safety net (pure Python, always runs): canonicalize competitor/product
   names (``product`` is a WellPeps SKU and is kept only when the mention is
   about WellPeps; ``drug`` is the generic/category drug discussed), keep only phrases copied verbatim, force urgent + no-reply when a
   ``knowledge.urgent_override`` pattern matches, force every severe
   category to urgent (so it always escalates), and never allow a reply on
   severe or irrelevant mentions.
4. In ``triage_batch``, an optional independent safety screen
   (harvey/agents/safety_screen.py) re-checks health-related mentions.
"""

import inspect
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from harvey import communities, knowledge, protocol
from harvey.agents import prompting
from harvey.batching import run_by_thread
from harvey.agents.safety_screen import ScreenResult, apply_screen, mentions_health_term
from harvey.escalation import SEVERE_KINDS, escalation_kind
from harvey.models import (
    AuditEvent,
    AuditEventType,
    Category,
    Mention,
    MentionStatus,
    Triage,
    Urgency,
)
from harvey.models.mention import PROTOCOL_INTENTS, PROTOCOL_NEEDS, TRIAGE_SUBTYPES, normalize_subtype

logger = logging.getLogger("harvey.agents.triager")

PROMPT_PATH = prompting.PROMPTS_DIR / "triage.md"
AGENT = "triager"
TASK = "classify"

MAX_ATTEMPTS = 2           # first try + one retry
MAX_PHRASES = 5
MAX_PHRASE_CHARS = 120
FALLBACK_REASON = "triage_failed"

# Categories that go to a named human owner, never to the reply queue.
SEVERE_CATEGORIES = frozenset({
    Category.ADVERSE_EVENT,
    Category.LEGAL_REGULATORY,
    Category.PRIVACY,
    Category.BILLING_FRAUD,
})


class TriageAnswer(BaseModel):
    """The JSON shape the model must return (see prompts/triage.md)."""

    model_config = ConfigDict(extra="ignore")

    relevant: bool
    subject_type: Literal["wellpeps", "competitor", "product", "category", "none"]
    subject: str = ""
    competitor: str | None = None
    product: str | None = None
    drug: str | None = None
    category: Category
    # The situation inside the category (rules of engagement). Optional and
    # forgiving: an unknown value becomes "" (see normalize_subtype).
    subtype: str | None = None
    # Competitor / switching protocol inputs (harvey/protocol.py). Optional
    # and forgiving: unknown tags are dropped, scores clamped to 0-2.
    intents: list | None = None
    unmet_need: str | None = None
    need_clarity: int | float | None = None
    useful_contribution: int | float | None = None
    sentiment: float = Field(ge=-1.0, le=1.0)
    sentiment_label: Literal["positive", "neutral", "negative", "mixed"]
    urgency: Urgency
    urgency_reason: str = ""
    reply_appropriate: bool
    phrases: list[str] = Field(default_factory=list)


# --- Prompt ------------------------------------------------------------------


def _name_lists() -> dict[str, str]:
    comps = knowledge.competitors().all()
    prods = knowledge.products()
    return {
        "competitors": ", ".join(c.name for c in comps),
        "products": ", ".join(p.name for p in prods.products),
        "product_categories": ", ".join(c.name for c in prods.categories),
        "categories": ", ".join(f"`{c.value}`" for c in Category),
        "subtypes": ", ".join(f"`{s}`" for s in TRIAGE_SUBTYPES),
        "intents": ", ".join(f"`{s}`" for s in PROTOCOL_INTENTS),
        "needs": ", ".join(f"`{s}`" for s in PROTOCOL_NEEDS),
    }


def build_prompt(mention: Mention, nonce: str | None = None) -> str:
    """Fill prompts/triage.md for one mention.

    Substitution is single-pass, so placeholders inside the mention text are
    left as literal text rather than expanded.
    """
    return prompting.render(PROMPT_PATH, {
        **_name_lists(),
        "nonce": nonce or prompting.new_nonce(),
        "platform": mention.platform.value,
        "mention": prompting.mention_block(mention),
    })


# --- Safety net (pure) -----------------------------------------------------------


def _canonical(name: str | None, lookup) -> str:
    if not name or not isinstance(name, str):
        return ""
    return lookup.get(name.strip().lower(), "")


MAX_DRUG_CHARS = 60


def _drug(name: str | None, text: str) -> str:
    """Canonical generic/category drug name.

    Known terms (products.yaml generics, never-offered peptides, keywords
    generics, and unambiguous brand names) map to their canonical spelling.
    An unknown term is kept, lower-cased, only if it appears in the post.
    """
    if not name or not isinstance(name, str):
        return ""
    key = name.strip().lower()
    if not key:
        return ""
    known = knowledge.drug_lookup().get(key)
    if known:
        return known
    if len(key) <= MAX_DRUG_CHARS and key in (text or "").lower():
        return key
    return ""


def _post_text(mention: Mention) -> str:
    return f"{mention.title}\n{mention.text}"


def _about_wellpeps(answer: "TriageAnswer", mention: Mention) -> bool:
    return answer.subject_type == "wellpeps" or knowledge.names_wellpeps(
        _post_text(mention))


def _verbatim_phrases(phrases: list[str], text: str) -> list[str]:
    kept: list[str] = []
    for phrase in phrases:
        if not isinstance(phrase, str):
            continue
        phrase = phrase.strip()
        if phrase and len(phrase) <= MAX_PHRASE_CHARS and phrase in text and phrase not in kept:
            kept.append(phrase)
        if len(kept) == MAX_PHRASES:
            break
    return kept


SEVERE_REASON = "severe_category"


def apply_safety_net(triage: Triage, text: str) -> Triage:
    """Deterministic post-processing. Returns a new Triage.

    A keyword override forces urgent + a severe category. Any severe
    category, whether from a keyword or from the model, is then forced to
    urgent with no reply, so a severe mention is always escalated (see
    ``route_status``) and never sits in ``triaged`` where it can't be drafted.
    """
    update: dict = {}
    hit = knowledge.urgent_override(text)
    if hit:
        override_category, pattern = hit
        category = triage.category
        if category not in SEVERE_CATEGORIES:
            category = Category(override_category)
        reason = f"override:{pattern}"
        if triage.urgency_reason == FALLBACK_REASON:
            reason += f"; {FALLBACK_REASON}"  # keep the parse failure visible
        update.update(
            relevant=True,
            category=category,
            urgency=Urgency.URGENT,
            urgency_reason=reason,
        )
    merged = triage.model_copy(update=update)
    if merged.relevant and merged.category in SEVERE_CATEGORIES and not hit:
        model_reason = merged.urgency_reason.strip()
        merged = merged.model_copy(update={
            "urgency": Urgency.URGENT,
            "urgency_reason": f"{SEVERE_REASON}: {model_reason}" if model_reason else SEVERE_REASON,
        })
    if merged.category in SEVERE_CATEGORIES or not merged.relevant:
        merged = merged.model_copy(update={"reply_appropriate": False})
    return merged


# --- Agent -----------------------------------------------------------------------


def _to_triage(answer: TriageAnswer, mention: Mention, model: str) -> Triage:
    return Triage(
        mention_id=mention.id or 0,
        relevant=answer.relevant,
        subject_type=answer.subject_type,
        subject=answer.subject.strip()[:200],
        competitor=_canonical(answer.competitor, knowledge.competitor_lookup()),
        # A WellPeps SKU only when the mention is about WellPeps; otherwise
        # "semaglutide" in a post is the drug, not our Oral Semaglutide.
        product=(_canonical(answer.product, knowledge.product_lookup())
                 if _about_wellpeps(answer, mention) else ""),
        drug=_drug(answer.drug, _post_text(mention)),
        category=answer.category,
        subtype=normalize_subtype(answer.subtype),
        intents=answer.intents or [],
        unmet_need=answer.unmet_need or "",
        need_clarity=answer.need_clarity,
        useful_contribution=answer.useful_contribution,
        sentiment=answer.sentiment_label,
        sentiment_score=answer.sentiment,
        urgency=answer.urgency,
        urgency_reason=answer.urgency_reason.strip()[:300],
        reply_appropriate=answer.reply_appropriate,
        phrases=_verbatim_phrases(answer.phrases, mention.text),
        model=model,
    )


def _fallback(mention: Mention, model: str) -> Triage:
    """Conservative result when the model can't be parsed: keep it, flag it."""
    return Triage(
        mention_id=mention.id or 0,
        relevant=True,
        category=Category.OTHER,
        urgency=Urgency.HIGH,
        urgency_reason=FALLBACK_REASON,
        reply_appropriate=False,
        model=model,
    )


class Triager:
    """Classifies mentions through a brain-like object.

    ``brain`` is duck-typed: it needs
    ``async think_json(prompt, session_id=None, agent="", task="")`` and may
    offer ``model_for(agent, task)`` (used only to label the result).
    """

    def __init__(self, brain, max_attempts: int = MAX_ATTEMPTS):
        self.brain = brain
        self.max_attempts = max(1, int(max_attempts))

    def _model_name(self) -> str:
        model_for = getattr(self.brain, "model_for", None)
        try:
            return (model_for(AGENT, TASK) or "") if callable(model_for) else ""
        except Exception:
            return ""

    async def _ask(self, prompt: str) -> tuple[TriageAnswer | None, str]:
        try:
            raw = await self.brain.think_json(prompt, agent=AGENT, task=TASK)
        except Exception as exc:
            return None, f"brain error: {exc}"
        if not isinstance(raw, dict):
            return None, "answer was not a JSON object"
        try:
            return TriageAnswer.model_validate(raw), ""
        except ValidationError as exc:
            return None, f"schema error: {exc.error_count()} invalid field(s)"

    async def triage(self, mention: Mention) -> Triage:
        prompt = build_prompt(mention)
        model = self._model_name()
        result: Triage | None = None
        for attempt in range(1, self.max_attempts + 1):
            answer, problem = await self._ask(prompt)
            if answer is not None:
                result = _to_triage(answer, mention, model)
                break
            logger.warning(f"triage attempt {attempt} for mention {mention.id} invalid: {problem}")
            prompt = (
                f"{prompt}\n\nYour previous answer was invalid ({problem}). "
                "Return one JSON object with exactly the fields listed above."
            )
        if result is None:
            result = _fallback(mention, model)
        return apply_safety_net(result, mention.text)


# --- Batch ------------------------------------------------------------------------

BudgetHook = Callable[[], bool | Awaitable[bool]]
# Opens/pages an escalation for (mention, triage); see harvey.escalation.escalate.
EscalateHook = Callable[[Mention, Triage], Awaitable[object]]


@dataclass
class TriageReport:
    processed: int = 0
    triaged: int = 0
    dropped: int = 0
    escalated: int = 0         # severe escalations opened (status escalated, or triaged for a boundary reply)
    fallbacks: int = 0
    errors: int = 0
    paged: int = 0             # escalations whose Slack page went out
    screened: int = 0          # mentions given the independent safety screen
    budget_exhausted: bool = False


def route_status(triage: Triage) -> MentionStatus:
    """Status after triage. ``escalation_kind`` is the single source of truth
    for paging: a severe kind leaves the reply queue (``escalated``) unless
    the rules of engagement give it an approved boundary reply (an adverse
    event about WellPeps, an emergency, a media inquiry, a billing complaint:
    config/engagement_guide.yaml ``reply: boundary_only``). Those are paged
    AND stay ``triaged`` so the guide's approved public reply is drafted for a
    human (clinical, for adverse events) to approve. A viral negative is paged
    but stays ``triaged`` too."""
    from harvey import engagement  # late: engagement imports compliance -> knowledge

    if not triage.relevant:
        return MentionStatus.DROPPED
    if escalation_kind(triage) in SEVERE_KINDS and engagement.reply_mode(triage) != "boundary_only":
        return MentionStatus.ESCALATED
    return MentionStatus.TRIAGED


def needs_screen(triage: Triage, text: str) -> bool:
    """Run the safety screen on health-related mentions not already escalated
    (a severe kind is escalated even when it stays ``triaged`` for its
    approved boundary reply)."""
    return escalation_kind(triage) not in SEVERE_KINDS and mentions_health_term(text)


async def _within_budget(hook: BudgetHook | None) -> bool:
    if hook is None:
        return True
    result = hook()
    if inspect.isawaitable(result):
        result = await result
    return bool(result)


def _verdict(triage: Triage, screen: ScreenResult | None = None) -> dict:
    verdict = {
        "relevant": triage.relevant,
        "category": triage.category.value,
        "subtype": triage.subtype,
        "protocol_decision": triage.protocol_decision,
        "protocol_route": triage.protocol_route,
        "opportunity_score": triage.opportunity_score,
        "urgency": triage.urgency.value,
        "urgency_reason": triage.urgency_reason,
        "reply_appropriate": triage.reply_appropriate,
        "model": triage.model,
    }
    if screen is not None:
        verdict["safety_screen"] = screen.as_dict()
    return verdict


async def _record(
    state, mention: Mention, triage: Triage, escalate: EscalateHook | None = None,
    screen: ScreenResult | None = None,
) -> tuple[MentionStatus, object]:
    """Save, audit, escalate, then move the status.

    The escalation runs before the status change: if it fails, the mention
    stays ``new`` and the whole thing is retried next cycle (escalate is
    idempotent, so a retry never double-pages).
    """
    await state.save_triage(triage)
    await state.append_audit(AuditEvent(
        mention_id=mention.id, event=AuditEventType.TRIAGED, actor=AGENT,
        verdict=_verdict(triage, screen), permalink=mention.url,
    ))
    status = route_status(triage)
    escalation = None
    if escalate is not None and status is not MentionStatus.DROPPED and escalation_kind(triage):
        escalation = await escalate(mention, triage)
    elif status is MentionStatus.ESCALATED or escalation_kind(triage) in SEVERE_KINDS:
        # No escalation hook (tests, one-off tools): the status and this
        # audit event are the whole escalation.
        await state.append_audit(AuditEvent(
            mention_id=mention.id, event=AuditEventType.ESCALATED, actor=AGENT,
            verdict={"kind": triage.category.value, "reason": triage.urgency_reason},
            permalink=mention.url,
        ))
    await state.set_mention_status(mention.id, status)
    return status, escalation


async def _triage_one(state, triager, mention, report: "TriageReport", escalate, screen) -> None:
    try:
        triage = await triager.triage(mention)
        screened = None
        if screen is not None and needs_screen(triage, f"{mention.title}\n{mention.text}"):
            screened = await screen.screen(mention)
            triage = apply_screen(triage, screened)
            report.screened += 1
        # The competitor / switching protocol's decision sequence (pure
        # Python, after every model call): its ESCALATE route feeds
        # escalation_kind, its classification the situation match.
        triage = await protocol.apply(state, mention, triage)
        status, escalation = await _record(state, mention, triage, escalate, screened)
    except Exception as exc:
        report.errors += 1
        logger.error(f"triage failed for mention {mention.id}: {exc}", exc_info=True)
        return
    report.processed += 1
    report.fallbacks += int(FALLBACK_REASON in triage.urgency_reason)
    report.paged += int(getattr(escalation, "notified_at", None) is not None)
    if status is MentionStatus.DROPPED:
        report.dropped += 1
    elif status is MentionStatus.ESCALATED or escalation_kind(triage) in SEVERE_KINDS:
        # Severe escalations, including those kept ``triaged`` for the
        # guide's approved boundary reply (they are paged all the same).
        report.escalated += 1
    else:
        report.triaged += 1


async def triage_batch(
    state,
    triager: Triager,
    limit: int = 25,
    budget_ok: BudgetHook | None = None,
    escalate: EscalateHook | None = None,
    screen=None,
    concurrency: int = 1,
) -> TriageReport:
    """Triage up to ``limit`` status=new mentions, oldest first.

    ``escalate`` is called for every mention that needs an escalation
    (severe categories, and urgent complaints about WellPeps, which stay
    ``triaged``). main.py passes ``harvey.escalation.escalate`` bound to the
    state, notifier, and config.

    ``screen`` (a ``SafetyScreen``, or None to skip) is a second, narrow
    model call on every health-related mention that triage didn't already
    escalate: a prompt-injected triage answer can't suppress an adverse
    event escalation on its own. See harvey/agents/safety_screen.py.

    ``budget_ok`` is checked before each mention; when it returns False the
    batch stops and the rest stay ``new`` for the next cycle. A mention whose
    bookkeeping fails is logged, counted, and left ``new`` to retry later.

    ``concurrency`` > 1 (scripts/seed_demo.py --claude only) triages different
    threads side by side, one mention per thread at a time (harvey/batching.py).
    """
    report = TriageReport()
    pending = await state.list_mentions(status=MentionStatus.NEW, limit=limit, oldest_first=True)

    async def one(mention) -> bool:
        if not await _within_budget(budget_ok):
            report.budget_exhausted = True
            logger.info("triage paused: Claude budget exhausted")
            return False
        await _triage_one(state, triager, mention, report, escalate, screen)
        return True

    if concurrency <= 1:
        for mention in pending:
            if not await one(mention):
                break
        return report
    await run_by_thread(pending, lambda m: communities.thread_key(m.url), one, concurrency)
    return report
