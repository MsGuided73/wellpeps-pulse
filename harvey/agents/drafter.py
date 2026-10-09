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

Smart Patient's Guide (binding user instruction, 2026-10-07): in answering
situations ``guidance`` names the single most relevant guide and the chapter(s)
that answer this question (harvey/guides.py). Its claim is offered right after
the disclosure / status claims, with its chapters as approved guide content, and
the prompt requires one "how it helps" sentence naming the chapter, the
gated-download disclosure ("free, it asks for your email") and the tracked link
(or the guide's name without a link where links are not allowed).

Rules of engagement (docs/RULES-OF-ENGAGEMENT.md): ``guidance``
(harvey.engagement.DraftGuidance) carries the situation, its template (Guide
§25 A-E), the persona's exact opening disclosure ("I work with WellPeps." by
default), the situation's preferred claims (offered first), and whether this
community allows a link or promotion at all. With no link allowed, claim
links are not shown to the model. Influencer-only wording and guide
templates with an unfilled [slot] are never offered.

The drafter never decides whether a reply is safe. The compliance filter,
the adversarial reviewer, and a human do that downstream.
"""

import logging
from dataclasses import dataclass, field, replace

from pydantic import BaseModel, ConfigDict, ValidationError

from harvey import knowledge as knowledge_module
from harvey import guides, links
from harvey.agents import prompting
from harvey.agents.acknowledger import AGENT as ACK_AGENT
from harvey.agents.acknowledger import TASK as ACK_TASK
from harvey.agents.acknowledger import Acknowledger
from harvey.compliance import names_medication
from harvey.models import Mention, Triage
from harvey.models.knowledge import Claim

logger = logging.getLogger("harvey.agents.drafter")

PROMPT_PATH = prompting.PROMPTS_DIR / "draft.md"
RULES_PATH = prompting.PROMPTS_DIR / "reply_rules.md"
AGENT = "drafter"
TASK = "reply"
MAX_CLAIMS = 12
MAX_ATTEMPTS = 2  # first try + one retry
FAILED_REASON = "draft_failed"
NO_REPLY_REASON = "drafter returned no reply"
MAX_RATIONALE_CHARS = 500
REDDIT_MAX_WORDS = 90
# Room for the required guide sentence (user instruction 2026-10-07).
REDDIT_MAX_WORDS_WITH_GUIDE = 110
PLAYBOOK_CATEGORIES = frozenset({"purchase_intent", "question"})
# The Approved Messaging & Response Guide §4 primary form ("I work with
# WellPeps."). The older "Disclosure: I work with WellPeps, so I am not
# neutral." (CLM-R3-DISCLOSURE) stays allowed but is no longer the default.
DISCLOSURE_CLAIM = "CLM-AMG-04-WORK-WITH"
# The playbook's fixed claims, in reply order: disclosure, a provider-neutral
# answer, the approved ongoing-support fact (used only when WellPeps is
# relevant), the provider-determines line (Operations Manual §18.1). Since the
# rules of engagement (2026-10-07) the guide claim is no longer pushed second
# and no price claim leads: a resource is optional and only when it directly
# answers (Protocol §8), a WellPeps fact only when asked or when alternatives
# are requested (Protocol §1, §7). Both stay in the pool.
PLAYBOOK_CLAIMS = (DISCLOSURE_CLAIM, "CLM-R7-PROVIDER-CHECKLIST", "CLM-LR-02-ONGOING-SUPPORT",
                   "CLM-R22-PROVIDER-DETERMINES")
SERIES_GUIDE_CLAIM = "CLM-EDU-GUIDES"
GUIDE_LINK_PLACEHOLDER = "<guide link>"
# Approved Messaging & Response Guide §1-§3 ("Answer the actual question"; the
# writing standard) and the switching protocol's "respond to the unmet need".
ANSWER_FIRST = ("Answer first: the sentence right after the disclosure directly answers the poster's actual "
                "question in their terms, from the claims; boundary or disclaimer wording only where the "
                "question needs it and never as the opener; then one useful next step.")


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


def offerable(claim: Claim) -> bool:
    """Claims the drafter may be shown: not Brand Ambassador / partner
    wording (Pulse never drafts as an influencer) and not a guide template
    with an unfilled [slot] (a human fills those)."""
    return not claim.influencer_only and not claim.has_placeholder


def programs_for(product: str = "", drug: str = "", text: str = "") -> list[str]:
    """Programs (products.yaml categories) a mention is about: its product's or
    drug's program, plus any program the post names ("hair" -> Hair Restoration)."""
    found = [knowledge_module.program_for(product, drug), *knowledge_module.programs_in_text(text)]
    return list(dict.fromkeys(p for p in found if p))


