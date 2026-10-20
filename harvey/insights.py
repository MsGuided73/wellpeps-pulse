"""Pain points & emerging ICPs: a deterministic roll-up of triaged mentions.

No Claude calls. For a window it answers "what are people struggling with,
who is it about, and which kinds of customers are showing up?" from what
triage already stored per mention: competitor, category, sentiment, the
protocol inputs (intents, unmet need) and the verbatim triage phrases. Rules
live in ``config/insights.yaml`` so the team can edit them.

- **Community attribution** (``attribute_competitor``): when triage left the
  competitor empty but the mention's community (the Reddit sub in
  ``engagement_json`` or the permalink) is named like a competitor or one of
  its aliases (case, spaces, punctuation and accents ignored), the mention is
  attributed to that competitor with source "community". Nothing is written
  back: stored triage rows are untouched.
- **Pain points**: mentions grouped by the unmet need (else, for a complaint
  with no need, by category). Ranked by mentions with complaint-like ones
  counted twice. Each has up to two verbatim triage phrases (<= 120 chars, no
  links/handles/e-mails) with mention id and permalink.
- **Competitor complaints**: per competitor, named or community-attributed.
- **Segments (emerging ICPs)**: configurable AND/OR rules over intents, needs,
  categories, keywords, "a competitor is named" and "reads as a complaint".
  Each segment is compared with the previous window of equal length: new,
  growing, steady or declining.
- Privacy: nothing below ``min_count`` mentions is listed on its own (it is
  only counted under ``suppressed``). ``model_payload`` is the only part a
  prompt may see: counts and labels, never quotes, links or mention ids.
"""

import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from functools import lru_cache

from harvey import communities, knowledge, pulse_store, trends
from harvey.models.knowledge import InsightsFile, Segment, SegmentClause

SOURCE_TRIAGE = "triage"
SOURCE_COMMUNITY = "community"
NEW, GROWING, STEADY, DECLINING = "new", "growing", "steady", "declining"
EMERGING_STATUSES = (NEW, GROWING)
MIN_QUOTE_WORDS = 3
MAX_TEXT_CHARS = trends.MAX_TEXT_CHARS
MODEL_COMPETITORS = 3
_APOSTROPHE = re.compile(r"['’‘`]")
_NON_ALNUM = re.compile(r"[^a-z0-9]")


# ── Community → competitor attribution ──


def normalize_name(text: str) -> str:
    """Lowercase letters and digits only, accents folded: "Ivím Health" and
    "IVIMHEALTH" both become "ivimhealth"."""
    folded = unicodedata.normalize("NFKD", text or "")
    return _NON_ALNUM.sub("", "".join(c for c in folded if not unicodedata.combining(c)).lower())


def community_index(cfg: InsightsFile | None = None) -> dict[str, str]:
    """Normalised community name -> competitor, from names and aliases.

    Skipped: keys shorter than ``community_min_chars``, keys listed in
    ``community_ambiguous`` (everyday words), and keys two competitors claim.
    """
    cfg = cfg or knowledge.insights()
    skip = {normalize_name(word) for word in cfg.community_ambiguous}
    owners: dict[str, set[str]] = defaultdict(set)
    for comp in knowledge.competitors().all():
        for term in (comp.name, *comp.aliases):
            key = normalize_name(term)
            if len(key) >= cfg.community_min_chars and key not in skip:
                owners[key].add(comp.name)
    return {key: next(iter(names)) for key, names in owners.items() if len(names) == 1}


