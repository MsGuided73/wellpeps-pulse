"""Shared setup for Pulse (Phase 8) tests: synthetic triaged mentions and a
scripted fake brain. Not a conftest: tests import what they need."""

import json
from datetime import datetime

from harvey.models import Mention, MentionStatus, Platform, Triage
from harvey.state import StateManager

# Frozen 7-day window used across the trend tests.
WINDOW_START = datetime(2026, 9, 20)
WINDOW_END = datetime(2026, 9, 27)


async def add_triaged(state: StateManager, key: str, *, text: str, posted_at: datetime,
                      title: str = "", author: str = "", url: str | None = None,
                      status: MentionStatus = MentionStatus.TRIAGED, relevant: bool = True,
                      **triage) -> int:
    """Insert one mention with posted_at and a triage row, moved to ``status``."""
    mention_id, _ = await state.upsert_mention(Mention(
        platform=Platform.REDDIT, external_id=key, title=title, author_handle=author,
        url=url or f"https://www.reddit.com/r/test/comments/{key}/", text=text,
        posted_at=posted_at, collected_at=posted_at,
    ))
    await state.save_triage(Triage(mention_id=mention_id, relevant=relevant, **triage))
    if status is not MentionStatus.NEW:
        if status is MentionStatus.DROPPED:
            await state.set_mention_status(mention_id, MentionStatus.DROPPED)
        else:
            await state.set_mention_status(mention_id, MentionStatus.TRIAGED)
            if status is MentionStatus.ESCALATED:
                await state.set_mention_status(mention_id, MentionStatus.ESCALATED)
    return mention_id


async def fresh_state(tmp_path) -> StateManager:
    state = StateManager(str(tmp_path / "pulse.db"))
    await state.init_db()
    return state


class FakeBrain:
    """Scripted stand-in for harvey.brain.Brain (think_json only).

    ``answers`` is a list consumed in order; each item is returned as-is
    (dict, None, or a string that is not JSON). Callable items get the prompt.
    """

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.calls: list[tuple[str, str]] = []

    def model_for(self, agent, task):
        return "fake-sonnet"

    async def think_json(self, prompt, session_id=None, agent="", task=""):
        self.prompts.append(prompt)
        self.calls.append((agent, task))
        answer = self.answers.pop(0) if self.answers else None
        if callable(answer):
            answer = answer(prompt)
        if isinstance(answer, str):
            return None  # the real Brain returns None when JSON can't be parsed
        return json.loads(json.dumps(answer)) if answer is not None else None


def good_answer(**overrides) -> dict:
    """A valid brief answer that cites no numbers at all."""
    answer = {
        "headline": "Shipping frustration is rising across the category",
        "summary_md": "**Shipping** complaints lead this period.\n\n- Watch pricing talk\n- Keep FAQ current",
        "action_cards": [
            {"title": "Refresh the shipping FAQ", "why": "Shipping terms lead the emerging list.",
             "action": "Update the FAQ with current delivery times.", "owner_hint": "support",
             "urgency": "this_week", "evidence_terms": ["shipping delay"]},
            {"title": "Review price-change messaging", "why": "Price talk is climbing.",
             "action": "Check that pricing pages are clear before checkout.", "owner_hint": "marketing",
             "urgency": "this_month", "evidence_terms": ["price hike"]},
            {"title": "Brief compliance on shortage questions", "why": "Shortage chatter is new.",
             "action": "Route any public copy about supply to compliance first.", "owner_hint": "compliance",
             "urgency": "watch", "evidence_terms": ["shortage"]},
        ],
        "watchlist": ["oral wegovy"],
    }
    answer.update(overrides)
    return answer
