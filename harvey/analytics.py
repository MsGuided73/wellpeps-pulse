"""Analytics tab: graphic market intelligence from triaged mentions.

Aggregates only, never individuals: counts, shares and means per time bucket.
No Claude calls. Every chart works on *relevant, triaged* mentions (never
untriaged, dropped or irrelevant ones, see ``pulse_store.RELEVANT_WHERE``),
timed by ``posted_at`` falling back to ``collected_at``.

Time: stored timestamps are naive UTC. Ranges and buckets are *local days*
in the org timezone (``usage.quiet_hours.timezone``), so a bucket boundary
is local midnight and follows DST. Week buckets start on Monday. ``auto``
buckets by day up to 31 days, by week beyond. A range is at most 366 days.
Each chart compares against the previous period of the same length.

Privacy floor (the Phase 8 rule): nothing derived from fewer than
``MIN_COUNT`` (2) mentions is shown on its own. A series (competitor,
category, platform) below it folds into "Other"; a heatmap cell or a term
below it is suppressed; a drug below it shows no numbers. Sentiment points
need ``MIN_SENTIMENT_N`` (3) scored mentions or they are left out.

Complaint themes (heatmap) are a fixed, documented lexicon (``THEMES``):
a complaint mention counts toward every theme whose keywords appear in its
title, text or triage phrases (case-insensitive substring), else "other".
The tooltip's top terms are ``trends.extract_terms`` n-grams seen in at
least two of the cell's mentions, brand names excluded.

Colors are the frontend's job, but the server fixes *which* competitors are
named: the "anchor" set is the top ``TOP_COMPETITORS`` competitors by volume
over the last ``ANCHOR_LOOKBACK_DAYS`` days, unfiltered, so a date or
platform filter never reshuffles who is broken out (color follows entity).

SQL is portable (``harvey.db.dialect``): ``?`` params, ISO timestamps
computed in Python, no SQLite-only functions; bucketing happens in Python.
"""

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

import pytz

from harvey import pulse_store, trends
from harvey.models import Category, Platform

MAX_RANGE_DAYS = 366
DEFAULT_DAYS = 30
AUTO_DAY_LIMIT = 31
MIN_COUNT = 2
MIN_SENTIMENT_N = 3
TOP_COMPETITORS = 6
SENTIMENT_DEFAULT_COMPETITORS = 3
TOP_PLATFORMS = 6
MAX_SELECTED = 8
MAX_FILTER_CHARS = 120
ANCHOR_LOOKBACK_DAYS = 90
EMERGING_TOP = 15
TOP_CELL_TERMS = 3
MAX_DRUGS = 12
WELLPEPS = trends.WELLPEPS
OTHER = "Other"
BUCKETS = ("auto", "day", "week")
VOLUME_BY = ("category", "platform")
CHARTS = ("summary", "volume", "share", "sentiment", "complaints", "emerging", "drugs", "escalations")
TRACKED_DRUGS = ("semaglutide", "tirzepatide", "BPC-157", "NAD+", "minoxidil", "tadalafil")

# Severe categories are rare; one "safety & legal" series keeps the volume
# chart to at most seven series (six hues + Other).
CATEGORY_SERIES = {
    "adverse_event": "safety_legal", "legal_regulatory": "safety_legal",
    "privacy": "safety_legal", "billing_fraud": "safety_legal",
}
CATEGORY_ORDER = ("complaint", "praise", "question", "purchase_intent", "misinformation",
                  "safety_legal", "other")

# Complaint-theme lexicon: theme -> keywords (lowercase substrings).
THEMES: dict[str, tuple[str, ...]] = {
    "shipping": ("shipping", "delivery", "delivered", "transit", "tracking", "package", "arrived late"),
    "price": ("price", "cost", "expensive", "charged", "charge", "billing", "refund", "fee", "hike"),
    "support": ("support", "customer service", "reply", "respond", "response", "no answer", "ignored",
                "took two days"),
    "supply": ("shortage", "out of stock", "backorder", "on hold", "supply"),
    "refills": ("refill", "pharmacy", "prescription", "renewal"),
    "side_effects": ("side effect", "nausea", "nauseous", "headache", "shedding", "fatigue", "rough"),
    "results": ("plateau", "not working", "no results", "stalled", "stopped working", "gained"),
}
PLATFORM_NAMES = {"x": "X", "tiktok": "TikTok", "youtube": "YouTube", "bbb": "BBB",
                  "google_reviews": "Google reviews"}
THEME_LABELS = {"shipping": "Shipping & delivery", "price": "Price & billing", "support": "Support response",
                "supply": "Supply & shortages", "refills": "Refills & pharmacy",
                "side_effects": "Side effects", "results": "Results & plateaus", "other": "Other"}

