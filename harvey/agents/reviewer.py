"""Adversarial reviewer: tries to find rule violations in one draft reply.

It sees only the draft, the platform, the mention (delimited as untrusted),
the texts of the claims the draft cites, and the rule list. It never
rewrites anything; it returns a verdict and reasons. Unparseable or invalid
output after one retry fails closed: ``reject`` with ``review_failed``.

Runs on a strong model (``reviewer`` -> sonnet in config.DEFAULT_MODELS;
config refuses haiku for it).
"""

import logging
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from harvey.agents import prompting
from harvey.agents.drafter import RULES_PATH, _model_name
from harvey.models import Mention, ReviewVerdict
from harvey.models.knowledge import Claim

logger = logging.getLogger("harvey.agents.reviewer")

PROMPT_PATH = prompting.PROMPTS_DIR / "review.md"
AGENT = "reviewer"
TASK = "adversarial"
MAX_ATTEMPTS = 2  # first try + one retry
FAILED_RULE = "review_failed"
MAX_EXPLANATION_CHARS = 300
MAX_REASONS = 10


class ReviewReason(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rule_id: str
    explanation: str


class ReviewAnswer(BaseModel):
    """The JSON shape the model must return (see prompts/review.md)."""

    model_config = ConfigDict(extra="ignore")

    verdict: Literal["pass", "reject", "needs_human"]
    reasons: list[ReviewReason] = []


@dataclass(frozen=True)
class ReviewResult:
    verdict: ReviewVerdict
    reasons: list[dict] = field(default_factory=list)  # [{rule_id, explanation}]
    model: str = ""


def _claim_line(claim: Claim) -> str:
    from harvey import guides

    line = f"- [{claim.id}] {claim.text}"
    guide = guides.by_claim_id(claim.id)
    if guide is not None and (guide.chapters or guide.landing):
        line += f"\n  Chapters in this guide (approved guide content): {guides.chapter_line(guide)}"
    return line


def _claims_block(claims: list[Claim]) -> str:
    return "\n".join(_claim_line(c) for c in claims) or "(none: the draft cites no claims)"


def guide_line(guidance=None) -> str:
    """What the reviewer checks about the Smart Patient's Guide reference."""
    mode = getattr(guidance, "guide_mode", "none")
    if mode == "forbidden":
        return ("no Smart Patient's Guide may appear in this reply "
                f"({getattr(guidance, 'guide_why', '') or 'excluded situation'}); a guide here is a violation")
    if mode == "omit":
        return ("no guide here: no guide chapter answers this question (WellPeps includes a guide only when a "
                "specific chapter materially helps); a guide that appears anyway must fit the post's program "
                "and name a real chapter")
    if not getattr(guidance, "guide_required", False):
        return "no guide reference is required here (if one appears, it must fit the post's program)"
    chapters = " / ".join(f'"{c}"' for c in guidance.guide_chapters) or "a chapter of the guide"
    how = "with its link" if guidance.guide_mode == "link" else "by name, without any link"
    return (f"EXPECTED (a chapter answers this question): the reply should point to {guidance.guide_title} {how} "
            "and say "
            f"concretely how it helps with this poster's question, naming a chapter such as {chapters}; "
            "it must say the guide is free and asks for an email. Its short name (\"our free "
            f"{guidance.guide_short}\") is enough, and a neutral phrase about the chapter (\"walks through what "
            "to check\") is not a new fact")


def situation_line(guidance=None) -> str:
    """The rules-of-engagement situation for the reviewer (from config only)."""
    if guidance is None:
        return "not classified (apply every check)"
    template = f"; Template {guidance.template} ({guidance.template_structure})" if guidance.template else ""
    limits = []
    if not guidance.allow_link:
        limits.append(f"no link allowed ({guidance.link_note})")
    if guidance.education_only:
        limits.append(f"education only ({guidance.promotion_note})")
    if getattr(guidance, "protocol_label", ""):
        limits.append(f"competitor/switching protocol {guidance.protocol_label}; WellPeps presence "
                      f"{guidance.brand_mode.replace('_', ' ')} ({guidance.brand_limits})")
    extra = f"; limits: {'; '.join(limits)}" if limits else ""
    return f"{guidance.label}{template}; required opening: \"{guidance.disclosure}\"{extra}"


def build_prompt(reply: str, platform: str, mention: Mention, claims: list[Claim], guidance=None) -> str:
    return prompting.render(PROMPT_PATH, {
        "situation": situation_line(guidance),
        "guide": guide_line(guidance),
        "rules": RULES_PATH.read_text(encoding="utf-8").strip(),
        "claims": _claims_block(claims),
        "platform": platform,
        "draft_nonce": prompting.new_nonce(),
        "draft": reply,
        "nonce": prompting.new_nonce(),
        "mention": prompting.mention_block(mention),
    })


def _failed(model: str) -> ReviewResult:
    return ReviewResult(
        verdict=ReviewVerdict.REJECT,
        reasons=[{"rule_id": FAILED_RULE, "explanation": "reviewer output was unusable; failing closed"}],
        model=model,
    )


class Reviewer:
    def __init__(self, brain, max_attempts: int = MAX_ATTEMPTS):
        self.brain = brain
        self.max_attempts = max(1, int(max_attempts))

    async def _ask(self, prompt: str) -> tuple[ReviewAnswer | None, str]:
        try:
            raw = await self.brain.think_json(prompt, agent=AGENT, task=TASK)
        except Exception as exc:
            return None, f"brain error: {type(exc).__name__}"
        if not isinstance(raw, dict):
            return None, "answer was not a JSON object"
        try:
            return ReviewAnswer.model_validate(raw), ""
        except ValidationError as exc:
            return None, f"schema error: {exc.error_count()} invalid field(s)"

    async def review(self, reply: str, platform: str, mention: Mention, claims: list[Claim],
                     guidance=None) -> ReviewResult:
        model = _model_name(self.brain, AGENT, TASK)
        prompt = build_prompt(reply, platform, mention, claims, guidance)
        for attempt in range(1, self.max_attempts + 1):
            answer, problem = await self._ask(prompt)
            if answer is not None:
                return ReviewResult(
                    verdict=ReviewVerdict(answer.verdict),
                    reasons=[
                        {"rule_id": r.rule_id.strip()[:20],
                         "explanation": r.explanation.strip()[:MAX_EXPLANATION_CHARS]}
                        for r in answer.reasons[:MAX_REASONS]
                    ],
                    model=model,
                )
            logger.warning(f"review attempt {attempt} for mention {mention.id} invalid: {problem}")
            prompt = (
                f"{prompt}\n\nYour previous answer was invalid ({problem}). "
                "Return one JSON object with exactly the fields listed above."
            )
        return _failed(model)
