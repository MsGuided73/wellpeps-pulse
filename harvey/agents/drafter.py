"""Drafter agent: one candidate reply built only from approved claims.

Flow for ``Drafter.draft``:
1. Pick candidate claims: those for the triaged product first, then the
   general (``"*"``) ones, capped at ``MAX_CLAIMS``. Only claim ids and
   texts reach the prompt (never prices or anything cost-related).
2. Fill prompts/draft.md (mention in nonce-tagged untrusted delimiters, the
   shared rule list from prompts/reply_rules.md, and a "never write" list of
   plain phrases from the compliance rules' ``examples``) and ask for JSON.
   A redraft (see harvey/drafting.py) appends why the last draft was blocked.
3. Validate. One retry on invalid output; then give up with an empty reply
   and ``needs_human_reason="draft_failed"`` so a human picks it up.
4. Keep only claim ids that were offered; the rest are reported as dropped.

The drafter never decides whether a reply is safe. The compliance filter,
the adversarial reviewer, and a human do that downstream.
"""

import logging
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, ValidationError

from harvey import knowledge as knowledge_module
from harvey.agents import prompting
from harvey.models import Mention, Triage
from harvey.models.knowledge import Claim

logger = logging.getLogger("harvey.agents.drafter")

PROMPT_PATH = prompting.PROMPTS_DIR / "draft.md"
RULES_PATH = prompting.PROMPTS_DIR / "reply_rules.md"
AGENT = "drafter"
TASK = "reply"
MAX_CLAIMS = 8
MAX_ATTEMPTS = 2  # first try + one retry
FAILED_REASON = "draft_failed"
NO_REPLY_REASON = "drafter returned no reply"
MAX_RATIONALE_CHARS = 500


class DraftAnswer(BaseModel):
    """The JSON shape the model must return (see prompts/draft.md)."""

    model_config = ConfigDict(extra="ignore")

    reply: str
    claim_ids: list = []
    rationale: str = ""
    needs_human_reason: str | None = None


@dataclass(frozen=True)
class DraftProposal:
    reply: str
    claim_ids: list[str]
    rationale: str = ""
    needs_human_reason: str | None = None
    model: str = ""
    offered_claim_ids: list[str] = field(default_factory=list)
    dropped_claim_ids: list[str] = field(default_factory=list)
    calls: int = 1  # drafter model calls spent on this proposal (incl. retries)


def candidate_claims(product: str, claims=None) -> list[Claim]:
    """Claims usable for a mention about ``product`` ("" = no product)."""
    pool = list(knowledge_module.claims() if claims is None else claims)
    specific = [c for c in pool if product and product in c.products]
    general = [c for c in pool if "*" in c.products and c not in specific]
    return (specific + general)[:MAX_CLAIMS]


def never_write_phrases(rules=None) -> list[str]:
    """Readable phrases the compliance filter blocks (R31 first).

    Built from the ``examples`` in config/compliance_rules.yaml, never from
    the raw regexes, so the drafter sees plain language.
    """
    rules = rules or knowledge_module.compliance_rules()
    phrases: list[str] = []
    for rule in (*rules.patient_confirmation, *rules.prohibited):
        for example in rule.examples:
            if example not in phrases:
                phrases.append(example)
    return phrases


def _never_write_block(phrases: list[str]) -> str:
    return "\n".join(f'- "{p}"' for p in phrases) or "(none)"


def _feedback_block(feedback: list[str]) -> str:
    reasons = "\n".join(f"- {line}" for line in feedback)
    return (
        "\n\n## Your previous draft was blocked\n\n"
        "Your previous draft was blocked because:\n"
        f"{reasons}\n\n"
        "Rewrite the reply avoiding these. If you can't, return an empty reply "
        "and say why in `needs_human_reason`."
    )


def _claims_block(claims: list[Claim]) -> str:
    return "\n".join(f"- [{c.id}] {c.text}" for c in claims) or "(none)"