_ROW_COLUMNS = (f"m.id, m.platform, {pulse_store.MENTION_TIME} AS at, t.subject_type, t.competitor, "
                "t.drug, t.category, t.sentiment_score")
_TEXT_COLUMNS = ", m.title, m.text, t.phrases_json"


class AnalyticsError(ValueError):
    """A bad request parameter (HTTP 400)."""


# ── Parameters ──


@dataclass(frozen=True)
class Params:
    start: date                  # first local day (inclusive)
    end: date                    # last local day + 1 (exclusive)
    bucket: str                  # "day" | "week"
    tz: str
    now: datetime                # naive UTC
    platforms: tuple[str, ...] = ()
    competitors: tuple[str, ...] = ()
    drug: str | None = None
    category: str | None = None

    @property
    def days(self) -> int:
        return (self.end - self.start).days

    def utc(self, day: date) -> datetime:
        """Local midnight of ``day`` as naive UTC."""
        local = pytz.timezone(self.tz).localize(datetime.combine(day, time()))
        return local.astimezone(pytz.utc).replace(tzinfo=None)

    @property
    def start_utc(self) -> datetime:
        return self.utc(self.start)

    @property
    def end_utc(self) -> datetime:
        return self.utc(self.end)

    @property
    def prev_start_utc(self) -> datetime:
        return self.utc(self.start - timedelta(days=self.days))

    @property
    def period_label(self) -> str:
        return f"the previous {self.days} day{'s' if self.days != 1 else ''}"

    def to_dict(self) -> dict:
        return {"from": self.start.isoformat(), "to": (self.end - timedelta(days=1)).isoformat(),
                "days": self.days, "bucket": self.bucket, "timezone": self.tz,
                "platforms": list(self.platforms), "competitors": list(self.competitors),
                "drug": self.drug, "category": self.category}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _split(values) -> list[str]:
    """Repeated and/or comma-joined query values -> distinct, stripped, in order."""
    out: list[str] = []
    for value in values or ():
        for part in str(value).split(","):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return out


def _date(value: str, name: str) -> date:
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise AnalyticsError(f"'{name}' must be a date like 2026-09-01")


def _range(days, start, end, today: date) -> tuple[date, date]:
    if start or end:
        if not (start and end):
            raise AnalyticsError("a custom range needs both 'from' and 'to'")
        first, last = _date(start, "from"), _date(end, "to")
        if last < first:
            raise AnalyticsError("'to' is before 'from'")
        span = (last - first).days + 1
        if span > MAX_RANGE_DAYS:
            raise AnalyticsError(f"a range can be at most {MAX_RANGE_DAYS} days (this one is {span})")
        return first, last + timedelta(days=1)
    try:
        count = DEFAULT_DAYS if days in (None, "") else int(str(days).strip())
    except ValueError:
        raise AnalyticsError("'days' must be a whole number")
    if not 1 <= count <= MAX_RANGE_DAYS:
        raise AnalyticsError(f"'days' must be between 1 and {MAX_RANGE_DAYS}")
    end_excl = today + timedelta(days=1)
    return end_excl - timedelta(days=count), end_excl


def _text_filter(value, name: str) -> str | None:
    value = (value or "").strip()
    if len(value) > MAX_FILTER_CHARS:
        raise AnalyticsError(f"'{name}' is longer than {MAX_FILTER_CHARS} characters")
    return value or None


def parse_params(*, days=None, start=None, end=None, bucket="auto", platforms=(), competitors=(),
                 drug=None, category=None, tz: str = "America/New_York",
                 now: datetime | None = None) -> Params:
    """Validate query parameters; raises AnalyticsError (HTTP 400)."""
    now = now or _utcnow()
    try:
        zone = pytz.timezone(tz)
    except pytz.UnknownTimeZoneError:
        raise AnalyticsError(f"unknown timezone '{tz}'")
    today = pytz.utc.localize(now).astimezone(zone).date()
    first, end_excl = _range(days, start, end, today)
    bucket = (bucket or "auto").strip().lower()
    if bucket not in BUCKETS:
        raise AnalyticsError(f"'bucket' must be one of {', '.join(BUCKETS)}")
    if bucket == "auto":
        bucket = "day" if (end_excl - first).days <= AUTO_DAY_LIMIT else "week"
    known = {p.value for p in Platform}
    plats = _split(platforms)
    for plat in plats:
        if plat not in known:
            raise AnalyticsError(f"unknown platform '{plat[:40]}'")
    comps = _split(competitors)
    if len(comps) > MAX_SELECTED:
        raise AnalyticsError(f"select at most {MAX_SELECTED} competitors")
    if any(len(c) > MAX_FILTER_CHARS for c in comps):
        raise AnalyticsError("competitor name too long")
    category = _text_filter(category, "category")
    if category and category not in {c.value for c in Category}:
        raise AnalyticsError(f"unknown category '{category[:40]}'")
    return Params(start=first, end=end_excl, bucket=bucket, tz=tz, now=now, platforms=tuple(plats),
                  competitors=tuple(comps), drug=_text_filter(drug, "drug"), category=category)


