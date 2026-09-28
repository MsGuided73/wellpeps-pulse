"""Pulse briefs (Phase 8): a daily / weekly market brief from aggregates.

``build_brief`` computes the window (daily = the previous local day, weekly =
the previous Monday-Sunday, local to ``usage.quiet_hours.timezone``), banks
new language, runs ``harvey.trends.compute_trends``, and asks the ``pulse``
agent (sonnet by default) for a JSON brief. The prompt carries ONLY the
aggregate tables: never mention text, handles, URLs or mention ids.

Safety rails around the model:
- the answer is validated with pydantic; one retry; then a deterministic
  fallback brief ("tables only") is stored instead;
- any action card whose ``why`` cites a number that is not in the aggregate
  payload is stripped and logged (the model must not invent numbers);
- one brief per (period, window_start): a rerun returns the stored brief
  unless ``force=True``.

Slack gets the headline, the top three action-card titles and a dashboard
link only, scrubbed of URLs, handles and language-bank phrases
(``build_brief_message`` is the one place that message is composed).
"""

import inspect
import json
import logging
import re
from dataclasses import asdict
from datetime import datetime, time, timedelta, timezone
from typing import Literal

import pytz
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from harvey import pulse_store, trends
from harvey.agents import prompting
from harvey.config import WEEKDAYS, PulseConfig

logger = logging.getLogger("harvey.briefs")

PROMPT_PATH = prompting.PROMPTS_DIR / "brief.md"
AGENT = "pulse"
TASK = "brief"
PERIODS = ("daily", "weekly")
MAX_ATTEMPTS = 2  # first try + one retry
MAX_CARDS = 7
MAX_SUMMARY_WORDS = 250
PAYLOAD_PHRASES = 20
# Nothing seen in fewer mentions than this reaches the prompt: a term or
# phrase from a single post is an anecdote and could point at its author.
MIN_PROMPT_COUNT = 2
SLACK_CARDS = 3
FALLBACK_HEADLINE = "Automated brief unavailable — tables only"
_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
_SCRUB_MIN_PHRASE = 8


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Answer schema ──


class ActionCard(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=120)
    why: str = Field(min_length=1, max_length=800)
    action: str = Field(min_length=1, max_length=800)
    owner_hint: Literal["marketing", "product", "support", "clinical", "compliance", "leadership"]
    urgency: Literal["this_week", "this_month", "watch"]
    evidence_terms: list[str] = Field(default_factory=list, max_length=12)


class BriefAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    headline: str = Field(min_length=1, max_length=200)
    summary_md: str = Field(min_length=1, max_length=4000)
    action_cards: list[ActionCard] = Field(min_length=1, max_length=12)
    watchlist: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("summary_md")
    @classmethod
    def _cap_words(cls, v: str) -> str:
        words = v.split(" ")
        return v if len(v.split()) <= MAX_SUMMARY_WORDS else " ".join(words[:MAX_SUMMARY_WORDS]) + " …"

    @field_validator("action_cards")
    @classmethod
    def _cap_cards(cls, v: list[ActionCard]) -> list[ActionCard]:
        return v[:MAX_CARDS]

    @field_validator("watchlist")
    @classmethod
    def _clean_watchlist(cls, v: list[str]) -> list[str]:
        return [w.strip()[:80] for w in v if isinstance(w, str) and w.strip()][:15]


# ── Windows ──


def _as_utc_naive(now: datetime) -> datetime:
    return now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now


def window_for(period: str, now: datetime, tz_name: str) -> tuple[datetime, datetime]:
    """[start, end) in naive UTC: the previous local day, or the previous
    local Monday-Sunday week."""
    if period not in PERIODS:
        raise ValueError(f"unknown brief period {period!r}; use {' or '.join(PERIODS)}")
    tz = pytz.timezone(tz_name)
    today = pytz.utc.localize(_as_utc_naive(now)).astimezone(tz).date()
    if period == "daily":
        start_day, end_day = today - timedelta(days=1), today
    else:
        end_day = today - timedelta(days=today.weekday())
        start_day = end_day - timedelta(days=7)

    def utc(day):
        return tz.localize(datetime.combine(day, time())).astimezone(pytz.utc).replace(tzinfo=None)

    return utc(start_day), utc(end_day)


# ── Payload and prompt ──


