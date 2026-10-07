"""Acknowledger: one short, post-specific acknowledgement for an approved reply.

For ``boundary_only`` situations (complaint, billing, adverse event, privacy,
individual medical questions ...) Pulse posts the Approved Messaging & Response
Guide's response verbatim. On its own that reads like a canned template ("Support
just sends the same canned email"), so Pulse may put ONE short clause between
the disclosure and the verbatim response that restates the poster's stated issue
neutrally ("Thanks for flagging the shipping delay you're describing."). The
guide allows adapting ordinary conversational wording while preserving meaning,
disclosures, claims and privacy boundaries ([AMG] §1); the approved response
itself is never changed.

A model (agent ``drafter``, task ``acknowledge``; haiku in harvey.yaml) writes the
clause; it is accepted only if ``acknowledgement_problems`` finds nothing:

- one sentence, at most ``MAX_WORDS`` words, no question, link, handle or hashtag;
- never confirms the person is a customer or patient (no "your ..." at all, no
  "customer" / "patient" / "member") and never speaks for the company ("we",
  "our", "WellPeps");
- no medical content (doses, symptoms, medications, treatment, providers ...);
- no promise or new fact ("we'll fix", "refund", "right away" ...);
- never endorses or judges the post ("great tip", "affordable alternative");
- no number that is not in the post.

``harvey.drafting`` then checks the composed reply with the compliance filter:
any hit the verbatim reply alone does not have rejects the clause too. On any
failure the reply is the verbatim template, exactly as before. Emergencies never
get a clause (the emergency line stands alone), and neither does misinformation.
"""

import logging
import re

from pydantic import BaseModel, ConfigDict, ValidationError

from harvey.agents import prompting
from harvey.compliance import names_medication
from harvey.models import Mention

logger = logging.getLogger("harvey.agents.acknowledger")

PROMPT_PATH = prompting.PROMPTS_DIR / "acknowledge.md"
AGENT = "drafter"
TASK = "acknowledge"
MAX_WORDS = 20
MAX_CHARS = 160
# The emergency line stands alone: nothing may delay "seek emergency care".
SKIP_SITUATIONS = frozenset({"emergency"})
# Never acknowledge misinformation: any friendly sentence about it reads as an
# endorsement ("Thank you for sharing this affordable alternative.").
SKIP_CATEGORIES = frozenset({"misinformation"})

# Any "your ..." could confirm a relationship ("your order", "your refund"):
# the clause names the issue, never the person's things.
_PATIENT_RX = re.compile(
    r"\byours?\b|\b(?:you.re|you\s+are)\s+(?:a|an|one\s+of)\b|\b(?:patients?|customers?|members?|clients?)\b",
    re.IGNORECASE,
)
_COMPANY_RX = re.compile(r"\b(?:we|we're|we've|we'll|we'd|us|our|ours|wellpeps|well\s+peps|team|company)\b",
                         re.IGNORECASE)
_MEDICAL_RX = re.compile(
    r"\b(?:doses?|dosing|dosage|symptoms?|side[\s-]?effects?|reactions?|diagnos\w*|prescri\w*|medications?|"
    r"medicines?|meds|drugs?|treatments?|therap\w*|vomit\w*|nause\w*|pain|sick|ill(?:ness)?|hospital\w*|"
    r"emergenc\w*|ER|clinic\w*|doctors?|providers?|nurses?|injections?|shots?|pills?|labs?|tests?|"
    r"health|healthy|medical|safe(?:ty)?|cause[ds]?|condition|allerg\w*|weight|glp-?1)\b",
    re.IGNORECASE,
)
_PROMISE_RX = re.compile(
    r"\b(?:will|won't|i'll|we'll|going\s+to|gonna|promise\w*|guarantee\w*|refund\w*|fix\w*|resolv\w*|"
    r"make\s+(?:it|this|things)\s+right|look\s+into|looking\s+into|escalat\w*|priorit\w*|asap|right\s+away|"
    r"immediately|today|tomorrow|soon|shortly|compensat\w*|credit\w*|replace\w*|reship\w*|ship(?:ped|ping)?\s+out)\b",
    re.IGNORECASE,
)
# Judging or endorsing what the post says or recommends (the clause names the
# issue; it never agrees, praises or rates).
_ENDORSE_RX = re.compile(
    r"\b(?:alternatives?|tips?|advice|recommend\w*|suggest\w*|options?|great|good|helpful|useful|smart|"
    r"valid|agree\w*|right|true|correct|accurate|affordable|cheap\w*|works?|better|best|interesting|"
    r"insight\w*|perspective|point)\b",
    re.IGNORECASE,
)
_LINK_RX = re.compile(r"https?://|www\.|\.(?:com|org|net|io)\b|[@#]\w", re.IGNORECASE)
_NUMBER_WORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
                 "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
                 "twenty", "thirty", "forty", "fifty", "hundred", "thousand", "dozen", "half", "once", "twice",
                 "first", "second", "third", "fourth", "fifth", "single", "double", "triple", "several", "multiple")