def _loads(raw, default):
    try:
        parsed = json.loads(raw) if isinstance(raw, (str, bytes)) and raw else raw
    except (json.JSONDecodeError, TypeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def community_of(row: dict) -> str:
    """The community name (subreddit / group) a mention lives in, or ""."""
    name = _loads(row.get("engagement_json"), {}).get("community")
    if isinstance(name, str) and name.strip():
        return name.strip()
    cid = communities.community_id(row.get("url") or "")
    return cid.rsplit("/", 1)[-1] if cid else ""


def attribute_competitor(row: dict, index: dict[str, str] | None = None) -> tuple[str, str]:
    """(competitor, source) for a mention row: triage's own answer first
    (source "triage"), else the community's competitor (source "community"),
    else ("", ""). Pure; the stored triage row is not changed."""
    named = (row.get("competitor") or "").strip()
    if named:
        return named, SOURCE_TRIAGE
    key = normalize_name(community_of(row))
    found = (community_index() if index is None else index).get(key) if key else None
    return (found, SOURCE_COMMUNITY) if found else ("", "")


# ── Per-mention view ──


@dataclass(frozen=True)
class Item:
    id: int
    url: str
    at: datetime | None
    competitor: str
    source: str
    category: str
    sentiment_score: float
    need: str
    intents: frozenset[str]
    complaint: bool
    haystack: str
    phrases: tuple[tuple[str, str], ...]   # (verbatim, normalised)
    segments: tuple[str, ...] = field(default=())


def is_complaint_like(category: str, sentiment: str, score: float, intents, rule) -> bool:
    """Negative sentiment, a complaint-type category, or a complaint intent."""
    return (category in rule.categories or (sentiment or "").lower() in rule.sentiments
            or float(score or 0.0) <= rule.sentiment_max or bool(set(intents) & set(rule.intents)))


def _phrase_list(raw) -> tuple[tuple[str, str], ...]:
    seen, out = set(), []
    for phrase in _loads(raw, []):
        if not isinstance(phrase, str):
            continue
        norm = trends.phrase_norm(phrase)
        if norm and norm not in seen:
            seen.add(norm)
            out.append((" ".join(phrase.split()), norm))
    return tuple(out)


def _haystack(row: dict, phrases) -> str:
    text = f"{row.get('title') or ''}\n{(row.get('text') or '')[:MAX_TEXT_CHARS]}\n" + "\n".join(p for p, _ in phrases)
    return _APOSTROPHE.sub("", text.lower())


def _protocol_fields(row: dict) -> tuple[frozenset[str], str]:
    blob = _loads(row.get("protocol_json"), {})
    record, inputs = blob.get("record"), blob.get("inputs")
    record = record if isinstance(record, dict) else {}
    inputs = inputs if isinstance(inputs, dict) else {}
    intents = record.get("intents") or inputs.get("intents") or []
    need = record.get("unmet_need") or inputs.get("unmet_need") or ""
    return (frozenset(i for i in intents if isinstance(i, str)), need if isinstance(need, str) else "")


def to_item(row: dict, cfg: InsightsFile, index: dict[str, str]) -> Item:
    intents, need = _protocol_fields(row)
    competitor, source = attribute_competitor(row, index)
    score = float(row.get("sentiment_score") or 0.0)
    category = row.get("category") or ""
    phrases = _phrase_list(row.get("phrases_json"))
    return Item(
        id=int(row["id"]), url=row.get("url") or "", at=trends.parse_at(row.get("at")),
        competitor=competitor, source=source, category=category, sentiment_score=score, need=need,
        intents=intents, complaint=is_complaint_like(category, row.get("sentiment") or "", score, intents,
                                                     cfg.complaint),
        haystack=_haystack(row, phrases), phrases=phrases,
    )


# ── Segment rules ──


@lru_cache(maxsize=256)
def _keyword_rx(keywords: tuple[str, ...]) -> re.Pattern[str] | None:
    words = sorted({_APOSTROPHE.sub("", k.lower()).strip() for k in keywords if k.strip()}, key=len, reverse=True)
    if not words:
        return None
    alternation = "|".join(r"\s+".join(re.escape(part) for part in word.split()) for word in words)
    return re.compile(rf"(?<!\w)(?:{alternation})(?!\w)")


def _clause_matches(clause: SegmentClause, item: Item) -> bool:
    if clause.intents and item.intents & set(clause.intents):
        return True
    if (clause.needs and item.need in clause.needs) or (clause.categories and item.category in clause.categories):
        return True
    if (clause.competitor and item.competitor) or (clause.complaint_like and item.complaint):
        return True
    rx = _keyword_rx(tuple(clause.keywords))
    return bool(rx and rx.search(item.haystack))


def in_segment(segment: Segment, item: Item) -> bool:
    """Every clause matches (AND); a clause matches on any of its conditions (OR)."""
    return all(_clause_matches(clause, item) for clause in segment.all_of)


def tag_segments(items: list[Item], cfg: InsightsFile) -> list[Item]:
    return [replace(item, segments=tuple(s.id for s in cfg.segments if in_segment(s, item))) for item in items]


def _segment_keywords(segment: Segment) -> re.Pattern[str] | None:
    return _keyword_rx(tuple(k for clause in segment.all_of for k in clause.keywords))


# ── Quotes ──


def quotable(phrase: str, max_chars: int) -> bool:
    """Short, whole-sentence-ish public text with nothing that points at a person."""
    return (len(phrase) <= max_chars and len(phrase.split()) >= MIN_QUOTE_WORDS
            and not trends.has_identifier(phrase))


def pick_examples(items: list[Item], bank: dict[str, int], cfg: InsightsFile,
                  prefer: re.Pattern[str] | None = None, limit: int | None = None) -> list[dict]:
    """Up to ``limit`` verbatim triage phrases, at most one per mention.

    Order: phrases matching ``prefer`` (a segment's keywords), phrases from
    complaint-like mentions, the language bank's use count, the most negative
    mention, then the longest quote that fits (more context) / alphabetical so the
    result is deterministic.
    """
    limit = cfg.examples_per_item if limit is None else limit
    candidates = []
    for item in items:
        for phrase, norm in item.phrases:
            if not quotable(phrase, cfg.max_quote_chars):
                continue
            hit = bool(prefer and prefer.search(_APOSTROPHE.sub("", norm)))
            candidates.append(((not hit, not item.complaint, -bank.get(norm, 0), item.sentiment_score,
                                -len(phrase), norm, item.id), phrase, item))
    candidates.sort(key=lambda c: c[0])
    picked, used_mentions, used_norms = [], set(), set()
    for _, phrase, item in candidates:
        norm = trends.phrase_norm(phrase)
        if len(picked) >= limit:
            break
        if item.id in used_mentions or norm in used_norms:
            continue
        used_mentions.add(item.id)
        used_norms.add(norm)
        picked.append({"quote": phrase, "mention_id": item.id, "url": item.url, "competitor": item.competitor})
    return picked


# ── Roll-up ──


def _status(count: int, prev: int, cfg: InsightsFile) -> str:
    if prev == 0:
        return NEW
    if count >= prev * cfg.growth_ratio:
        return GROWING
    if count * cfg.growth_ratio <= prev:
        return DECLINING
    return STEADY


def _competitor_counts(items: list[Item], top: int = 5) -> list[dict]:
    counts = Counter(i.competitor for i in items if i.competitor)
    return [{"name": name, "count": n} for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]]