# ── Buckets ──


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def bucket_keys(p: Params) -> list[str]:
    """Every bucket in the range, oldest first (ISO date of its first day)."""
    step = 1 if p.bucket == "day" else 7
    day = p.start if p.bucket == "day" else _week_start(p.start)
    keys = []
    while day < p.end:
        keys.append(day.isoformat())
        day += timedelta(days=step)
    return keys


def local_day(p: Params, at: datetime) -> date:
    return pytz.utc.localize(at).astimezone(pytz.timezone(p.tz)).date()


def bucket_key(p: Params, at: datetime) -> str:
    day = local_day(p, at)
    return (day if p.bucket == "day" else _week_start(day)).isoformat()


# ── Rows ──


async def _rows(state, sql: str, params: tuple = ()) -> list[dict]:
    async with state.connect() as db:
        async with db.execute(sql, params) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


def mentions_sql(n_platforms: int = 0, with_text: bool = False) -> str:
    platform = f" AND m.platform IN ({', '.join('?' for _ in range(n_platforms))})" if n_platforms else ""
    return (f"SELECT {_ROW_COLUMNS}{_TEXT_COLUMNS if with_text else ''} "
            "FROM mentions m JOIN triage t ON t.mention_id = m.id "
            f"WHERE {pulse_store.RELEVANT_WHERE} AND {pulse_store.MENTION_TIME} >= ? "
            f"AND {pulse_store.MENTION_TIME} < ?{platform}")


ANCHORS_SQL = (
    "SELECT t.competitor AS competitor, COUNT(*) AS n FROM mentions m "
    "JOIN triage t ON t.mention_id = m.id "
    f"WHERE {pulse_store.RELEVANT_WHERE} AND COALESCE(t.competitor, '') != '' "
    f"AND {pulse_store.MENTION_TIME} >= ? GROUP BY t.competitor HAVING COUNT(*) >= ? "
    "ORDER BY n DESC, competitor ASC LIMIT ?"
)
ESCALATIONS_SQL = (
    "SELECT e.kind, e.created_at, e.sla_due_at, e.acked_at, e.breached, m.platform "
    "FROM escalations e JOIN mentions m ON m.id = e.mention_id "
    "WHERE e.created_at >= ? AND e.created_at < ?"
)
OPEN_ESCALATIONS_SQL = "SELECT e.sla_due_at, e.breached FROM escalations e WHERE e.acked_at IS NULL"
COMPETITOR_NAMES_SQL = (
    "SELECT DISTINCT t.competitor AS competitor FROM triage t WHERE COALESCE(t.competitor, '') != ''"
)
DRUG_NAMES_SQL = (
    "SELECT t.drug AS drug, COUNT(*) AS n FROM triage t WHERE COALESCE(t.drug, '') != '' "
    "GROUP BY t.drug HAVING COUNT(*) >= ? ORDER BY n DESC, drug ASC LIMIT ?"
)
ALL_SQL = (mentions_sql(), mentions_sql(3, True), ANCHORS_SQL, ESCALATIONS_SQL, OPEN_ESCALATIONS_SQL,
           COMPETITOR_NAMES_SQL, DRUG_NAMES_SQL)


def subject_of(row: dict) -> str:
    if row.get("competitor"):
        return row["competitor"]
    return WELLPEPS if row.get("subject_type") == "wellpeps" else ""


def _matches(row: dict, p: Params) -> bool:
    if p.competitors and subject_of(row) not in (*p.competitors, WELLPEPS):
        return False
    if p.drug and (row.get("drug") or "").lower() != p.drug.lower():
        return False
    return not (p.category and row.get("category") != p.category)


async def fetch_mentions(state, p: Params, start: datetime, end: datetime,
                         with_text: bool = False) -> list[dict]:
    """Relevant mentions in [start, end) passing the filters; ``at`` parsed."""
    rows = await _rows(state, mentions_sql(len(p.platforms), with_text),
                       (start.isoformat(), end.isoformat(), *p.platforms))
    kept = []
    for row in rows:
        row["at"] = trends._parse_at(row.get("at"))
        if row["at"] is not None and _matches(row, p):
            kept.append(row)
    return kept