def build_prompt(mention: Mention, triage: Triage, claims: list[Claim], nonce: str | None = None) -> str:
    platform = mention.platform.value
    max_chars = knowledge_module.compliance_rules().limits.max_chars_for(platform)
    return prompting.render(PROMPT_PATH, {
        "claims": _claims_block(claims),
        "never_write": _never_write_block(never_write_phrases(knowledge_module.compliance_rules())),
        "rules": RULES_PATH.read_text(encoding="utf-8").strip(),
        "max_chars": str(max_chars),
        "platform": platform,
        "category": triage.category.value,
        "product": triage.product or "none",
        "nonce": nonce or prompting.new_nonce(),
        "mention": prompting.mention_block(mention),
    })


def _model_name(brain, agent: str, task: str) -> str:
    model_for = getattr(brain, "model_for", None)
    try:
        return (model_for(agent, task) or "") if callable(model_for) else ""
    except Exception:
        return ""


def _split_claim_ids(raw: list, offered: list[str]) -> tuple[list[str], list[str]]:
    kept: list[str] = []
    dropped: list[str] = []
    for item in raw:
        cid = str(item).strip()
        if not cid:
            continue
        if cid in offered:
            if cid not in kept:
                kept.append(cid)
        elif cid not in dropped:
            dropped.append(cid)
    return kept, dropped


class Drafter:
    """Drafts replies through a brain-like object (``think_json``)."""

    def __init__(self, brain, knowledge=None, max_attempts: int = MAX_ATTEMPTS):
        self.brain = brain
        self.knowledge = knowledge or knowledge_module
        self.max_attempts = max(1, int(max_attempts))

    async def _ask(self, prompt: str) -> tuple[DraftAnswer | None, str]:
        try:
            raw = await self.brain.think_json(prompt, agent=AGENT, task=TASK)
        except Exception as exc:
            return None, f"brain error: {type(exc).__name__}"
        if not isinstance(raw, dict):
            return None, "answer was not a JSON object"
        try:
            return DraftAnswer.model_validate(raw), ""
        except ValidationError as exc:
            return None, f"schema error: {exc.error_count()} invalid field(s)"

    async def draft(
        self,
        mention: Mention,
        triage: Triage,
        feedback: list[str] | None = None,
        max_calls: int | None = None,
    ) -> DraftProposal:
        """Draft one reply.

        ``feedback``: reasons a previous draft was blocked (compliance filter
        hits); they are appended after the mention so the model rewrites.
        ``max_calls`` caps model calls for this draft (default
        ``max_attempts``); ``DraftProposal.calls`` reports how many were used.
        """
        claims = candidate_claims(triage.product, self.knowledge.claims())
        offered = [c.id for c in claims]
        model = _model_name(self.brain, AGENT, TASK)
        prompt = build_prompt(mention, triage, claims)
        if feedback:
            prompt += _feedback_block(feedback)
        attempts = self.max_attempts if max_calls is None else max(1, min(int(max_calls), self.max_attempts))
        for attempt in range(1, attempts + 1):
            answer, problem = await self._ask(prompt)
            if answer is not None:
                return self._proposal(answer, offered, model, calls=attempt)
            logger.warning(f"draft attempt {attempt} for mention {mention.id} invalid: {problem}")
            prompt = (
                f"{prompt}\n\nYour previous answer was invalid ({problem}). "
                "Return one JSON object with exactly the fields listed above."
            )
        return DraftProposal(reply="", claim_ids=[], needs_human_reason=FAILED_REASON,
                             model=model, offered_claim_ids=offered, calls=attempts)

    @staticmethod
    def _proposal(answer: DraftAnswer, offered: list[str], model: str, calls: int = 1) -> DraftProposal:
        reply = answer.reply.strip()
        kept, dropped = _split_claim_ids(answer.claim_ids, offered)
        reason = (answer.needs_human_reason or "").strip() or None
        if not reply:
            kept = []
            reason = reason or NO_REPLY_REASON
        if dropped:
            logger.warning(f"drafter cited claim ids that weren't offered: {dropped}")
        return DraftProposal(
            reply=reply,
            claim_ids=kept,
            rationale=answer.rationale.strip()[:MAX_RATIONALE_CHARS],
            needs_human_reason=reason,
            model=model,
            offered_claim_ids=offered,
            dropped_claim_ids=dropped,
            calls=calls,
        )
