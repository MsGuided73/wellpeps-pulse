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
   names, keep only phrases copied verbatim, force urgent + no-reply when a
   ``knowledge.urgent_override`` pattern matches, and never allow a reply on
   severe or irrelevant mentions.
"""

import inspect
import logging
import re
import secrets
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from harvey import knowledge
from harvey.models import (
    AuditEvent,
    AuditEventType,
    Category,
    Mention,
    MentionStatus,
    Triage,
    Urgency,
)
from harvey.paths import PROJECT_ROOT

logger = logging.getLogger("harvey.agents.triager")

PROMPT_PATH = PROJECT_ROOT / "prompts" / "triage.md"
AGENT = "triager"
TASK = "classify"

MAX_ATTEMPTS = 2           # first try + one retry
MAX_PHRASES = 5
MAX_PHRASE_CHARS = 120
MAX_MENTION_CHARS = 4000   # bound prompt size; triage never needs more
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
    category: Category
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
    }


def _mention_block(mention: Mention) -> str:
    text = mention.text[:MAX_MENTION_CHARS]
    return f"Title: {mention.title}\n\n{text}" if mention.title else text


def build_prompt(mention: Mention, nonce: str | None = None) -> str:
    """Fill prompts/triage.md for one mention.

    Substitution is single-pass, so placeholders inside the mention text are
    left as literal text rather than expanded.
    """
    template = PROMPT_PATH.read_text(encoding="utf-8")
    values = {
        **_name_lists(),
        "nonce": nonce or secrets.token_hex(8),
        "platform": mention.platform.value,
        "mention": _mention_block(mention),
    }
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values.get(m.group(1), m.group(0)), template)


# --- Safety net (pure) -----------------------------------------------------------


def _canonical(name: str | None, lookup) -> str:
    if not name or not isinstance(name, str):
        return ""
    return lookup.get(name.strip().lower(), "")


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


def apply_safety_net(triage: Triage, text: str) -> Triage:
    """Deterministic post-processing. Returns a new Triage."""
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
        product=_canonical(answer.product, knowledge.product_lookup()),
        category=answer.category,
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


@dataclass
class TriageReport:
    processed: int = 0
    triaged: int = 0
    dropped: int = 0
    escalated: int = 0
    fallbacks: int = 0
    errors: int = 0
    budget_exhausted: bool = False


def route_status(triage: Triage) -> MentionStatus:
    if not triage.relevant:
        return MentionStatus.DROPPED
    if triage.urgency is Urgency.URGENT and triage.category in SEVERE_CATEGORIES:
        return MentionStatus.ESCALATED
    return MentionStatus.TRIAGED


async def _within_budget(hook: BudgetHook | None) -> bool:
    if hook is None:
        return True
    result = hook()
    if inspect.isawaitable(result):
        result = await result
    return bool(result)


def _verdict(triage: Triage) -> dict:
    return {
        "relevant": triage.relevant,
        "category": triage.category.value,
        "urgency": triage.urgency.value,
        "urgency_reason": triage.urgency_reason,
        "reply_appropriate": triage.reply_appropriate,
        "model": triage.model,
    }


async def _record(state, mention: Mention, triage: Triage) -> MentionStatus:
    await state.save_triage(triage)
    await state.append_audit(AuditEvent(
        mention_id=mention.id, event=AuditEventType.TRIAGED, actor=AGENT,
        verdict=_verdict(triage), permalink=mention.url,
    ))
    status = route_status(triage)
    await state.set_mention_status(mention.id, status)
    if status is MentionStatus.ESCALATED:
        # TODO(Phase 5): create the escalations row (owner, SLA) and send the
        # Slack page (link + category only) here. Until then the status and
        # this audit event are the whole escalation.
        await state.append_audit(AuditEvent(
            mention_id=mention.id, event=AuditEventType.ESCALATED, actor=AGENT,
            verdict={"kind": triage.category.value, "reason": triage.urgency_reason},
            permalink=mention.url,
        ))
    return status


async def triage_batch(
    state, triager: Triager, limit: int = 25, budget_ok: BudgetHook | None = None
) -> TriageReport:
    """Triage up to ``limit`` status=new mentions, oldest first.

    ``budget_ok`` is checked before each mention; when it returns False the
    batch stops and the rest stay ``new`` for the next cycle. A mention whose
    bookkeeping fails is logged, counted, and left ``new`` to retry later.
    """
    report = TriageReport()
    pending = await state.list_mentions(status=MentionStatus.NEW, limit=limit, oldest_first=True)
    for mention in pending:
        if not await _within_budget(budget_ok):
            report.budget_exhausted = True
            logger.info("triage paused: Claude budget exhausted")
            break
        try:
            triage = await triager.triage(mention)
            status = await _record(state, mention, triage)
        except Exception as exc:
            report.errors += 1
            logger.error(f"triage failed for mention {mention.id}: {exc}", exc_info=True)
            continue
        report.processed += 1
        report.fallbacks += int(FALLBACK_REASON in triage.urgency_reason)
        if status is MentionStatus.DROPPED:
            report.dropped += 1
        elif status is MentionStatus.ESCALATED:
            report.escalated += 1
        else:
            report.triaged += 1
    return report
