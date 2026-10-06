"""Shared setup for the #pulse-query bot tests: a scripted brain, a fake
Slack client, and a seeded week of triaged mentions. No network."""

import json
from datetime import datetime, timedelta

from tests.pulse_helpers import add_triaged

NOW = datetime(2026, 9, 28, 16, 0)  # naive UTC, a Monday noon in New York
CHANNEL = "C0QUERYCHN"
OTHER_CHANNEL = "C0ELSEWHERE"
BOT_TOKEN = "xoxb-1111111111-2222222222-SECRETBOTTOKENabc"
APP_TOKEN = "xapp-1-A0TEST-3333333333-SECRETAPPTOKENxyz"


class ScriptedBrain:
    """think_json -> plan answers; think -> answer texts (consumed in order)."""

    def __init__(self, plans=(), answers=()):
        self.plans = list(plans)
        self.answers = list(answers)
        self.prompts: list[tuple[str, str, str]] = []  # (agent, task, prompt)

    def model_for(self, agent, task):
        return {"plan": "haiku", "answer": "sonnet"}.get(task, "")

    async def think_json(self, prompt, session_id=None, agent="", task=""):
        self.prompts.append((agent, task, prompt))
        item = self.plans.pop(0) if self.plans else None
        if isinstance(item, Exception):
            raise item
        return json.loads(json.dumps(item)) if isinstance(item, (dict, list)) else None

    async def think(self, prompt, session_id=None, agent="", task="", **kwargs):
        self.prompts.append((agent, task, prompt))
        item = self.answers.pop(0) if self.answers else ""
        if isinstance(item, Exception):
            raise item
        return item


class FakeSlackClient:
    """Records the AsyncWebClient calls the bot makes."""

    def __init__(self, fail_reactions: bool = False):
        self.posts: list[dict] = []
        self.reactions: list[tuple[str, dict]] = []
        self.fail_reactions = fail_reactions

    async def chat_postMessage(self, **kwargs):
        self.posts.append(kwargs)
        return {"ok": True}

    async def reactions_add(self, **kwargs):
        self.reactions.append(("add", kwargs))
        if self.fail_reactions:
            raise RuntimeError("already_reacted")

    async def reactions_remove(self, **kwargs):
        self.reactions.append(("remove", kwargs))


def mention_event(text="<@UBOT> what's trending this week?", *, channel=CHANNEL, user="U0STAFF1",
                  ts="1727539200.000100", **extra) -> dict:
    return {"type": "app_mention", "text": text, "channel": channel, "user": user, "ts": ts, **extra}


async def seed_week(state, now: datetime = NOW) -> None:
    """A small, deterministic week of relevant mentions (plus a previous week)."""
    def at(hours):
        return now - timedelta(hours=hours)

    rows = [
        # WellPeps: three complaints about shipping, two praise
        *[dict(key=f"wp-c{i}", text="my shipping delay again, waiting on my order", subject_type="wellpeps",
               category="complaint", drug="tirzepatide", sentiment_score=-0.6,
               phrases=["shipping delay again"], posted_at=at(5 + i)) for i in range(3)],
        *[dict(key=f"wp-p{i}", text="love the support team, fast refill", subject_type="wellpeps",
               category="praise", drug="semaglutide", sentiment_score=0.8, posted_at=at(20 + i))
          for i in range(2)],
        # Hims & Hers: four complaints about price
        *[dict(key=f"hh-c{i}", text="price hike on compounded semaglutide, contact me at a@b.example",
               subject_type="competitor", competitor="Hims & Hers", category="complaint", drug="semaglutide",
               sentiment_score=-0.4, phrases=["price hike"], posted_at=at(30 + i)) for i in range(4)],
        # Ro: one question (below the privacy floor)
        dict(key="ro-q0", text="does ro ship to texas", subject_type="competitor", competitor="Ro",
             category="question", posted_at=at(40)),
        # previous week
        *[dict(key=f"prev-{i}", text="shipping fine this time", subject_type="wellpeps", category="praise",
               sentiment_score=0.5, posted_at=at(24 * 8 + i)) for i in range(2)],
    ]
    for row in rows:
        key = row.pop("key")
        await add_triaged(state, key, **row)
