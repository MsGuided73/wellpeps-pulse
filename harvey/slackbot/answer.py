"""Aggregate result -> Slack answer (one sonnet call, ``agent="slackbot", task="answer"``).

The prompt carries ONLY ``QueryResult.payload()`` (aggregates); the staff
question itself is not sent. The model's text then goes through the guards:
sentences citing numbers not in the payload are dropped, and if nothing is
left (or the call failed) a deterministic templated answer is used instead.
The scrubbed text is capped at 120 words, and a deterministic footer with
the dashboard deep link (omitted when PULSE_DASHBOARD_URL is unset) is
appended; the whole message is at most 1500 characters.
"""

import json
import logging
from dataclasses import dataclass

from harvey.agents import prompting
from harvey.slackbot import guards
from harvey.slackbot.executor import QueryResult, full_link

logger = logging.getLogger("harvey.slackbot.answer")

AGENT = "slackbot"
TASK = "answer"
PROMPT = "slack_answer.md"
TOO_FEW = "too few to show"


@dataclass
class Answer:
    text: str
    fallback: bool          # the templated answer was used
    stripped: int           # sentences dropped by the numeric guard


def build_prompt(payload: dict) -> str:
    return prompting.fill(prompting.load(PROMPT), {
        "nonce": prompting.new_nonce(),
        "max_words": str(guards.MAX_WORDS),
        "data": json.dumps(payload, indent=1, ensure_ascii=False),
    })


async def write(brain, payload: dict) -> str:
    """The model's raw answer text, or "" on any failure."""
    try:
        return (await brain.think(build_prompt(payload), agent=AGENT, task=TASK)) or ""
    except Exception as exc:
        logger.warning(f"answer call failed: {type(exc).__name__}")
        return ""


# ── Deterministic answers (fallback) ──


def _n(value) -> str:
    return TOO_FEW if value is None else f"{value:,}" if isinstance(value, int) else str(value)


def _signed(value) -> str:
    return "n/a" if value is None else f"{value:+g}"


def _pct_plain(value) -> str:
    return "n/a" if value is None else f"{value:g}%"


def _pct(value) -> str:
    return "n/a" if value is None else f"{value:+g}%"


def _window(result: QueryResult) -> str:
    return f"the last {result.days} day{'s' if result.days != 1 else ''}"


def templated(result: QueryResult) -> str:
    """A plain answer built from the payload alone (always passes the numeric guard)."""
    d = result.data
    w = _window(result)
    if result.intent == "volume":
        lines = [f"*Relevant mentions in {w}:* {_n(d.get('mentions'))} "
                 f"(previous period: {_n(d.get('previous_period_mentions'))}, change {_pct(d.get('change_pct'))})."]
        lines += [f"- {c['category'].replace('_', ' ')}: {c['mentions']}" for c in d.get("by_category", [])[:6]]
    elif result.intent == "share_of_voice":
        lines = [f"*Share of voice, {w}:*"]
        lines += [f"- {b['brand']}: {b['share_pct']}% ({_signed(b['change_pts'])} pts)" for b in d.get("brands", [])[:6]]
    elif result.intent == "sentiment":
        lines = [f"*Average sentiment, {w}* (-1 to +1):"]
        lines += [f"- {b['brand']}: " + (TOO_FEW + " (needs 3 scored mentions)" if b["mean"] is None
                                          else f"{b['mean']:+g} (change {_signed(b['change'])})")
                  for b in d.get("brands", [])[:6]]
    elif result.intent == "emerging_terms":
        terms = d.get("terms", [])
        lines = [f"*Emerging terms, {w}:*" if terms else f"No term reached the minimum count in {w}."]
        lines += [f"- {t['term']}: {t['mentions']} mentions" + (" (new)" if t["new"] else f", {t['velocity']}x baseline")
                  for t in terms[:6]]
    elif result.intent == "complaints" and "term" in d:
        lines = [f"Complaint mentions containing '{d['term']}' in {w}: {_n(d.get('mentions'))} "
                 f"(previous period: {_n(d.get('previous_period_mentions'))})."]
    elif result.intent == "complaints":
        cells = d.get("top_clusters", [])
        lines = [f"*Complaints in {w}:* {_n(d.get('complaint_mentions'))}."]
        lines += [f"- {c['brand']} / {c['theme']}: {c['mentions']}" for c in cells[:6]]
    elif result.intent == "drug_momentum":
        drugs = d.get("drugs", [])
        lines = [f"*Drug mentions, {w}:*" if drugs else f"No drug had enough mentions to show in {w}."]
        lines += [f"- {x['drug']}: {x['mentions']} (change {_pct(x['change_pct'])})" for x in drugs[:6]]
    elif result.intent == "escalations_sla":
        lines = [f"*Escalations in {w}:* {d.get('escalations', 0)}; acknowledged {d.get('acknowledged', 0)}; "
                 f"median minutes to acknowledge {_n(d.get('median_minutes_to_acknowledge'))}; "
                 f"breached {_pct_plain(d.get('breached_pct'))}. Open now: {d.get('open_now', 0)} "
                 f"({d.get('open_breached_now', 0)} past SLA)."]
    elif result.intent == "latest_brief":
        brief = d.get("brief")
        if not brief:
            lines = ["No Pulse brief has been written yet."]
        else:
            lines = [f"*Latest {brief['period']} brief* (window from {brief['window_start']}): {brief['headline']}"]
            lines += [f"- {a}" for a in brief.get("top_actions", []) if a]
    elif result.intent == "search_count":
        lines = [f"Mentions containing '{d.get('term', '')}' in {w}: {_n(d.get('mentions'))} "
                 f"(previous period: {_n(d.get('previous_period_mentions'))})."]
    else:
        lines = ["No data for that question."]
    if result.ignored:
        lines.append("Not applied: " + ", ".join(result.ignored) + ".")
    return "\n".join(lines)


def footer(result: QueryResult, dashboard_url: str) -> str:
    link = full_link(dashboard_url, result.link)
    return f"<{link}|Open in Pulse dashboard>" if link else ""


def finalize(raw: str, result: QueryResult, dashboard_url: str) -> Answer:
    """Guards + footer. Never returns model text that failed verification."""
    payload = result.payload()
    text, stripped = guards.verify_numbers(raw or "", payload)
    if stripped:
        logger.warning(f"slackbot answer: stripped {len(stripped)} sentence(s) with numbers not in the data")
    text = guards.scrub(text, dashboard_url)
    fallback = not text.strip()
    if fallback:
        text = guards.scrub(templated(result), dashboard_url)
    text = guards.cap_words(text)
    tail = footer(result, dashboard_url)
    room = guards.MAX_CHARS - (len(tail) + 1 if tail else 0)
    text = guards.cap_chars(text, room)
    return Answer(f"{text}\n{tail}" if tail else text, fallback, len(stripped))