_NUMBER_RX = re.compile(r"\d+(?:[.,]\d+)?|\b(?:" + "|".join(_NUMBER_WORDS) + r")\b", re.IGNORECASE)
_SENTENCE_BREAK = re.compile(r"[.!?;:]\s+\S")


class AckAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    acknowledgement: str = ""


def _numbers(text: str) -> set[str]:
    return {m.group(0).lower().replace(",", "") for m in _NUMBER_RX.finditer(text or "")}


def acknowledgement_problems(clause: str, post: str) -> list[str]:
    """Why ``clause`` may not go into an approved reply ([] = acceptable)."""
    text = (clause or "").strip()
    if not text:
        return ["empty"]
    problems = []
    if "\n" in text or _SENTENCE_BREAK.search(text):
        problems.append("more than one sentence")
    if len(text.split()) > MAX_WORDS or len(text) > MAX_CHARS:
        problems.append(f"longer than {MAX_WORDS} words")
    if "?" in text:
        problems.append("asks a question")
    if "[" in text or "]" in text:
        problems.append("bracketed placeholder")
    if _LINK_RX.search(text):
        problems.append("link, handle or hashtag")
    if _PATIENT_RX.search(text):
        problems.append("could confirm the person is a customer or patient (R31)")
    if _COMPANY_RX.search(text):
        problems.append("speaks for the company")
    if _MEDICAL_RX.search(text) or names_medication(text):
        problems.append("medical content")
    if _PROMISE_RX.search(text):
        problems.append("promise or new fact")
    if _ENDORSE_RX.search(text):
        problems.append("endorses or judges the post")
    extra = _numbers(text) - _numbers(post)
    if extra:
        problems.append(f"number not in the post: {', '.join(sorted(extra))}")
    return problems


def _clean(clause: str) -> str:
    text = " ".join((clause or "").split()).strip().strip('"').strip()
    if text and text[-1] not in ".!":
        text += "."
    return text[:1].upper() + text[1:] if text else text


def build_prompt(mention: Mention, situation_label: str, nonce: str | None = None) -> str:
    return prompting.render(PROMPT_PATH, {
        "situation": situation_label,
        "max_words": str(MAX_WORDS),
        "platform": mention.platform.value,
        "nonce": nonce or prompting.new_nonce(),
        "mention": prompting.mention_block(mention),
    })


class Acknowledger:
    """Asks a brain-like object (``think_json``) for one acknowledgement clause."""

    def __init__(self, brain):
        self.brain = brain

    async def acknowledge(self, mention: Mention, situation_label: str) -> tuple[str, list[str]]:
        """(clause, problems). The clause is "" whenever it can't be used."""
        try:
            raw = await self.brain.think_json(build_prompt(mention, situation_label), agent=AGENT, task=TASK)
        except Exception as exc:
            return "", [f"brain error: {type(exc).__name__}"]
        if not isinstance(raw, dict):
            return "", ["answer was not a JSON object"]
        try:
            answer = AckAnswer.model_validate(raw)
        except ValidationError:
            return "", ["schema error"]
        clause = _clean(answer.acknowledgement)
        problems = acknowledgement_problems(clause, f"{mention.title or ''} {mention.text or ''}")
        if problems:
            logger.info(f"acknowledgement for mention {mention.id} rejected: {problems}")
            return "", problems
        return clause, []
