"""Pulse trends (Phase 8): deterministic market signals, no Claude calls.

Everything here works on *triaged, relevant* mentions (never dropped or
untriaged ones) and produces aggregates only: term counts, shares, means.
Nothing profiles an individual author; handles, URLs and e-mail addresses
are stripped before tokenizing.

- ``tokenize`` / ``extract_terms``: lowercase tokens (hyphenated drug names
  like ``bpc-157`` survive), then unigrams + bigrams + trigrams that never
  span a stopword. Terms are counted once per mention (document frequency),
  so one long rant can't make a trend.
- ``rank_terms``: velocity = (window_rate + α) / (baseline_rate + α) per
  day, α-smoothed; score = velocity × log(1 + count). A term with no
  baseline mentions is ``new``. A sub-term that only ever appears inside a
  longer ranked term ("shipping" inside "shipping delay") is dropped.
- ``share_of_voice``, ``sentiment_shift``, ``mix``, ``complaint_themes``:
  window vs the previous window of equal length.
- ``bank_language``: verbatim triage phrases into the language bank,
  idempotent per mention.
"""

import json
import logging
import math
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from harvey import pulse_store

logger = logging.getLogger("harvey.trends")

ALPHA = 0.5             # smoothing, mentions per day
MAX_EXAMPLES = 3
MAX_TERM_TOKENS = 3
MAX_TEXT_CHARS = 5000   # per mention, for tokenizing
MAX_PHRASE_CHARS = 200
WELLPEPS = "WellPeps"
SHORT_TERMS = frozenset({"ed"})
# A complaint theme needs at least this many mentions: a word from a single
# post is an anecdote (and could point at its author), not a theme.
MIN_THEME_COUNT = 2  # meaningful two-letter terms (erectile dysfunction)

STOPWORDS = frozenset("""
a about above after again against ago all almost also am an and another any anyone anything are aren arent
around as at away back be because been before being below between both but by can cannot cant could
couldnt did didnt do does doesnt doing done dont down during each else even ever every everyone
everything few for from further get gets getting go goes going gone got gotten had hadnt has hasnt
have havent having he hed hell her here hers herself hes him himself his how however i id ill im
into is isnt it its itself ive just know last least less let lets like literally made make makes
many may maybe me might mine more most much must my myself need needs never new next no nobody none
nor not nothing now of off often oh ok okay old on once one only onto or other others otherwise our
ours ourselves out over own per please pretty put quite rather re really right said same say says
see seem seems shall she shed shell shes should shouldnt since so some someone something still such
sure take than that thats the their theirs them themselves then there theres these they theyd
theyll theyre theyve thing things think this those though through thru to too took toward under
until up upon us use used using very via want wants was wasnt way we wed well went were werent weve
what whats when where whether which while who whole whom whos whose why will with within without
wont would wouldnt yeah yes yet you youd youll your youre yours yourself yourselves youve
day days week weeks month months year years today tomorrow yesterday time times morning night
seeing seen saw lot lots bit kind sort stuff anyway actually basically
lol lmao lmk tbh imo imho idk omg btw fyi smh ngl irl af
edit edited deleted removed post posts posted posting comment comments thread threads reddit
subreddit sub subs upvote upvotes downvote op dm dms link links bio pic pics video reel story
amp gt lt nbsp http https www com org net html
""".split())

_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_EMAIL = re.compile(r"\S+@\S+\.\S+")
_HANDLE = re.compile(r"(?<![\w/])@[\w.]+|\b[ur]/[\w-]+", re.I)
_APOSTROPHE = re.compile(r"['’‘`]")
_TOKEN = re.compile(r"[^\W_]+(?:-[^\W_]+)*")
_HAS_LETTER = re.compile(r"[^\W\d_]")


# ── Tokenizing ──


# Clause boundaries: n-grams never span punctuation or a line break.
_BREAK = re.compile(r"[.,;:!?()\[\]{}\"\n\r\t|/]+|\s-\s")


def has_identifier(text: str) -> bool:
    """True when ``text`` holds a URL, an e-mail address or a handle."""
    text = text or ""
    return bool(_URL.search(text) or _EMAIL.search(text) or _HANDLE.search(text))