def _post_text(mention: Mention) -> str:
    return " ".join(part for part in (mention.title or "", mention.text or "") if part)


def _with_status_first(ordered: list[Claim], status: list[Claim]) -> list[Claim]:
    """The program-status claims right after a leading disclosure claim, so a
    "is X live yet?" post always has the answer on offer (never cut by MAX_CLAIMS)."""
    if not status:
        return ordered
    rest = [c for c in ordered if c not in status]
    head = rest[:1] if rest and rest[0].id.startswith(("CLM-AMG-04-", "CLM-R3-DISCLOSURE")) else []
    return head + status + rest[len(head):]


def _with_guide(ordered: list[Claim], guide_id: str) -> list[Claim]:
    """The required guide claim right after the disclosure and status claims
    (never cut by MAX_CLAIMS)."""
    guide = next((c for c in ordered if c.id == guide_id), None)
    if guide is None:
        return ordered
    rest = [c for c in ordered if c is not guide]
    lead = 0
    while lead < len(rest) and (rest[lead].id.startswith(("CLM-AMG-04-", "CLM-R3-DISCLOSURE"))
                                or rest[lead].program):
        lead += 1
    return rest[:lead] + [guide] + rest[lead:]


def candidate_claims(product: str, claims=None, *, drug: str = "", category=None,
                     preferred=(), disclosure: str = DISCLOSURE_CLAIM, programs=(),
                     guide_claim_id: str = "") -> list[Claim]:
    """Claims usable for a mention about ``product`` ("" = no product) or
    ``drug``; the playbook claims come first for purchase_intent / question,
    and the situation's ``preferred`` claims (with the ``disclosure`` claim
    first) before everything else. ``programs``: the programs the post is
    about; their program-status claims are offered right after the disclosure."""
    pool = [c for c in (knowledge_module.claims() if claims is None else claims) if offerable(c)]
    status = [c for p in programs for c in pool if p and c.program == p]
    about = _about(product, drug)
    specific = [c for c in pool if about & set(c.products)]
    general = [c for c in pool if "*" in c.products and c not in specific]
    ordered = specific + general
    if _category(category) in PLAYBOOK_CATEGORIES:
        by_id = {c.id: c for c in pool}
        fixed = [by_id[cid] for cid in PLAYBOOK_CLAIMS if cid in by_id]
        guide = guide_claim(product, drug, pool)
        first = fixed + ([guide] if guide else [])   # offered, never required (Protocol §8)
        ordered = first + [c for c in ordered if c not in first]
    if preferred:
        by_id = {c.id: c for c in pool}
        lead = [by_id[cid] for cid in (disclosure, *preferred) if cid in by_id]
        ordered = lead + [c for c in ordered if c not in lead]
    ordered = _with_status_first(ordered, status)
    if guide_claim_id:
        by_id = {c.id: c for c in pool}
        if guide_claim_id in by_id and by_id[guide_claim_id] not in ordered:
            ordered = [*ordered, by_id[guide_claim_id]]
        ordered = _with_guide(ordered, guide_claim_id)
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


def _claim_line(claim: Claim, mention: Mention, allow_link: bool = True) -> str:
    line = f"- [{claim.id}] {claim.text}"
    guide = guides.by_claim_id(claim.id)
    if guide is not None and (guide.chapters or guide.landing):
        line += f"\n  Chapters in this guide (approved guide content; you may name them): {guides.chapter_line(guide)}"
    link = knowledge_module.links_by_id().get(claim.link_id or "") if allow_link else None
    if link is not None:
        line += f"\n  Link for this claim: {links.tracked_url_for(link, mention)}"
    return line


def _claims_block(claims: list[Claim], mention: Mention, allow_link: bool = True) -> str:
    return "\n".join(_claim_line(c, mention, allow_link) for c in claims) or "(none)"