def split_periods(p: Params, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """(current period, previous period of equal length)."""
    start, end, prev = p.start_utc, p.end_utc, p.prev_start_utc
    return ([r for r in rows if start <= r["at"] < end], [r for r in rows if prev <= r["at"] < start])


async def anchor_competitors(state, now: datetime | None = None) -> list[str]:
    """Top competitors by volume over the lookback window (stable, unfiltered)."""
    since = (now or _utcnow()) - timedelta(days=ANCHOR_LOOKBACK_DAYS)
    rows = await _rows(state, ANCHORS_SQL, (since.isoformat(), MIN_COUNT, TOP_COMPETITORS))
    return [r["competitor"] for r in rows]


# ── Small helpers ──


def pct_change(current: float, previous: float) -> float | None:
    return None if not previous else round((current - previous) / previous * 100, 1)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _signed(value: float, digits: int = 1, unit: str = "") -> str:
    return f"{'+' if value > 0 else '−' if value < 0 else '±'}{abs(value):.{digits}f}{unit}"


def _change_words(change: float | None, unit: str = "%", digits: int = 0) -> str:
    if change is None:
        return "with nothing to compare against"
    if round(change, digits) == 0:
        return "flat"
    return f"{'up' if change > 0 else 'down'} {abs(change):.{digits}f}{unit}"


def _series_order(keys, order) -> list:
    rank = {k: i for i, k in enumerate(order)}
    return sorted(keys, key=lambda k: (rank.get(k, len(order)), str(k)))


# ── a. Volume ──


def volume(p: Params, current: list[dict], previous: list[dict], by: str = "category") -> dict:
    """Mentions per bucket, stacked by category (or platform)."""
    if by not in VOLUME_BY:
        raise AnalyticsError(f"'by' must be one of {', '.join(VOLUME_BY)}")
    keys = bucket_keys(p)
    index = {k: i for i, k in enumerate(keys)}

    def key_of(row):
        if by == "platform":
            return row.get("platform") or "other"
        category = row.get("category") or "other"
        return CATEGORY_SERIES.get(category, category)

    totals = Counter(key_of(r) for r in current)
    if by == "platform":
        keep = {k for k, n in totals.most_common(TOP_PLATFORMS) if n >= MIN_COUNT and k != "other"}
    else:
        keep = {k for k, n in totals.items() if n >= MIN_COUNT and k in CATEGORY_ORDER}
    counts: dict[str, list[int]] = defaultdict(lambda: [0] * len(keys))
    for row in current:
        key = key_of(row)
        counts[key if key in keep else "other"][index[bucket_key(p, row["at"])]] += 1
    order = CATEGORY_ORDER if by == "category" else [p.value for p in Platform]
    series = [{"key": k, "values": counts[k], "total": sum(counts[k])}
              for k in _series_order(counts, order) if sum(counts[k])]
    total, prev_total = len(current), len(previous)
    change = pct_change(total, prev_total)
    takeaway = f"{total:,} relevant mention{'s' if total != 1 else ''}, {_change_words(change)} vs {p.period_label}"
    lead = max((s for s in series if s["key"] != "other"), key=lambda s: s["total"], default=None)
    if lead and total:
        takeaway += f"; {_key_label(lead['key'], by)} led with {lead['total'] / total:.0%}"
    return {"buckets": keys, "by": by, "series": series,
            "totals": [sum(s["values"][i] for s in series) for i in range(len(keys))],
            "total": total, "prev_total": prev_total, "change_pct": change, "takeaway": takeaway + "."}


def _key_label(key: str, by: str) -> str:
    if by == "platform":
        return PLATFORM_NAMES.get(key, key.replace("_", " ").title())
    return {"safety_legal": "safety & legal", "purchase_intent": "purchase intent"}.get(key, key.replace("_", " "))


# ── b. Share of voice ──


def _subjects_for(p: Params, anchors: list[str]) -> list[str]:
    return [WELLPEPS, *[c for c in (p.competitors or anchors) if c != WELLPEPS]]


def share_of_voice(p: Params, current: list[dict], previous: list[dict], anchors: list[str]) -> dict:
    """Brand-level share per bucket (sums to 1 wherever a bucket has mentions)."""
    keys = bucket_keys(p)
    index = {k: i for i, k in enumerate(keys)}
    subjects = _subjects_for(p, anchors)
    now = Counter(subject_of(r) for r in current if subject_of(r))
    before = Counter(subject_of(r) for r in previous if subject_of(r))
    named = [s for s in subjects if now[s] >= MIN_COUNT]

    def key_of(subject):
        return subject if subject in named else OTHER

    counts: dict[str, list[int]] = defaultdict(lambda: [0] * len(keys))
    for row in current:
        subject = subject_of(row)
        if subject:
            counts[key_of(subject)][index[bucket_key(p, row["at"])]] += 1
    bucket_totals = [sum(counts[k][i] for k in counts) for i in range(len(keys))]
    total, prev_total = sum(now.values()), sum(before.values())
    prev_by_key = Counter()
    for subject, n in before.items():
        prev_by_key[key_of(subject)] += n
    series = []
    for key in [*named, OTHER]:
        if key == OTHER and not sum(counts[key]):
            continue
        share = sum(counts[key]) / total if total else None
        prev_share = prev_by_key[key] / prev_total if prev_total else None
        series.append({
            "key": key, "counts": counts[key], "total": sum(counts[key]),
            "values": [counts[key][i] / t if t else None for i, t in enumerate(bucket_totals)],
            "share": _r(share), "prev_share": _r(prev_share),
            "delta_pts": _r((share - prev_share) * 100, 1) if share is not None and prev_share is not None else None,
        })
    return {"buckets": keys, "series": series, "bucket_totals": bucket_totals, "total": total,
            "prev_total": prev_total, "takeaway": _share_takeaway(series, p)}


def _r(value, places: int = 4):
    return None if value is None else round(value, places)


def _share_takeaway(series: list[dict], p: Params) -> str:
    wellpeps = next((s for s in series if s["key"] == WELLPEPS), None)
    if wellpeps is None:
        return f"Fewer than {MIN_COUNT} brand mentions of WellPeps in this period, so its share is not shown."
    text = f"WellPeps holds {wellpeps['share'] * 100:.1f}% share of voice"
    if wellpeps["delta_pts"] is not None:
        text += f", {_change_words(wellpeps['delta_pts'], ' pts', 1)} vs {p.period_label}"
    leader = max((s for s in series if s["key"] not in (WELLPEPS, OTHER)), key=lambda s: s["total"], default=None)
    if leader and leader["total"] > wellpeps["total"]:
        text += f"; {leader['key']} leads at {leader['share'] * 100:.1f}%"
    return text + "."


# ── c. Sentiment ──


def sentiment(p: Params, current: list[dict], previous: list[dict], anchors: list[str]) -> dict:
    """Mean sentiment_score per bucket; a point needs MIN_SENTIMENT_N mentions."""
    keys = bucket_keys(p)
    index = {k: i for i, k in enumerate(keys)}
    subjects = [WELLPEPS, *[c for c in (p.competitors or anchors[:SENTIMENT_DEFAULT_COMPETITORS])
                            if c != WELLPEPS]]
    scores: dict[str, list[list[float]]] = {s: [[] for _ in keys] for s in subjects}
    period: dict[str, list[float]] = defaultdict(list)
    prev: dict[str, list[float]] = defaultdict(list)
    for row in current:
        subject = subject_of(row)
        if subject in scores:
            value = float(row.get("sentiment_score") or 0.0)
            scores[subject][index[bucket_key(p, row["at"])]].append(value)
            period[subject].append(value)
    for row in previous:
        if subject_of(row) in scores:
            prev[subject_of(row)].append(float(row.get("sentiment_score") or 0.0))
    series = []
    for subject in subjects:
        n = len(period[subject])
        mean = _mean(period[subject]) if n >= MIN_SENTIMENT_N else None
        prev_mean = _mean(prev[subject]) if len(prev[subject]) >= MIN_SENTIMENT_N else None
        series.append({
            "key": subject,
            "values": [_mean(b) if len(b) >= MIN_SENTIMENT_N else None for b in scores[subject]],
            "n": [len(b) for b in scores[subject]],
            "mean": mean, "prev_mean": prev_mean, "total": n,
            "delta": round(mean - prev_mean, 3) if mean is not None and prev_mean is not None else None,
        })
    return {"buckets": keys, "series": series, "min_n": MIN_SENTIMENT_N,
            "takeaway": _sentiment_takeaway(series, p)}


def _sentiment_takeaway(series: list[dict], p: Params) -> str:
    wellpeps = series[0]
    if wellpeps["mean"] is None:
        return f"Fewer than {MIN_SENTIMENT_N} scored WellPeps mentions in this period, so no WellPeps average."
    text = f"WellPeps averaged {_signed(wellpeps['mean'], 2)}"
    if wellpeps["delta"] is not None:
        text += f" ({_signed(wellpeps['delta'], 2)} vs {p.period_label})"
    others = [s for s in series[1:] if s["mean"] is not None]
    if others:
        low = min(others, key=lambda s: s["mean"])
        text += f"; {low['key']} was lowest at {_signed(low['mean'], 2)}"
    return text + " on a −1 to +1 scale."


# ── d. Complaint heatmap ──


def _phrases(raw) -> list[str]:
    try:
        phrases = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(x) for x in phrases if isinstance(x, str)] if isinstance(phrases, list) else []


