"""QuerySpec -> aggregate result, deterministically (no Claude, no free SQL).

Every intent maps to existing aggregate code: ``harvey.analytics.chart`` for
the Analytics charts, ``pulse_store`` for briefs, and ``analytics.fetch_mentions``
(relevant, triaged mentions only) for term counts. The result handed to the
answer model is aggregates only: counts, shares, means, terms seen in at
least two mentions, and brief headlines. Never mention text, handles, URLs
or mention ids.

Privacy floor (the Phase 8 rule): a mention-derived count of 1 is reported
as ``null`` ("fewer than 2"); zero is fine to say. Sentiment means need 3
scored mentions (``analytics.MIN_SENTIMENT_N``). Filters the model asked for
are checked against the known competitors / drugs (case-insensitive); unknown
ones are dropped and listed in ``ignored_filters``. Analytics takes one drug
and one category, so only the first of each is applied.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from harvey import analytics, briefs, pulse_store, trends
from harvey.slackbot.planner import QuerySpec

logger = logging.getLogger("harvey.slackbot.executor")

MIN_COUNT = analytics.MIN_COUNT
TOP_TERMS = 10
TOP_CELLS = 8
TOP_ACTIONS = 3
ANALYTICS_INTENTS = ("volume", "share_of_voice", "sentiment", "emerging_terms", "complaints", "drug_momentum")
_CHART = {"volume": "volume", "share_of_voice": "share", "sentiment": "sentiment",
          "emerging_terms": "emerging", "complaints": "complaints", "drug_momentum": "drugs",
          "escalations_sla": "escalations"}


@dataclass
class QueryResult:
    intent: str
    days: int
    filters: dict
    data: dict
    link: str = ""                     # dashboard hash fragment, e.g. "#analytics?days=7"
    ignored: list[str] = field(default_factory=list)

    def payload(self) -> dict:
        """The only data the answer model sees."""
        return {"intent": self.intent, "days": self.days, "filters": self.filters,
                "ignored_filters": self.ignored, "data": self.data,
                "privacy_note": "null counts mean too few mentions to show"}


def floor(n):
    """A mention-derived count, or None below the privacy floor (0 stays 0)."""
    if n is None:
        return None
    return n if n == 0 or n >= MIN_COUNT else None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _resolve_filters(state, spec: QuerySpec, now: datetime) -> tuple[dict, list[str]]:
    """Canonical competitor/drug names; unknown ones dropped (and reported)."""
    options = await analytics.options(state, now)
    known_comp = {c.lower(): c for c in options["competitors"]}
    known_drug = {d.lower(): d for d in options["drugs"]}
    ignored = []
    competitors = []
    for name in spec.competitors:
        if name.lower() in ("wellpeps", "well peps"):
            continue
        canon = known_comp.get(name.lower())
        if canon and canon not in competitors:
            competitors.append(canon)
        elif not canon:
            ignored.append(f"competitor {name}")
    drugs = []
    for name in spec.drugs:
        canon = known_drug.get(name.lower())
        if canon and canon not in drugs:
            drugs.append(canon)
        elif not canon:
            ignored.append(f"drug {name}")
    if len(drugs) > 1:
        ignored += [f"drug {d} (one drug at a time)" for d in drugs[1:]]
    if len(spec.categories) > 1:
        ignored += [f"category {c} (one category at a time)" for c in spec.categories[1:]]
    filters = {"competitors": competitors[:analytics.MAX_SELECTED], "drug": drugs[0] if drugs else None,
               "platforms": list(spec.platforms), "category": spec.categories[0] if spec.categories else None}
    if spec.term:
        filters["term"] = spec.term
    return filters, ignored


def _params(spec: QuerySpec, filters: dict, config, now: datetime) -> analytics.Params:
    return analytics.parse_params(days=spec.days, platforms=filters["platforms"],
                                  competitors=filters["competitors"], drug=filters["drug"],
                                  category=filters["category"], tz=config.usage.quiet_hours.timezone, now=now)


# ── Per-intent shaping (aggregates only) ──


def _volume(c: dict) -> dict:
    shown = floor(c["total"]) is not None and floor(c["prev_total"]) is not None
    return {"mentions": floor(c["total"]), "previous_period_mentions": floor(c["prev_total"]),
            "change_pct": c["change_pct"] if shown else None,  # a % change could reveal a hidden 1
            "by_category": [{"category": s["key"], "mentions": s["total"]}
                            for s in c["series"] if floor(s["total"])]}


def _pct(share):
    return None if share is None else round(share * 100, 1)


def _share(c: dict) -> dict:
    # Previous shares only when the previous period itself clears the floor.
    prev_ok = floor(c["prev_total"]) not in (None, 0)
    return {"brand_mentions": floor(c["total"]),
            "brands": [{"brand": s["key"], "mentions": s["total"], "share_pct": _pct(s["share"]),
                        "previous_share_pct": _pct(s["prev_share"]) if prev_ok else None,
                        "change_pts": s["delta_pts"] if prev_ok else None}
                       for s in c["series"] if floor(s["total"])]}


def _r2(value):
    return None if value is None else round(value, 2)


def _sentiment(c: dict) -> dict:
    return {"scale": "-1 (negative) to +1 (positive)", "min_scored_mentions": c["min_n"],
            "brands": [{"brand": s["key"], "mean": _r2(s["mean"]), "previous_mean": _r2(s["prev_mean"]),
                        "change": _r2(s["delta"]),
                        "scored_mentions": s["total"] if s["mean"] is not None else None}
                       for s in c["series"]]}


def _emerging(c: dict) -> dict:
    terms = [t for t in c["terms"] if t["count"] >= MIN_COUNT and not trends.has_identifier(t["term"])]
    return {"baseline_days": c["baseline_days"],
            "terms": [{"term": t["term"], "mentions": t["count"], "baseline_mentions": floor(t["baseline_count"]),
                       "velocity": round(t["velocity"], 1), "new": t["is_new"]} for t in terms[:TOP_TERMS]]}


def _complaints(c: dict) -> dict:
    cells = []
    for i, subject in enumerate(c["rows"]):
        for j, theme in enumerate(c["themes"]):
            cell = c["cells"][i][j]
            if cell["count"]:
                cells.append({"brand": subject, "theme": theme["label"], "mentions": cell["count"],
                              "top_terms": [t["term"] for t in cell["terms"]
                                            if not trends.has_identifier(t["term"])]})
    cells.sort(key=lambda x: (-x["mentions"], x["brand"], x["theme"]))
    return {"complaint_mentions": floor(c["complaints"]), "top_clusters": cells[:TOP_CELLS]}


def _drugs(c: dict) -> dict:
    shown = [d for d in c["drugs"] if not d["suppressed"]]
    return {"drugs": [{"drug": d["drug"], "mentions": d["total"], "previous_period_mentions": d["prev_total"],
                       "change_pct": d["change_pct"]} for d in shown],
            "below_floor": [d["drug"] for d in c["drugs"] if d["suppressed"]]}


def _escalations(c: dict) -> dict:
    # Operational SLA numbers (the Urgent tab shows them to every viewer);
    # the per-kind split still follows the floor.
    return {"escalations": c["total"], "acknowledged": c["acked"],
            "median_minutes_to_acknowledge": c["median_ack_minutes"], "breached_pct": c["breached_pct"],
            "open_now": c["open"], "open_breached_now": c["open_breached"],
            "by_kind": [{"kind": s["key"], "count": s["total"]} for s in c["series"] if floor(s["total"])]}


def _matches_term(row: dict, term: str) -> bool:
    needle = term.lower()
    blob = " ".join([row.get("title") or "", row.get("text") or "",
                     *analytics._phrases(row.get("phrases_json"))]).lower()
    return needle in blob


async def _term_counts(state, p: analytics.Params, term: str, complaints_only: bool) -> dict:
    rows = await analytics.fetch_mentions(state, p, p.prev_start_utc, p.end_utc, with_text=True)
    if complaints_only:
        rows = [r for r in rows if r.get("category") == "complaint"]
    hits = [r for r in rows if _matches_term(r, term)]
    current, previous = analytics.split_periods(p, hits)
    by_category: dict[str, int] = {}
    for row in current:
        by_category[row.get("category") or "other"] = by_category.get(row.get("category") or "other", 0) + 1
    return {"term": term, "mentions": floor(len(current)), "previous_period_mentions": floor(len(previous)),
            "by_category": [{"category": k, "mentions": v} for k, v in sorted(by_category.items(),
                                                                                key=lambda kv: -kv[1])
                            if floor(v)] if not complaints_only else []}


async def _latest_brief(state) -> tuple[dict, str]:
    rows = await pulse_store.list_briefs(state, limit=1)
    if not rows:
        return {"brief": None}, "#pulse"
    brief = await pulse_store.get_brief(state, rows[0]["id"]) or rows[0]
    phrases = [p.get("phrase", "") for p in (brief.get("data") or {}).get("language_bank", [])]
    data = {"brief": {
        "period": brief.get("period"), "window_start": str(brief.get("window_start", ""))[:10],
        "status": brief.get("status"), "headline": briefs._scrub(brief.get("headline", ""), phrases),
        "top_actions": [briefs._scrub(c.get("title", ""), phrases)
                        for c in (brief.get("action_cards") or [])[:TOP_ACTIONS]],
    }}
    return data, f"#pulse-brief-{brief['id']}"


# ── Links ──


def _query(params: dict) -> str:
    clean = {k: v for k, v in params.items() if v not in (None, "", [])}
    return urlencode(clean, quote_via=quote, safe=",")


def analytics_link(days: int, filters: dict) -> str:
    return "#analytics?" + _query({
        "days": days, "platform": ",".join(filters.get("platforms") or []),
        "competitor": ",".join(filters.get("competitors") or []),
        "drug": filters.get("drug"), "category": filters.get("category")})


def feed_link(filters: dict, category: str | None = None) -> str:
    query = _query({"q": filters.get("term"), "category": category or filters.get("category"),
                    "drug": filters.get("drug"), "platform": (filters.get("platforms") or [None])[0]})
    return "#feed" + (f"?{query}" if query else "")


# ── Entry point ──


async def execute(state, spec: QuerySpec, config, now: datetime | None = None) -> QueryResult:
    """Run one spec. ``help`` returns an empty result; callers send help text."""
    now = now or _utcnow()
    if spec.intent == "help":
        return QueryResult("help", spec.days, {}, {}, "")
    if spec.intent == "latest_brief":
        data, link = await _latest_brief(state)
        return QueryResult("latest_brief", spec.days, {}, data, link)

    filters, ignored = await _resolve_filters(state, spec, now)
    p = _params(spec, filters, config, now)
    if spec.intent == "search_count":
        data = await _term_counts(state, p, spec.term, complaints_only=False)
        return QueryResult(spec.intent, spec.days, filters, data, feed_link(filters), ignored)
    if spec.intent == "complaints" and spec.term:
        data = await _term_counts(state, p, spec.term, complaints_only=True)
        return QueryResult(spec.intent, spec.days, filters, data, feed_link(filters, "complaint"), ignored)

    chart = await analytics.chart(state, _CHART[spec.intent], p, baseline_days=config.pulse.baseline_days,
                                  min_count=config.pulse.min_count)
    shape = {"volume": _volume, "share_of_voice": _share, "sentiment": _sentiment,
             "emerging_terms": _emerging, "complaints": _complaints, "drug_momentum": _drugs,
             "escalations_sla": _escalations}[spec.intent]
    link = "#urgent" if spec.intent == "escalations_sla" else analytics_link(spec.days, filters)
    return QueryResult(spec.intent, spec.days, filters, shape(chart), link, ignored)


def full_link(dashboard_url: str, fragment: str) -> str:
    """Absolute dashboard link, or "" when PULSE_DASHBOARD_URL is unset."""
    base = (dashboard_url or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/{fragment}" if fragment else f"{base}/"
