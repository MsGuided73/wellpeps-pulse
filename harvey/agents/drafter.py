"""Drafter agent: one candidate reply built only from approved claims.

Flow for ``Drafter.draft``:
1. Pick candidate claims: those for the triaged product (else the products
   the triaged drug maps to) first, then the general (``"*"``) ones, capped
   at ``MAX_CLAIMS``. For ``purchase_intent`` and ``question`` the playbook
   claims always come first: disclosure, the program's guide claim (the most
   specific one whose text the R38 toggle allows, else the guide series),
   the provider checklist, follow-up care, provider-determines. Only claim
   ids, texts and (for claims with a ``link_id``) the tracked registry URL
   reach the prompt (never prices or anything cost-related).
2. Fill prompts/draft.md (mention in nonce-tagged untrusted delimiters, the
   shared rule list from prompts/reply_rules.md, and a "never write" list of
   plain phrases from the compliance rules' ``examples``) and ask for JSON.
   A redraft (see harvey/drafting.py) appends why the last draft was blocked.
3. Validate. One retry on invalid output; then give up with an empty reply
   and ``needs_human_reason="draft_failed"`` so a human picks it up.
4. Keep only claim ids that were offered; the rest are reported as dropped.
5. Rewrite any registry link in the reply to this mention's tracked (UTM)
   URL, so the URL a human copies is always the canonical one.

The drafter never decides whether a reply is safe. The compliance filter,
the adversarial reviewer, and a human do that downstream.
"""

import logging
from dataclasses import dataclass, field, replace

from pydantic import BaseModel, ConfigDict, ValidationError

from harvey import knowledge as knowledge_module
from harvey import links
from harvey.agents import prompting
from harvey.compliance import names_medication
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
REDDIT_MAX_WORDS = 90
PLAYBOOK_CATEGORIES = frozenset({"purchase_intent", "question"})
DISCLOSURE_CLAIM = "CLM-R3-DISCLOSURE"
# The playbook's fixed claims, in reply order (the guide claim goes right
# after the disclosure; see candidate_claims).
PLAYBOOK_CLAIMS = (DISCLOSURE_CLAIM, "CLM-R7-PROVIDER-CHECKLIST", "CLM-PRICE-FOLLOWUP",
                   "CLM-R22-PROVIDER-DETERMINES")
SERIES_GUIDE_CLAIM = "CLM-EDU-GUIDES"
GUIDE_LINK_PLACEHOLDER = "<guide link>"


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


def _about(product: str, drug: str) -> set[str]:
    """WellPeps products a mention is about: its product, else its drug's."""
    if product:
        return {product}
    return set(knowledge_module.products_for_drug(drug))


def guide_claim(product: str = "", drug: str = "", claims=None) -> Claim | None:
    """The guide claim for the program discussed: the most specific linked
    claim covering the product/drug whose text the R38 toggle allows; else
    the guide-series claim; None if neither is in the pool."""
    pool = list(knowledge_module.claims() if claims is None else claims)
    program = knowledge_module.program_for(product, drug)
    in_program = {p.name for p in knowledge_module.products().products if program and p.category == program}
    about = _about(product, drug) or in_program
    forbid = knowledge_module.compliance_rules().toggles.forbid_medication_names_in_replies
    fits = [c for c in pool if c.link_id and "*" not in c.products and about & set(c.products)
            and not (forbid and names_medication(c.text))]
    fits.sort(key=lambda c: len(c.products))  # stable: most specific first
    if fits:
        return fits[0]
    return next((c for c in pool if c.id == SERIES_GUIDE_CLAIM), None)


def _category(category) -> str:
    return getattr(category, "value", category) or ""


def candidate_claims(product: str, claims=None, *, drug: str = "", category=None) -> list[Claim]:
    """Claims usable for a mention about ``product`` ("" = no product) or
    ``drug``; the playbook claims come first for purchase_intent / question."""
    pool = list(knowledge_module.claims() if claims is None else claims)
    about = _about(product, drug)
    specific = [c for c in pool if about & set(c.products)]
    general = [c for c in pool if "*" in c.products and c not in specific]
    ordered = specific + general
    if _category(category) in PLAYBOOK_CATEGORIES:
        by_id = {c.id: c for c in pool}
        fixed = [by_id[cid] for cid in PLAYBOOK_CLAIMS if cid in by_id]
        guide = guide_claim(product, drug, pool)
        first = fixed[:1] + ([guide] if guide else []) + fixed[1:]
        ordered = first + [c for c in ordered if c not in first]
    unique: dict[str, Claim] = {}
    for claim in ordered:
        unique.setdefault(claim.id, claim)
    return list(unique.values())[:MAX_CLAIMS]


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


def _claim_line(claim: Claim, mention: Mention) -> str:
    line = f"- [{claim.id}] {claim.text}"
    link = knowledge_module.links_by_id().get(claim.link_id or "")
    if link is not None:
        line += f"\n  Link for this claim: {links.tracked_url_for(link, mention)}"
    return line


def _claims_block(claims: list[Claim], mention: Mention) -> str:
    return "\n".join(_claim_line(c, mention) for c in claims) or "(none)"


def examples_block(examples=None) -> str:
    """The few-shot examples (config/reply_examples.yaml), registry URLs masked."""
    examples = knowledge_module.reply_examples().examples if examples is None else examples
    blocks = []
    for ex in examples:
        reply = links.mask_registry_links(ex.reply, GUIDE_LINK_PLACEHOLDER)
        blocks.append(f"Example ({ex.category}, {ex.platform}) post: {ex.post}\n"
                      f"Reply: {reply}\nclaim_ids: {', '.join(ex.claim_ids)}")
    return "\n\n".join(blocks) or "(none)"


def length_rule(platform: str) -> str:
    max_chars = knowledge_module.compliance_rules().limits.max_chars_for(platform)
    if platform == "reddit":
        return f"under {REDDIT_MAX_WORDS} words and at most {max_chars} characters"
    return f"at most {max_chars} characters"


def build_prompt(mention: Mention, triage: Triage, claims: list[Claim], nonce: str | None = None) -> str:
    platform = mention.platform.value
    return prompting.render(PROMPT_PATH, {
        "claims": _claims_block(claims, mention),
        "examples": examples_block(),
        "never_write": _never_write_block(never_write_phrases(knowledge_module.compliance_rules())),
        "rules": RULES_PATH.read_text(encoding="utf-8").strip(),
        "length_rule": length_rule(platform),
        "platform": platform,
        "category": triage.category.value,
        "product": triage.product or "none",
        "drug": triage.drug or "none",
        "program": knowledge_module.program_for(triage.product, triage.drug) or "unknown",
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
        claims = candidate_claims(triage.product, self.knowledge.claims(), drug=triage.drug,
                                  category=triage.category)
        offered = [c.id for c in claims]
        model = _model_name(self.brain, AGENT, TASK)
        prompt = build_prompt(mention, triage, claims)
        if feedback:
            prompt += _feedback_block(feedback)
        attempts = self.max_attempts if max_calls is None else max(1, min(int(max_calls), self.max_attempts))
        for attempt in range(1, attempts + 1):
            answer, problem = await self._ask(prompt)
            if answer is not None:
                proposal = self._proposal(answer, offered, model, calls=attempt)
                return replace(proposal, reply=links.track_registry_links(proposal.reply, mention))
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