def themes_of(row: dict) -> list[str]:
    """Complaint themes a mention matches (``THEMES`` lexicon), else ["other"]."""
    blob = " ".join([row.get("title") or "", row.get("text") or "", *_phrases(row.get("phrases_json"))])
    blob = blob.lower()
    found = [theme for theme, words in THEMES.items() if any(w in blob for w in words)]
    return found or ["other"]


def _cell_terms(docs: list[set[str]], brand: set[str]) -> list[dict]:
    counts: Counter = Counter()
    for terms in docs:
        counts.update(t for t in terms if not brand & set(t.split(" ")))
    ranked = [{"term": t, "count": c} for t, c in counts.items() if c >= MIN_COUNT]
    ranked.sort(key=lambda r: (-r["count"], -r["term"].count(" "), r["term"]))
    return trends._drop_riders(ranked, lambda r: r["count"])[:TOP_CELL_TERMS]


def complaint_heatmap(p: Params, current: list[dict], anchors: list[str]) -> dict:
    """Subjects × complaint themes; cell = complaint mentions (suppressed below MIN_COUNT)."""
    subjects = _subjects_for(p, anchors)
    complaints = [r for r in current if r.get("category") == "complaint" and subject_of(r)]
    per_subject = Counter(subject_of(r) for r in complaints)
    named = [s for s in subjects if per_subject[s] >= MIN_COUNT]
    docs: dict[tuple[str, str], list[set[str]]] = defaultdict(list)
    for row in complaints:
        subject = subject_of(row)
        key = subject if subject in named else OTHER
        terms = trends._mention_terms(row)
        for theme in themes_of(row):
            docs[(key, theme)].append(terms)
    rows = [s for s in [*named, OTHER] if any(docs.get((s, t)) for t in [*THEMES, "other"])]
    themes = [t for t in [*THEMES, "other"] if any(len(docs.get((s, t), [])) >= MIN_COUNT for s in rows)]
    brand = {s: trends._brand_tokens(s) if s != OTHER else set() for s in rows}
    cells = []
    for subject in rows:
        line = []
        for theme in themes:
            n = len(docs.get((subject, theme), []))
            line.append({"count": n if n >= MIN_COUNT else None, "suppressed": 0 < n < MIN_COUNT,
                         "terms": _cell_terms(docs[(subject, theme)], brand[subject]) if n >= MIN_COUNT else []})
        cells.append(line)
    peak = max(((cells[i][j]["count"] or 0, rows[i], themes[j]) for i in range(len(rows))
                for j in range(len(themes))), default=(0, "", ""))
    takeaway = (f"The biggest complaint cluster is {peak[1]} × {THEME_LABELS[peak[2]].lower()} "
                f"({peak[0]} mentions)." if peak[0] else
                f"No complaint theme reached {MIN_COUNT} mentions for any brand in this period.")
    return {"rows": rows, "themes": [{"key": t, "label": THEME_LABELS[t]} for t in themes],
            "cells": cells, "complaints": len(complaints), "takeaway": takeaway,
            "method": ("Complaint mentions per brand, matched to themes by a fixed keyword lexicon "
                       f"(a mention can match several). Cells under {MIN_COUNT} are hidden.")}