def build_payload(report: trends.TrendReport, phrases: list[dict], period: str) -> dict:
    """The only data the model sees: aggregate tables, rounded for citing."""
    return {
        "period": period,
        "market": "US",
        "window": {"start": report.window_start.isoformat(), "end": report.window_end.isoformat(),
                   "days": round(report.window_days, 1)},
        "baseline_days": report.baseline_days,
        "mentions_in_window": report.mentions,
        "mentions_in_previous_window": report.previous_mentions,
        "emerging_terms": [
            {"term": t.term, "count": t.count, "baseline_count": t.baseline_count,
             "velocity": round(t.velocity, 1), "new": t.is_new}
            for t in report.terms if t.count >= MIN_PROMPT_COUNT
        ],
        "share_of_voice": [
            {"subject": r["subject"], "count": r["count"], "share_pct": round(r["share"] * 100, 1),
             "prev_count": r["prev_count"], "prev_share_pct": round(r["prev_share"] * 100, 1),
             "delta_pp": round(r["delta"] * 100, 1)}
            for r in report.share_of_voice
        ],
        "sentiment": [
            {"kind": r["kind"], "subject": r["subject"], "n": r["n"],
             "mean": _r2(r["mean"]), "prev_mean": _r2(r["prev_mean"]), "delta": _r2(r["delta"])}
            for r in report.sentiment
        ],
        "category_mix": report.category_mix,
        "drug_mix": report.drug_mix,
        "complaint_themes": report.complaint_themes,
        "language_bank": [
            {"phrase": p["phrase"], "count": p["count"], "category": p.get("category") or ""}
            for p in phrases
            if p["count"] >= MIN_PROMPT_COUNT and not trends.has_identifier(p["phrase"])
        ][:PAYLOAD_PHRASES],
    }


def _r2(value):
    return None if value is None else round(value, 2)


def build_prompt(payload: dict) -> str:
    return prompting.fill(prompting.load("brief.md"), {
        "nonce": prompting.new_nonce(),
        "period": str(payload.get("period", "daily")),
        "data": json.dumps(payload, indent=1, ensure_ascii=False),
    })


# ── Numeric verification ──


def numbers_in(text: str) -> set[float]:
    return {float(n.replace(",", "")) for n in _NUMBER.findall(text or "")}


def verify_cards(cards: list[dict], payload: dict) -> tuple[list[dict], list[dict]]:
    """Split cards into (kept, stripped): a card is stripped when its ``why``
    cites a number that appears nowhere in the payload."""
    allowed = numbers_in(json.dumps(payload))
    kept, stripped = [], []
    for card in cards:
        invented = sorted(numbers_in(card.get("why", "")) - allowed)
        if invented:
            logger.warning(f"stripped action card {card.get('title', '')!r}: numbers not in the data: {invented}")
            stripped.append(card)
        else:
            kept.append(card)
    return kept, stripped


# ── Model call ──


def _model_name(brain) -> str:
    model_for = getattr(brain, "model_for", None)
    try:
        return (model_for(AGENT, TASK) or "") if callable(model_for) else ""
    except Exception:
        return ""


async def _ask(brain, payload: dict) -> BriefAnswer | None:
    prompt = build_prompt(payload)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        problem = ""
        try:
            raw = await brain.think_json(prompt, agent=AGENT, task=TASK)
        except Exception as exc:
            raw, problem = None, f"brain error: {type(exc).__name__}"
        if isinstance(raw, dict):
            try:
                return BriefAnswer.model_validate(raw)
            except ValidationError as exc:
                problem = f"schema error: {exc.error_count()} invalid field(s)"
        problem = problem or "answer was not a JSON object"
        logger.warning(f"brief attempt {attempt} invalid: {problem}")
        prompt = (f"{prompt}\n\nYour previous answer was invalid ({problem}). "
                  "Return one JSON object with exactly the fields listed above.")
    return None


def fallback_fields(report: trends.TrendReport) -> dict:
    """A deterministic brief built from the tables alone."""
    lines = ["Automated summary unavailable; the tables are computed directly from triaged mentions.", "",
             f"- {report.mentions} relevant mention(s) in the window "
             f"({report.previous_mentions} in the previous window)."]
    if report.terms:
        lines.append("- Top emerging terms: " + ", ".join(t.term for t in report.terms[:5]) + ".")
    if report.share_of_voice:
        top = report.share_of_voice[0]
        lines.append(f"- Most discussed: {top['subject']} ({round(top['share'] * 100, 1)}% share of voice).")
    return {"status": "fallback", "headline": FALLBACK_HEADLINE, "summary_md": "\n".join(lines),
            "action_cards": [], "watchlist": [t.term for t in report.terms if t.is_new][:5]}


# ── build_brief ──


async def build_brief(state, brain, period: str, *, config: PulseConfig | None = None,
                      now: datetime | None = None, force: bool = False, notifier=None) -> dict:
    """Build (or return the stored) brief for the window ``period`` ends at ``now``."""
    config = config or PulseConfig()
    now = _as_utc_naive(now or _utcnow())
    start, end = window_for(period, now, config.usage.quiet_hours.timezone)
    existing = await pulse_store.get_brief_by_window(state, period, start)
    if existing is not None and not force:
        return {**existing, "created": False}

    await trends.bank_language(state)
    settings = config.pulse
    report = await trends.compute_trends(state, start, end, baseline_days=settings.baseline_days,
                                         min_count=settings.min_count, top_n=settings.top_terms)
    phrases = await pulse_store.top_phrases(state, since=start, limit=PAYLOAD_PHRASES * 2)
    payload = build_payload(report, phrases, period)
    data = {**report.to_dict(), "language_bank": payload["language_bank"], "verification": {"stripped": []}}

    answer = await _ask(brain, payload) if brain is not None else None
    if answer is None:
        fields = fallback_fields(report)
    else:
        kept, stripped = verify_cards([c.model_dump() for c in answer.action_cards], payload)
        data["verification"]["stripped"] = [c["title"] for c in stripped]
        fields = {"status": "ok", "headline": answer.headline, "summary_md": answer.summary_md,
                  "action_cards": kept, "watchlist": answer.watchlist}

    brief_id = await pulse_store.save_brief(state, {
        "period": period, "window_start": start, "window_end": end, "data": data,
        "model": _model_name(brain) if answer is not None else "", "created_at": now, **fields,
    }, [asdict(t) for t in report.terms])
    logger.info(f"{period} brief #{brief_id} stored ({fields['status']}, {len(fields['action_cards'])} card(s))")
    if notifier is not None:
        await post_brief(state, notifier, await pulse_store.get_brief(state, brief_id), config, now=now)
    return {**await pulse_store.get_brief(state, brief_id), "created": True}