def pain_key(item: Item, cfg: InsightsFile) -> str:
    """The pain-point id for a mention, "" when it states no pain: its unmet
    need, else (for a complaint-like mention) ``category:<category>``."""
    if item.need and item.need != "other":
        return item.need
    if not item.complaint:
        return ""
    cat = item.category if item.category in cfg.category_pain else "unclassified"
    return f"category:{cat}"


def pain_label(key: str, cfg: InsightsFile) -> str:
    if key.startswith("category:"):
        cat = key.split(":", 1)[1]
        return cfg.category_pain.get(cat) or cat.replace("_", " ").capitalize()
    try:
        from harvey import protocol

        spec = protocol.config().needs.get(key)
        if spec is not None and spec.label:
            return spec.label
    except Exception:  # engagement guide unavailable: fall back to the id
        pass
    return key.replace("_", " ").capitalize()


def _pain_counts(items: list[Item], cfg: InsightsFile) -> Counter:
    return Counter(k for k in (pain_key(i, cfg) for i in items) if k)


def pain_points(current: list[Item], previous: list[Item], bank: dict[str, int], cfg: InsightsFile,
                min_count: int) -> tuple[list[dict], int]:
    groups: dict[str, list[Item]] = defaultdict(list)
    for item in current:
        key = pain_key(item, cfg)
        if key:
            groups[key].append(item)
    before = _pain_counts(previous, cfg)
    rows, hidden = [], 0
    for key, items in groups.items():
        if len(items) < min_count:
            hidden += 1
            continue
        complaints = sum(i.complaint for i in items)
        rows.append({
            "id": key, "label": pain_label(key, cfg), "kind": "category" if ":" in key else "need",
            "count": len(items), "complaint_count": complaints, "score": len(items) + complaints,
            "prev_count": before[key], "delta": len(items) - before[key],
            "status": _status(len(items), before[key], cfg),
            "competitors": _competitor_counts(items, MODEL_COMPETITORS),
            "examples": pick_examples(items, bank, cfg),
        })
    rows.sort(key=lambda r: (-r["score"], -r["complaint_count"], r["label"]))
    return rows[:cfg.top_pain_points], hidden


