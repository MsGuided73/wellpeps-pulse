"""Question -> QuerySpec (one haiku call, ``agent="slackbot", task="plan"``).

The question is staff text but still untrusted: it goes between nonce-tagged
``BEGIN_UNTRUSTED_QUESTION`` / ``END_UNTRUSTED_QUESTION`` markers, and the
model may only answer with a JSON object that validates as ``QuerySpec``.
Anything else (no JSON, unknown intent, out-of-range days, unknown platform
or category, a search term carrying a link/handle/e-mail) returns ``None``,
which the bot turns into the help reply. The spec only *selects* one of the
fixed, deterministic queries in ``executor``; it never carries SQL.

Two cheap deterministic shortcuts skip the model: an empty / "help" question
is help, and a request to *do* something (approve, ack, post, ...) is an
action request that the bot refuses (actions happen in the dashboard).
"""

import json
import logging
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from harvey import trends
from harvey.agents import prompting
from harvey.models import Category, Platform

logger = logging.getLogger("harvey.slackbot.planner")

AGENT = "slackbot"
TASK = "plan"
PROMPT = "slack_plan.md"
INTENTS = ("volume", "share_of_voice", "sentiment", "emerging_terms", "complaints", "drug_momentum",
           "escalations_sla", "latest_brief", "search_count", "help")
DEFAULT_DAYS = 7
MAX_DAYS = 90
MAX_QUESTION_CHARS = 500
MAX_TERM_CHARS = 60
MAX_FILTERS = 8
MAX_NAME_CHARS = 80
PLATFORMS = tuple(p.value for p in Platform)
CATEGORIES = tuple(c.value for c in Category)
_PLATFORM_ALIASES = {"twitter": "x", "x.com": "x", "ig": "instagram", "fb": "facebook",
                     "google": "google_reviews", "google reviews": "google_reviews",
                     "yt": "youtube", "tik tok": "tiktok"}
_CATEGORY_ALIASES = {"complaints": "complaint", "questions": "question", "purchase intent": "purchase_intent",
                     "legal": "legal_regulatory", "adverse event": "adverse_event",
                     "adverse events": "adverse_event", "billing": "billing_fraud"}

# Requests to change something. Read-only bot: these get the "use the
# dashboard" reply without a model call.
_ACTION = re.compile(
    r"^\W*(?:(?:please|pls|can you|could you|would you|go ahead and)\s+)*"
    r"(?:approve|un-?approve|ack|acknowledge|publish|escalate|de-?escalate|reject|delete|remove|assign|"
    r"resolve|dismiss|snooze|reply to|respond to|"
    r"(?:post|send|edit|draft)\s+(?:the\s+|a\s+|this\s+|that\s+|my\s+)?(?:reply|response|draft|message|it)|"
    r"mark\s+(?:it|this|that|#?\d+|\S+\s+as))\b",
    re.I,
)
_HELP = re.compile(r"^\W*(?:help|\?+|what can you (?:do|answer)|how do i use (?:this|you))\W*$", re.I)


class QuerySpec(BaseModel):
    """The only thing the plan model may return. Extra keys are ignored."""

    model_config = ConfigDict(extra="ignore")

    intent: Literal[INTENTS]  # type: ignore[valid-type]
    days: int = Field(default=DEFAULT_DAYS, ge=1, le=MAX_DAYS)
    competitors: list[str] = Field(default_factory=list, max_length=MAX_FILTERS)
    drugs: list[str] = Field(default_factory=list, max_length=MAX_FILTERS)
    platforms: list[str] = Field(default_factory=list, max_length=MAX_FILTERS)
    categories: list[str] = Field(default_factory=list, max_length=MAX_FILTERS)
    term: str = Field(default="", max_length=MAX_TERM_CHARS)

    @field_validator("days", mode="before")
    @classmethod
    def _days(cls, v):
        return DEFAULT_DAYS if v in (None, "") else v

    @field_validator("competitors", "drugs", mode="before")
    @classmethod
    def _names(cls, v):
        return _clean_list(v, MAX_NAME_CHARS)

    @field_validator("platforms", mode="before")
    @classmethod
    def _platforms(cls, v):
        out = []
        for item in _clean_list(v, MAX_NAME_CHARS):
            key = item.lower()
            key = _PLATFORM_ALIASES.get(key, key)
            if key not in PLATFORMS:
                raise ValueError(f"unknown platform {item[:30]!r}")
            if key not in out:
                out.append(key)
        return out

    @field_validator("categories", mode="before")
    @classmethod
    def _categories(cls, v):
        out = []
        for item in _clean_list(v, MAX_NAME_CHARS):
            key = item.lower()
            key = _CATEGORY_ALIASES.get(key, key.replace(" ", "_"))
            if key not in CATEGORIES:
                raise ValueError(f"unknown category {item[:30]!r}")
            if key not in out:
                out.append(key)
        return out

    @field_validator("term", mode="before")
    @classmethod
    def _term(cls, v):
        if v is None:
            return ""
        if not isinstance(v, str):
            raise ValueError("term must be a string")
        term = " ".join(v.split()).strip("\"'“”‘’")
        if term and trends.has_identifier(term):
            raise ValueError("term must not contain a link, handle or e-mail")
        return term

    @model_validator(mode="after")
    def _term_needed(self):
        if self.intent == "search_count" and not self.term:
            raise ValueError("search_count needs a term")
        return self