# ── Slack ──


def _scrub(text: str, phrases: list[str]) -> str:
    """No links, handles, e-mails or banked phrases leave Pulse via Slack."""
    text = trends.strip_identifiers(text)
    for phrase in sorted(phrases, key=len, reverse=True):
        words = phrase.split()
        if len(phrase) >= _SCRUB_MIN_PHRASE and words:
            pattern = r"\s+".join(re.escape(w) for w in words)
            text = re.sub(pattern, "[phrase]", text, flags=re.I)
    return " ".join(text.split())[:200]


def _slack_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_brief_message(brief: dict, config) -> tuple[str, list[dict]]:
    """The one place a brief's Slack message is composed: headline, top
    action-card titles, dashboard link. Never phrases or mention text."""
    phrases = [p.get("phrase", "") for p in (brief.get("data") or {}).get("language_bank", [])]
    title = f"Pulse {brief['period']} brief — window from {str(brief['window_start'])[:10]}"
    headline = _scrub(brief.get("headline", ""), phrases)
    actions = [_scrub(c.get("title", ""), phrases) for c in brief.get("action_cards", [])[:SLACK_CARDS]]
    body = [title, f"Headline: {headline}"]
    if actions:
        body.append("Top actions:")
        body += [f"{i}. {a}" for i, a in enumerate(actions, start=1)]
    dashboard = config.notify.dashboard_url.strip().rstrip("/")
    link = f"{dashboard}/#pulse-brief-{brief['id']}" if dashboard else ""
    if link:
        body.append(f"Dashboard: {link}")
    text = "\n".join(body)
    mrkdwn = [f"*{_slack_escape(title)}*"] + [_slack_escape(line) for line in body[1:] if not line.startswith("Dashboard:")]
    if link:
        mrkdwn.append(f"<{_slack_escape(link)}|Open in Pulse>")
    return text, [{"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(mrkdwn)}}]


async def post_brief(state, notifier, brief: dict, config, now: datetime | None = None) -> bool:
    text, blocks = build_brief_message(brief, config)
    try:
        sent = bool(await notifier.send(text, blocks=blocks))
    except Exception as exc:  # the notifier shouldn't raise; be sure anyway
        logger.error(f"brief #{brief['id']} Slack post failed: {type(exc).__name__}")
        sent = False
    if sent:
        await pulse_store.mark_brief_slack_sent(state, brief["id"], now or _utcnow())
    return sent


# ── Scheduling ──


async def due_periods(state, config: PulseConfig, now: datetime | None = None) -> list[str]:
    """Briefs the heartbeat should build now: the daily one after
    ``pulse.daily_brief_hour`` local, the weekly one from ``pulse.weekly_day``
    on and only once the daily is done. A window with no relevant mentions
    gets no brief."""
    now = _as_utc_naive(now or _utcnow())
    tz_name = config.usage.quiet_hours.timezone
    local = pytz.utc.localize(now).astimezone(pytz.timezone(tz_name))
    if local.hour < config.pulse.daily_brief_hour:
        return []

    async def needed(period: str) -> bool:
        start, end = window_for(period, now, tz_name)
        if await pulse_store.get_brief_by_window(state, period, start) is not None:
            return False
        return await pulse_store.count_relevant(state, start, end) > 0

    if await needed("daily"):
        return ["daily"]
    if local.weekday() >= WEEKDAYS.index(config.pulse.weekly_day) and await needed("weekly"):
        return ["weekly"]
    return []


async def run_due_briefs(state, brain, config: PulseConfig, notifier=None, budget_ok=None,
                         now: datetime | None = None) -> list[dict]:
    """Build every due brief, checking the Claude budget before each."""
    built = []
    for period in await due_periods(state, config, now=now):
        if budget_ok is not None:
            ok = budget_ok()
            if inspect.isawaitable(ok):
                ok = await ok
            if not ok:
                logger.info(f"{period} brief deferred: over the Claude budget")
                break
        built.append(await build_brief(state, brain, period, config=config, now=now, notifier=notifier))
    return built