def competitor_complaints(current: list[Item], previous: list[Item], bank: dict[str, int], cfg: InsightsFile,
                          min_count: int) -> tuple[list[dict], int]:
    by_comp: dict[str, list[Item]] = defaultdict(list)
    for item in current:
        if item.competitor:
            by_comp[item.competitor].append(item)
    before = Counter(i.competitor for i in previous if i.competitor and i.complaint)
    rows, hidden = [], 0
    for name, items in by_comp.items():
        complaints = [i for i in items if i.complaint]
        if not complaints:
            continue
        if len(complaints) < min_count:
            hidden += 1
            continue
        needs = _pain_counts(complaints, cfg)
        top_need = sorted(needs.items(), key=lambda kv: (-kv[1], kv[0]))[0] if needs else None
        worst = min(complaints, key=lambda i: (i.sentiment_score, i.id))
        example = (pick_examples([worst], bank, cfg, limit=1) or pick_examples(complaints, bank, cfg, limit=1))
        rows.append({
            "competitor": name, "count": len(complaints), "mentions": len(items),
            "prev_count": before[name], "delta": len(complaints) - before[name],
            "sources": dict(Counter(i.source for i in items)),
            "main_pain": ({"id": top_need[0], "label": pain_label(top_need[0], cfg), "count": top_need[1]}
                          if top_need else None),
            "worst_sentiment": round(worst.sentiment_score, 3),
            "example": example[0] if example else None,
        })
    rows.sort(key=lambda r: (-r["count"], -r["mentions"], r["competitor"]))
    return rows[:cfg.top_competitors], hidden


def segments(current: list[Item], previous: list[Item], bank: dict[str, int], cfg: InsightsFile,
             min_count: int) -> tuple[list[dict], int]:
    rows, hidden = [], 0
    for seg in cfg.segments:
        mine = [i for i in current if seg.id in i.segments]
        prev = sum(1 for i in previous if seg.id in i.segments)
        if not mine:
            continue
        if len(mine) < min_count:
            hidden += 1
            continue
        status = _status(len(mine), prev, cfg)
        rows.append({
            "id": seg.id, "label": seg.label, "description": seg.description, "count": len(mine),
            "prev_count": prev, "delta": len(mine) - prev, "status": status,
            "emerging": status in EMERGING_STATUSES,
            "competitors": _competitor_counts(mine, MODEL_COMPETITORS),
            "examples": pick_examples(mine, bank, cfg, prefer=_segment_keywords(seg)),
        })
    rows.sort(key=lambda r: (not r["emerging"], -r["count"], r["label"]))
    return rows[:cfg.top_segments], hidden


def build_rollup(current_rows: list[dict], previous_rows: list[dict], bank: dict[str, int] | None = None, *,
                 start: datetime, end: datetime, min_count: int | None = None,
                 cfg: InsightsFile | None = None) -> dict:
    """The whole roll-up for [start, end) from mention rows
    (``pulse_store.fetch_insight_rows``); ``previous_rows`` is the window of
    equal length just before. Pure and deterministic."""
    cfg = cfg or knowledge.insights()
    floor = cfg.min_count if min_count is None else max(int(min_count), 1)
    index = community_index(cfg)
    cur = tag_segments([to_item(r, cfg, index) for r in current_rows], cfg)
    prev = tag_segments([to_item(r, cfg, index) for r in previous_rows], cfg)
    bank = bank or {}
    pains, hidden_pains = pain_points(cur, prev, bank, cfg, floor)
    comps, hidden_comps = competitor_complaints(cur, prev, bank, cfg, floor)
    segs, hidden_segs = segments(cur, prev, bank, cfg, floor)
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat(),
                   "days": round((end - start).total_seconds() / 86400, 3)},
        "min_count": floor,
        "mentions": len(cur), "previous_mentions": len(prev),
        "complaint_like_mentions": sum(i.complaint for i in cur),
        "competitor_mentions": sum(1 for i in cur if i.competitor),
        "community_attributed": sum(1 for i in cur if i.source == SOURCE_COMMUNITY),
        "pain_points": pains, "competitor_complaints": comps, "segments": segs,
        "suppressed": {"pain_points": hidden_pains, "competitor_complaints": hidden_comps,
                       "segments": hidden_segs},
    }


async def compute_insights(state, start: datetime, end: datetime, *, min_count: int | None = None) -> dict:
    """The roll-up for [start, end) (naive UTC) against the window before it."""
    if end <= start:
        raise ValueError("end must be after start")
    prev_start = start - (end - start)
    rows = await pulse_store.fetch_insight_rows(state, prev_start, end)
    current, previous = [], []
    for row in rows:
        at = trends.parse_at(row.get("at"))
        if at is None:
            continue
        (current if at >= start else previous).append(row)
    return build_rollup(current, previous, await pulse_store.phrase_counts(state),
                        start=start, end=end, min_count=min_count)