# ── e. Emerging terms ──


def emerging(p: Params, current: list[dict], baseline: list[dict], baseline_days: int,
             min_count: int) -> dict:
    """Top velocity terms for the window (trends.rank_terms), aggregates only.

    Bare brand names (WellPeps and any brand in the window, with aliases) are
    not "emerging terms": share of voice already covers them."""
    brand: set[str] = set()
    for subject in {WELLPEPS, *(subject_of(r) for r in current)} - {""}:
        brand |= trends._brand_tokens(subject)

    def terms_of(row):
        return {t for t in trends._mention_terms(row) if not set(t.split(" ")) <= brand}

    ranked = trends.rank_terms(
        [(r["id"], terms_of(r)) for r in current],
        [terms_of(r) for r in baseline],
        window_days=p.days, baseline_days=baseline_days,
        min_count=max(int(min_count), MIN_COUNT), top_n=EMERGING_TOP,
    )
    terms = [{"term": t.term, "count": t.count, "baseline_count": t.baseline_count,
              "velocity": round(t.velocity, 2), "is_new": t.is_new} for t in ranked]
    if terms:
        top = max(terms, key=lambda t: (t["velocity"], t["count"]))
        takeaway = (f"'{top['term']}' is rising fastest: {top['count']} mentions, "
                    + ("new this period" if top["is_new"] else f"{top['velocity']:.1f}× its baseline rate") + ".")
    else:
        takeaway = f"No term reached {max(int(min_count), MIN_COUNT)} mentions in this period."
    return {"terms": terms, "baseline_days": baseline_days, "new": sum(t["is_new"] for t in terms),
            "takeaway": takeaway}