def strip_identifiers(text: str) -> str:
    """Replace URLs, e-mail addresses and handles with placeholders."""
    text = _URL.sub("[link]", text or "")
    text = _EMAIL.sub("[email]", text)
    return _HANDLE.sub("[handle]", text)


def _keep(token: str) -> bool:
    if not _HAS_LETTER.search(token):
        return False
    plain = "-" not in token and token.isalpha()
    return len(token) >= (3 if plain else 2) or token in SHORT_TERMS


def _segments(text: str) -> list[list[str | None]]:
    """Clauses of tokens; a dropped token stays as None so n-grams never
    bridge it (or a clause boundary)."""
    text = (text or "")[:MAX_TEXT_CHARS].lower()
    text = _URL.sub(" . ", text)
    text = _EMAIL.sub(" . ", text)
    text = _HANDLE.sub(" . ", text)
    text = _APOSTROPHE.sub("", text)
    return [[t if _keep(t) else None for t in _TOKEN.findall(clause)]
            for clause in _BREAK.split(text)]


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens with URLs, e-mails, @handles, u/ and r/ names,
    emoji and punctuation removed. Hyphenated names (bpc-157, glp-1) stay
    whole; pure numbers and very short tokens are dropped."""
    return [t for clause in _segments(text) for t in clause if t is not None]


def extract_terms(text: str, max_n: int = MAX_TERM_TOKENS) -> set[str]:
    """Unigrams, bigrams and trigrams within a clause that contain no
    stopword and skip no dropped token."""
    terms: set[str] = set()
    for clause in _segments(text):
        for n in range(1, max_n + 1):
            for i in range(len(clause) - n + 1):
                gram = clause[i:i + n]
                if all(t is not None and t not in STOPWORDS for t in gram):
                    terms.add(" ".join(gram))
    return terms


def _mention_terms(row: dict) -> set[str]:
    return extract_terms(f"{row.get('title') or ''}\n{row.get('text') or ''}")


def _contains(longer: str, shorter: str) -> bool:
    return longer != shorter and f" {shorter} " in f" {longer} "


def _drop_riders(ranked: list, count_of) -> list:
    """Drop a term when a longer ranked term contains it with the same count."""
    kept = []
    for item in ranked:
        term, count = item_term(item), count_of(item)
        if any(_contains(item_term(other), term) and count_of(other) == count for other in ranked):
            continue
        kept.append(item)
    return kept


def item_term(item) -> str:
    return item.term if isinstance(item, TrendTerm) else item["term"]


# ── Ranking ──


@dataclass(frozen=True)
class TrendTerm:
    term: str
    count: int
    baseline_count: int
    window_rate: float
    baseline_rate: float
    velocity: float
    score: float
    is_new: bool
    example_ids: tuple[int, ...] = ()


def rank_terms(window_docs: list[tuple[int, set[str]]], baseline_docs: list[set[str]],
               window_days: float, baseline_days: float, min_count: int = 3,
               top_n: int = 25, alpha: float = ALPHA) -> list[TrendTerm]:
    """Rank window terms by velocity × log(1 + count).

    ``window_docs`` is [(mention_id, terms)] newest first (examples are the
    first ids seen); ``baseline_docs`` is [terms] for the baseline period.
    """
    window_days = max(float(window_days), 1e-9)
    baseline_days = max(float(baseline_days), 1e-9)
    counts: Counter = Counter()
    examples: dict[str, list[int]] = defaultdict(list)
    for mention_id, terms in window_docs:
        for term in terms:
            counts[term] += 1
            if len(examples[term]) < MAX_EXAMPLES:
                examples[term].append(mention_id)
    baseline: Counter = Counter()
    for terms in baseline_docs:
        baseline.update(terms)

    ranked = []
    for term, count in counts.items():
        if count < min_count:
            continue
        base = baseline[term]
        window_rate, baseline_rate = count / window_days, base / baseline_days
        velocity = (window_rate + alpha) / (baseline_rate + alpha)
        ranked.append(TrendTerm(
            term=term, count=count, baseline_count=base, window_rate=window_rate,
            baseline_rate=baseline_rate, velocity=velocity, score=velocity * math.log1p(count),
            is_new=base == 0, example_ids=tuple(examples[term]),
        ))
    # Ties go to the longer phrase: "price hike" says more than "plan".
    ranked.sort(key=lambda t: (-t.score, -t.count, -t.term.count(" "), t.term))
    return _drop_riders(ranked, lambda t: t.count)[:max(int(top_n), 0)]


# ── Subject aggregates (window vs previous window) ──


def _subject(row: dict) -> str:
    if row.get("competitor"):
        return row["competitor"]
    return WELLPEPS if row.get("subject_type") == "wellpeps" else ""


def share_of_voice(current: list[dict], previous: list[dict]) -> list[dict]:
    """Counts per competitor (+ WellPeps); shares sum to 1 within each window."""
    now = Counter(s for s in map(_subject, current) if s)
    before = Counter(s for s in map(_subject, previous) if s)
    total, prev_total = sum(now.values()), sum(before.values())
    rows = []
    for subject in set(now) | set(before):
        share = now[subject] / total if total else 0.0
        prev_share = before[subject] / prev_total if prev_total else 0.0
        rows.append({"subject": subject, "count": now[subject], "share": share,
                     "prev_count": before[subject], "prev_share": prev_share,
                     "delta": share - prev_share})
    rows.sort(key=lambda r: (-r["count"], -r["prev_count"], r["subject"]))
    return rows


def _sentiment_keys(row: dict) -> list[tuple[str, str]]:
    keys = []
    if row.get("subject_type") == "wellpeps":
        keys.append(("wellpeps", WELLPEPS))
    if row.get("competitor"):
        keys.append(("competitor", row["competitor"]))
    if row.get("drug"):
        keys.append(("drug", row["drug"]))
    return keys


def _means(rows: list[dict]) -> dict[tuple[str, str], tuple[float, int]]:
    sums: dict = defaultdict(float)
    counts: Counter = Counter()
    for row in rows:
        for key in _sentiment_keys(row):
            sums[key] += float(row.get("sentiment_score") or 0.0)
            counts[key] += 1
    return {key: (sums[key] / counts[key], counts[key]) for key in counts}


def sentiment_shift(current: list[dict], previous: list[dict]) -> list[dict]:
    """Mean sentiment_score per WellPeps / competitor / drug, window vs previous."""
    now, before = _means(current), _means(previous)
    rows = []
    for kind, subject in set(now) | set(before):
        mean, n = now.get((kind, subject), (None, 0))
        prev_mean, prev_n = before.get((kind, subject), (None, 0))
        delta = mean - prev_mean if mean is not None and prev_mean is not None else None
        rows.append({"kind": kind, "subject": subject,
                     "mean": _round(mean), "prev_mean": _round(prev_mean), "delta": _round(delta),
                     "n": n, "prev_n": prev_n})
    order = {"wellpeps": 0, "competitor": 1, "drug": 2}
    rows.sort(key=lambda r: (order[r["kind"]], -r["n"], r["subject"]))
    return rows


def mix(current: list[dict], previous: list[dict], key: str) -> list[dict]:
    """Counts per ``key`` value (e.g. category, drug) with the change."""
    now = Counter(r[key] for r in current if r.get(key))
    before = Counter(r[key] for r in previous if r.get(key))
    rows = [{key: value, "count": now[value], "prev_count": before[value],
             "delta": now[value] - before[value]} for value in set(now) | set(before)]
    rows.sort(key=lambda r: (-r["count"], -r["prev_count"], r[key]))
    return rows


def _brand_tokens(subject: str) -> set[str]:
    names = [subject]
    if subject == WELLPEPS:
        names += ["wellpeps", "well peps", "wellpep"]
    else:
        try:
            from harvey import knowledge

            for comp in knowledge.competitors().competitors:
                if comp.name == subject:
                    names += list(comp.aliases)
        except Exception as exc:  # knowledge files missing: names only
            logger.debug(f"competitor aliases unavailable: {exc}")
    return {t for name in names for t in tokenize(name)}


def complaint_themes(rows: list[dict], top: int = 5) -> list[dict]:
    """Top terms among complaint mentions, per competitor (and WellPeps).

    The brand's own name and aliases are not themes.
    """
    groups: dict[str, list[set[str]]] = defaultdict(list)
    for row in rows:
        subject = _subject(row)
        if subject and row.get("category") == "complaint":
            groups[subject].append(_mention_terms(row))
    themes = []
    for subject, docs in groups.items():
        brand = _brand_tokens(subject)
        counts: Counter = Counter()
        for terms in docs:
            counts.update(t for t in terms if not brand & set(t.split(" ")))
        ranked = [{"term": t, "count": c} for t, c in counts.items() if c >= MIN_THEME_COUNT]
        ranked.sort(key=lambda r: (-r["count"], -r["term"].count(" "), r["term"]))
        ranked = _drop_riders(ranked, lambda r: r["count"])[:top]
        themes.append({"subject": subject, "mentions": len(docs), "terms": ranked})
    themes.sort(key=lambda t: (-t["mentions"], t["subject"]))
    return themes


def _round(value: float | None, places: int = 3) -> float | None:
    return None if value is None else round(value, places)


# ── Report ──


@dataclass
class TrendReport:
    window_start: datetime
    window_end: datetime
    baseline_days: int
    mentions: int = 0
    previous_mentions: int = 0
    baseline_mentions: int = 0
    terms: list[TrendTerm] = field(default_factory=list)
    share_of_voice: list[dict] = field(default_factory=list)
    sentiment: list[dict] = field(default_factory=list)
    category_mix: list[dict] = field(default_factory=list)
    drug_mix: list[dict] = field(default_factory=list)
    complaint_themes: list[dict] = field(default_factory=list)

    @property
    def window_days(self) -> float:
        return (self.window_end - self.window_start).total_seconds() / 86400

    def to_dict(self) -> dict:
        data = asdict(self)
        data["window_start"] = self.window_start.isoformat()
        data["window_end"] = self.window_end.isoformat()
        data["window_days"] = round(self.window_days, 3)
        for term in data["terms"]:
            term["example_ids"] = list(term["example_ids"])
        return data


def _parse_at(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace(" ", "T"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


async def compute_trends(state, window_start: datetime, window_end: datetime,
                         baseline_days: int = 28, min_count: int = 3, top_n: int = 25) -> TrendReport:
    """Every Pulse aggregate for [window_start, window_end) (naive UTC)."""
    if window_end <= window_start:
        raise ValueError("window_end must be after window_start")
    length = window_end - window_start
    prev_start = window_start - length
    base_start = window_start - timedelta(days=baseline_days)
    current, previous, baseline = [], [], []
    for row in await pulse_store.fetch_rows(state, min(prev_start, base_start), window_end):
        at = _parse_at(row.get("at"))
        if at is None:
            continue
        if at >= window_start:
            current.append(row)
            continue
        if at >= prev_start:
            previous.append(row)
        if at >= base_start:
            baseline.append(row)

    report = TrendReport(window_start=window_start, window_end=window_end,
                         baseline_days=int(baseline_days), mentions=len(current),
                         previous_mentions=len(previous), baseline_mentions=len(baseline))
    report.terms = rank_terms(
        [(r["id"], _mention_terms(r)) for r in current], [_mention_terms(r) for r in baseline],
        window_days=report.window_days, baseline_days=baseline_days,
        min_count=min_count, top_n=top_n,
    )
    report.share_of_voice = share_of_voice(current, previous)
    report.sentiment = sentiment_shift(current, previous)
    report.category_mix = mix(current, previous, "category")
    report.drug_mix = mix(current, previous, "drug")
    report.complaint_themes = complaint_themes(current)
    return report


# ── Language bank ──


def phrase_norm(phrase: str) -> str:
    return " ".join((phrase or "").lower().split())


def _phrases(raw) -> list[tuple[str, str]]:
    try:
        phrases = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        return []
    kept, seen = [], set()
    for phrase in phrases if isinstance(phrases, list) else []:
        if not isinstance(phrase, str):
            continue
        norm = phrase_norm(phrase)
        if norm and len(norm) <= MAX_PHRASE_CHARS and norm not in seen:
            seen.add(norm)
            kept.append((" ".join(phrase.split()), norm))
    return kept


async def bank_language(state) -> int:
    """Bank the verbatim triage phrases of every not-yet-banked relevant
    mention. Returns how many mentions were banked (0 on a rerun)."""
    banked = 0
    for mention in await pulse_store.unbanked_mentions(state):
        if await pulse_store.bank_mention(state, mention, _phrases(mention.get("phrases_json"))):
            banked += 1
    return banked