def guide_lines(guidance) -> list[str]:
    """The Smart Patient's Guide instruction for this mention (WellPeps
    2026-10-07: point to a guide when a specific chapter materially helps)."""
    mode = getattr(guidance, "guide_mode", "none")
    if mode == "forbidden":
        why = getattr(guidance, "guide_why", "") or "excluded situation"
        return [f"- No Smart Patient's Guide, resource or link in this reply ({why})."]
    if mode == "omit":
        return ["- No Smart Patient's Guide in this reply: no guide chapter answers this question, and WellPeps "
                "includes a guide only when a specific chapter materially helps."]
    if not getattr(guidance, "guide_required", False):
        return []
    chapters = list(guidance.guide_chapters) or ["questions to ask before choosing a provider"]
    named = f'"{chapters[0]}"' + (f' (or, if it fits better, "{chapters[1]}")' if len(chapters) > 1 else "")
    lines = [f"- Smart Patient's Guide (a chapter answers this question, so include it): after the answer, add "
             "ONE sentence that points to "
             f"the single most relevant guide, {guidance.guide_title} (cite {guidance.guide_claim_id}), and says "
             f"concretely how it helps with THIS poster's question by naming the guide chapter {named}. Tie the "
             "chapter to what they asked (since you're comparing what's included, its chapter 'What's "
             "actually included in the price' walks through what to check); never a generic 'check out our "
             "guide'. Describe the chapter only by its title plus a neutral phrase such as 'walks through "
             "what to check' or 'explains the differences'; never claim it covers anything else. Write the "
             f"gate once, exactly: 'our free {guidance.guide_short} (it asks for your email)'. Phrase it as "
             "help, not a sales pitch; keep answer-first: answer -> guide and how it helps. Vary the wording "
             "naturally; never a stock sentence."]
    if guidance.guide_mode == "link":
        lines.append(f"- Put the link shown under {guidance.guide_claim_id}, copied exactly, at the end of the "
                     "guide sentence. It is the only link in the reply.")
    else:
        lines.append(f"- No link here ({guidance.guide_why}): name it as 'our free {guidance.guide_short} on the "
                     "WellPeps website' and never write a URL or a domain.")
    return lines


def engagement_block(guidance) -> str:
    """The rules-of-engagement section of the prompt (all from config, never
    from the mention)."""
    if guidance is None:
        return ("- Open with exactly this sentence: \"I work with WellPeps.\"\n"
                f"- {ANSWER_FIRST}\n"
                "- Educate before promoting.")
    lines = [f"- Situation: {guidance.label}."]
    if guidance.template:
        lines.append(f"- Shape (Approved Messaging & Response Guide, Template {guidance.template}): "
                     f"{guidance.template_structure} Fill the [bracketed] parts ONLY with approved claim "
                     "texts; never leave brackets in the reply.")
    if guidance.notes:
        lines.append(f"- {guidance.notes}")
    lines.append(f"- Open with exactly this sentence: \"{guidance.disclosure}\" "
                 f"(cite {guidance.disclosure_claim}).")
    lines.append(f"- {ANSWER_FIRST}")
    if guidance.preferred_claims:
        lines.append("- Prefer these claims for this situation: " + ", ".join(guidance.preferred_claims)
                     + " (only where they answer what was asked; a claim that answers the specific "
                       "question comes first).")
    required = getattr(guidance, "guide_required", False)
    if required and guidance.guide_mode == "link":
        lines.append("- One link at most: the guide link below.")
    elif guidance.allow_link:
        lines.append("- A link is allowed only if it directly answers the question; most replies need none.")
    else:
        lines.append(f"- No link in this reply ({guidance.link_note}).")
    if guidance.education_only:
        offer = ("no assessment offer; the guide sentence below is education, not promotion" if required
                 else "no guide or assessment offer")
        lines.append(f"- Education only: no call to action, no pricing, {offer} ({guidance.promotion_note}).")
    if getattr(guidance, "protocol_label", ""):
        lines.append(f"- Competitor / switching protocol: {guidance.protocol_label}. Respond to the unmet need "
                     f"({guidance.need_focus or 'answer the actual question'}); never name, repeat or attack "
                     "the other provider, never treat their complaint as fact, never infer the other provider "
                     "failed clinically, and never promise the same medication, dose, labs, response times or "
                     "a smooth transfer.")
        if guidance.brand_limits:
            lines.append(f"- WellPeps presence ({guidance.brand_mode.replace('_', ' ')}): {guidance.brand_limits}.")
        if required:
            lines.append("- Aim for 40 to 110 words. A guide chapter answers this question, so include the guide "
                         "(WellPeps 2026-10-07: a guide when a chapter materially helps; Protocol §8: never in "
                         "safety or clinical replies).")
        else:
            lines.append("- Aim for 40 to 90 words. A resource is optional and never required to get the answer.")
    lines.extend(guide_lines(guidance))
    return "\n".join(lines)