# ── f. Drug momentum ──


def drug_momentum(p: Params, current: list[dict], previous: list[dict]) -> dict:
    """Mentions per bucket per drug (small multiples), with % change."""
    keys = bucket_keys(p)
    index = {k: i for i, k in enumerate(keys)}
    canon = {d.lower(): d for d in TRACKED_DRUGS}

    def name(row):
        drug = (row.get("drug") or "").strip()
        return canon.get(drug.lower(), drug) if drug else ""

    now = Counter(name(r) for r in current if name(r))
    before = Counter(name(r) for r in previous if name(r))
    extra = [d for d, n in now.most_common() if d not in TRACKED_DRUGS and n >= MIN_COUNT]
    drugs = [*TRACKED_DRUGS, *extra][:MAX_DRUGS]
    values: dict[str, list[int]] = {d: [0] * len(keys) for d in drugs}
    for row in current:
        if name(row) in values:
            values[name(row)][index[bucket_key(p, row["at"])]] += 1
    items = []
    for drug in drugs:
        shown = now[drug] >= MIN_COUNT
        items.append({"drug": drug, "total": now[drug] if shown else None,
                      "prev_total": before[drug] if before[drug] >= MIN_COUNT else None,
                      "values": values[drug] if shown else None, "suppressed": not shown,
                      "change_pct": pct_change(now[drug], before[drug]) if shown and before[drug] >= MIN_COUNT else None})
    items.sort(key=lambda d: (d["suppressed"], -(d["total"] or 0), d["drug"].lower()))
    movers = [d for d in items if d["change_pct"] is not None]
    if movers:
        mover = max(movers, key=lambda d: abs(d["change_pct"]))
        takeaway = f"{mover['drug']} is {_change_words(mover['change_pct'])} vs {p.period_label}"
        if items[0]["drug"] != mover["drug"]:
            takeaway += f"; {items[0]['drug']} leads volume ({items[0]['total']})"
    elif items and not items[0]["suppressed"]:
        takeaway = f"{items[0]['drug']} leads volume ({items[0]['total']} mentions)"
    else:
        takeaway = f"No drug reached {MIN_COUNT} mentions in this period"
    return {"buckets": keys, "drugs": items, "takeaway": takeaway + "."}


# ── g. Escalations & SLA ──


def _ts(value) -> datetime | None:
    return trends._parse_at(value)


def _open_breached(p: Params, open_rows: list[dict]) -> int:
    """Open escalations flagged breached, or past their SLA as of ``p.now``."""
    def late(row):
        due = _ts(row.get("sla_due_at"))
        return due is not None and due <= p.now
    return sum(1 for r in open_rows if bool(r.get("breached")) or late(r))


def escalation_stats(p: Params, rows: list[dict], open_rows: list[dict]) -> dict:
    """Escalations per bucket by kind; median minutes to ack; % breached."""
    keys = bucket_keys(p)
    index = {k: i for i, k in enumerate(keys)}
    counts: dict[str, list[int]] = defaultdict(lambda: [0] * len(keys))
    ack_minutes, breached = [], 0
    kept = 0
    for row in rows:
        created = _ts(row.get("created_at"))
        if created is None or not p.start_utc <= created < p.end_utc:
            continue
        if p.platforms and row.get("platform") not in p.platforms:
            continue
        kept += 1
        counts[row.get("kind") or "other"][index[bucket_key(p, created)]] += 1
        acked, due = _ts(row.get("acked_at")), _ts(row.get("sla_due_at"))
        if acked is not None:
            ack_minutes.append(max(0.0, (acked - created).total_seconds() / 60))
        late = (acked is not None and due is not None and acked > due) or \
               (acked is None and due is not None and due <= p.now)
        breached += bool(row.get("breached")) or late
    open_breached = _open_breached(p, open_rows)
    median = round(statistics.median(ack_minutes), 1) if ack_minutes else None
    breached_pct = round(breached / kept * 100, 1) if kept else None
    series = [{"key": k, "values": counts[k], "total": sum(counts[k])} for k in sorted(counts)]
    if kept:
        takeaway = (f"{kept} escalation{'s' if kept != 1 else ''} in this period; "
                    + (f"median time to acknowledge {median:g} min" if median is not None else "none acknowledged yet")
                    + f"; {breached_pct:g}% breached the SLA.")
    else:
        takeaway = "No escalations in this period."
    return {"buckets": keys, "series": series, "total": kept, "acked": len(ack_minutes),
            "median_ack_minutes": median, "breached": breached, "breached_pct": breached_pct,
            "open": len(open_rows), "open_breached": open_breached, "takeaway": takeaway}