# ── Output for the model, Slack and markdown ──


def model_payload(rollup: dict) -> dict:
    """What a prompt may see: counts and labels. Never quotes, links or ids."""
    return {
        "min_count": rollup["min_count"],
        "mentions_considered": rollup["mentions"],
        "complaint_like_mentions": rollup["complaint_like_mentions"],
        "pain_points": [{"label": p["label"], "count": p["count"], "complaint_count": p["complaint_count"],
                         "prev_count": p["prev_count"], "status": p["status"],
                         "competitors": [c["name"] for c in p["competitors"]]} for p in rollup["pain_points"]],
        "competitor_complaints": [{"competitor": c["competitor"], "count": c["count"], "prev_count": c["prev_count"],
                                   "main_pain": (c["main_pain"] or {}).get("label", ""),
                                   "worst_sentiment": c["worst_sentiment"]}
                                  for c in rollup["competitor_complaints"]],
        "segments": [{"label": s["label"], "count": s["count"], "prev_count": s["prev_count"],
                      "status": s["status"]} for s in rollup["segments"]],
    }


def has_content(rollup: dict | None) -> bool:
    return bool(rollup and (rollup.get("pain_points") or rollup.get("competitor_complaints")
                            or rollup.get("segments")))


def slack_lines(rollup: dict | None, top: int = 2) -> list[str]:
    """Up to two lines of labels and counts for the Slack brief (none when empty)."""
    if not has_content(rollup):
        return []
    lines = []
    if rollup["pain_points"]:
        lines.append("Top pain points: " + ", ".join(f"{p['label']} ({p['count']})"
                                                     for p in rollup["pain_points"][:top]))
    emerging = [s for s in rollup["segments"] if s["emerging"]]
    if emerging:
        lines.append("Emerging ICPs: " + ", ".join(f"{s['label']} ({s['status']})" for s in emerging[:top]))
    return lines


def _example_md(example: dict | None) -> str:
    if not example:
        return ""
    who = f" [{example['competitor']}]" if example.get("competitor") else ""
    return f"\"{example['quote']}\"{who} ({example['url']})" if example.get("url") else f"\"{example['quote']}\"{who}"


def render_markdown(rollup: dict | None, note: str = "") -> str:
    """The "Pain points & emerging ICPs" section as markdown (CLI and exports)."""
    if not has_content(rollup):
        return ""
    lines = ["### Pain points & emerging ICPs", ""]
    if note:
        lines += [note, ""]
    if rollup["pain_points"]:
        lines.append("**Top pain points**")
        for p in rollup["pain_points"]:
            comps = ", ".join(c["name"] for c in p["competitors"])
            lines.append(f"- {p['label']}: {p['count']} mention(s), {p['complaint_count']} complaint-like "
                         f"(previous {p['prev_count']}, {p['status']})" + (f"; {comps}" if comps else ""))
            lines += [f"  - {_example_md(e)}" for e in p["examples"]]
    if rollup["competitor_complaints"]:
        lines += ["", "**Competitor complaints**"]
        for c in rollup["competitor_complaints"]:
            pain = (c["main_pain"] or {}).get("label", "")
            lines.append(f"- {c['competitor']}: {c['count']} complaint(s)" + (f", mainly {pain}" if pain else "")
                         + f", worst sentiment {c['worst_sentiment']}")
            if c["example"]:
                lines.append(f"  - {_example_md(c['example'])}")
    if rollup["segments"]:
        lines += ["", "**Emerging ICP signals**"]
        for s in rollup["segments"]:
            lines.append(f"- {s['label']}: {s['count']} mention(s) (previous {s['prev_count']}, {s['status']})")
            lines += [f"  - {_example_md(e)}" for e in s["examples"]]
    return "\n".join(lines)


NOTE_MAX_SENTENCES = 2
NOTE_MAX_CHARS = 400
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def clean_note(note: str, allowed_numbers: set[float], numbers_in) -> str:
    """The model's interpretation: at most two sentences, no links or handles,
    and no number that is not in the data (else the whole note is dropped)."""
    text = " ".join((note or "").split())
    if not text or trends.has_identifier(text):
        return ""
    if numbers_in(text) - allowed_numbers:
        return ""
    return " ".join(_SENTENCE_END.split(text)[:NOTE_MAX_SENTENCES])[:NOTE_MAX_CHARS]