def examples_block(examples=None) -> str:
    """The few-shot examples (config/reply_examples.yaml), registry URLs masked."""
    examples = knowledge_module.reply_examples().examples if examples is None else examples
    blocks = []
    for ex in examples:
        reply = links.mask_registry_links(ex.reply, GUIDE_LINK_PLACEHOLDER)
        blocks.append(f"Example ({ex.category}, {ex.platform}) post: {ex.post}\n"
                      f"Reply: {reply}\nclaim_ids: {', '.join(ex.claim_ids)}")
    return "\n\n".join(blocks) or "(none)"


def length_rule(platform: str, guidance=None) -> str:
    max_chars = knowledge_module.compliance_rules().limits.max_chars_for(platform)
    if platform == "reddit":
        words = REDDIT_MAX_WORDS_WITH_GUIDE if getattr(guidance, "guide_required", False) else REDDIT_MAX_WORDS
        return f"under {words} words and at most {max_chars} characters"
    return f"at most {max_chars} characters"


def build_prompt(mention: Mention, triage: Triage, claims: list[Claim], nonce: str | None = None,
                 guidance=None) -> str:
    platform = mention.platform.value
    allow_link = guidance is None or guidance.allow_link
    return prompting.render(PROMPT_PATH, {
        "claims": _claims_block(claims, mention, allow_link),
        "engagement": engagement_block(guidance),
        "examples": examples_block(),
        "never_write": _never_write_block(never_write_phrases(knowledge_module.compliance_rules())),
        "rules": RULES_PATH.read_text(encoding="utf-8").strip(),
        "length_rule": length_rule(platform, guidance),
        "platform": platform,
        "category": triage.category.value,
        "product": triage.product or "none",
        "drug": triage.drug or "none",
        "program": ", ".join(programs_for(triage.product, triage.drug, _post_text(mention))) or "unknown",
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

    def __init__(self, brain, knowledge=None, max_attempts: int = MAX_ATTEMPTS, acknowledgements: bool = False):
        """``acknowledgements``: approved (boundary-only) replies may get one
        short, validated acknowledgement clause (harvey/agents/acknowledger.py)."""
        self.brain = brain
        self.knowledge = knowledge or knowledge_module
        self.max_attempts = max(1, int(max_attempts))
        self.acknowledgements = bool(acknowledgements)
        self._acknowledger = Acknowledger(brain) if self.acknowledgements else None

    def acknowledgement_model(self) -> str:
        return _model_name(self.brain, ACK_AGENT, ACK_TASK)

    async def acknowledge(self, mention: Mention, situation_label: str) -> tuple[str, list[str]]:
        """(clause, problems): one validated acknowledgement clause, or "" with why."""
        if self._acknowledger is None:
            return "", ["acknowledgements disabled"]
        return await self._acknowledger.acknowledge(mention, situation_label)

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
        guidance=None,
    ) -> DraftProposal:
        """Draft one reply.

        ``feedback``: reasons a previous draft was blocked (compliance filter
        hits); they are appended after the mention so the model rewrites.
        ``max_calls`` caps model calls for this draft (default
        ``max_attempts``); ``DraftProposal.calls`` reports how many were used.
        ``guidance``: the rules-of-engagement guidance for this mention
        (harvey.engagement.guidance_for), or None for the defaults.
        """
        claims = candidate_claims(
            triage.product, self.knowledge.claims(), drug=triage.drug, category=triage.category,
            preferred=tuple(getattr(guidance, "preferred_claims", ()) or ()),
            disclosure=getattr(guidance, "disclosure_claim", DISCLOSURE_CLAIM) or DISCLOSURE_CLAIM,
            programs=programs_for(triage.product, triage.drug, _post_text(mention)),
            guide_claim_id=(guidance.guide_claim_id if getattr(guidance, "guide_required", False) else ""),
        )
        offered = [c.id for c in claims]
        model = _model_name(self.brain, AGENT, TASK)
        prompt = build_prompt(mention, triage, claims, guidance=guidance)
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