# ── h. Summary tiles ──


def summary(p: Params, current: list[dict], previous: list[dict], share: dict, senti: dict,
            emerging_terms: dict, open_rows: list[dict]) -> dict:
    wellpeps_share = next((s for s in share["series"] if s["key"] == WELLPEPS), None)
    wellpeps_senti = senti["series"][0]
    open_breached = _open_breached(p, open_rows)
    tiles = {
        "mentions": {"value": len(current), "prev": len(previous),
                     "change_pct": pct_change(len(current), len(previous))},
        "share": {"value": wellpeps_share["share"] if wellpeps_share else None,
                  "delta_pts": wellpeps_share["delta_pts"] if wellpeps_share else None},
        "sentiment": {"value": wellpeps_senti["mean"], "delta": wellpeps_senti["delta"],
                      "n": wellpeps_senti["total"]},
        "open_escalations": {"value": len(open_rows), "breached": open_breached},
        "emerging": {"value": len(emerging_terms["terms"]), "new": emerging_terms["new"]},
    }
    return {"tiles": tiles, "takeaway": share["takeaway"]}


# ── Dispatch ──


async def options(state, now: datetime | None = None) -> dict:
    """Filter choices: platforms, competitors (anchors first), categories, drugs."""
    anchors = await anchor_competitors(state, now)
    seen = sorted({r["competitor"] for r in await _rows(state, COMPETITOR_NAMES_SQL)} - set(anchors),
                  key=str.lower)
    try:
        from harvey import knowledge

        known = [c.name for c in knowledge.competitors().competitors]
    except Exception:  # knowledge files missing: names seen in the data only
        known = []
    rest = sorted((set(seen) | set(known)) - set(anchors), key=str.lower)
    drugs = [r["drug"] for r in await _rows(state, DRUG_NAMES_SQL, (MIN_COUNT, 40))]
    tracked = {d.lower() for d in TRACKED_DRUGS}
    return {"anchors": anchors, "competitors": [*anchors, *rest],
            "platforms": [p.value for p in Platform], "categories": [c.value for c in Category],
            "drugs": [*TRACKED_DRUGS, *sorted({d for d in drugs if d.lower() not in tracked}, key=str.lower)],
            "max_days": MAX_RANGE_DAYS, "min_count": MIN_COUNT, "min_sentiment_n": MIN_SENTIMENT_N}


async def chart(state, name: str, p: Params, *, baseline_days: int = 28, min_count: int = 3,
                by: str = "category") -> dict:
    """One chart's payload (``name`` in CHARTS)."""
    if name not in CHARTS:
        raise KeyError(name)
    if name == "escalations":
        rows = await _rows(state, ESCALATIONS_SQL, (p.start_utc.isoformat(), p.end_utc.isoformat()))
        open_rows = await _rows(state, OPEN_ESCALATIONS_SQL)
        return {"params": p.to_dict(), **escalation_stats(p, rows, open_rows)}
    anchors = await anchor_competitors(state, p.now)
    with_text = name in ("complaints", "emerging", "summary")
    start = min(p.prev_start_utc, p.start_utc - timedelta(days=baseline_days))
    rows = await fetch_mentions(state, p, start, p.end_utc, with_text=with_text)
    current, previous = split_periods(p, rows)
    payload: dict
    if name == "volume":
        payload = volume(p, current, previous, by=by)
    elif name == "share":
        payload = share_of_voice(p, current, previous, anchors)
    elif name == "sentiment":
        payload = sentiment(p, current, previous, anchors)
    elif name == "complaints":
        payload = complaint_heatmap(p, current, anchors)
    elif name == "drugs":
        payload = drug_momentum(p, current, previous)
    else:
        base_start = p.start_utc - timedelta(days=baseline_days)
        baseline = [r for r in rows if base_start <= r["at"] < p.start_utc]
        terms = emerging(p, current, baseline, baseline_days, min_count)
        if name == "emerging":
            payload = terms
        else:
            open_rows = await _rows(state, OPEN_ESCALATIONS_SQL)
            payload = summary(p, current, previous, share_of_voice(p, current, previous, anchors),
                              sentiment(p, current, previous, anchors), terms, open_rows)
    return {"params": p.to_dict(), "anchors": anchors, **payload}