def _clean_list(value, max_chars: int) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError("expected a list of strings")
    out = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("expected a list of strings")
        item = " ".join(item.split())
        if len(item) > max_chars:
            raise ValueError("name too long")
        if item and item not in out:
            out.append(item)
    return out


HELP_SPEC = QuerySpec(intent="help")


def clean_question(text: str) -> str:
    """The question without Slack user/channel mentions, whitespace-collapsed, capped."""
    text = re.sub(r"<[@#!][^>]*>", " ", text or "")
    return " ".join(text.split())[:MAX_QUESTION_CHARS]


def is_action_request(question: str) -> bool:
    return bool(_ACTION.match(question or ""))


def is_help_request(question: str) -> bool:
    return not (question or "").strip() or bool(_HELP.match(question))


# Competitor/drug names in the prompt come from triage output, which came
# from public posts: only plain short names go in, outside the question block.
_SAFE_NAME = re.compile(r"^[\w][\w &.'+/-]{0,39}$")
MAX_PROMPT_NAMES = 40


def safe_names(names) -> list[str]:
    return [n for n in (str(x).strip() for x in names or ()) if _SAFE_NAME.match(n)][:MAX_PROMPT_NAMES]


def build_prompt(question: str, *, competitors=(), drugs=(), today: datetime | None = None) -> str:
    competitors, drugs = safe_names(competitors), safe_names(drugs)
    return prompting.fill(prompting.load(PROMPT), {
        "nonce": prompting.new_nonce(),
        "question": clean_question(question),
        "intents": ", ".join(INTENTS),
        "competitors": ", ".join(competitors) or "(none known yet)",
        "drugs": ", ".join(drugs) or "(none known yet)",
        "platforms": ", ".join(PLATFORMS),
        "categories": ", ".join(CATEGORIES),
        "today": (today or datetime.now()).date().isoformat(),
        "max_days": str(MAX_DAYS),
        "default_days": str(DEFAULT_DAYS),
        "max_term": str(MAX_TERM_CHARS),
    })


def parse_spec(raw) -> QuerySpec | None:
    """A validated spec, or None for anything that isn't one."""
    if not isinstance(raw, dict):
        return None
    try:
        return QuerySpec.model_validate(raw)
    except ValidationError as exc:
        logger.info(f"plan rejected: {exc.error_count()} invalid field(s)")
        return None


async def plan(brain, question: str, *, competitors=(), drugs=(), today: datetime | None = None
               ) -> QuerySpec | None:
    """Help for an empty/help question; else one plan call, validated."""
    if is_help_request(clean_question(question)):
        return HELP_SPEC
    prompt = build_prompt(question, competitors=competitors, drugs=drugs, today=today)
    try:
        raw = await brain.think_json(prompt, agent=AGENT, task=TASK)
    except Exception as exc:
        logger.warning(f"plan call failed: {type(exc).__name__}")
        return None
    return parse_spec(raw)


def spec_summary(spec: QuerySpec) -> dict:
    """The spec as audit/payload JSON (no free text beyond the short term)."""
    return json.loads(spec.model_dump_json())
