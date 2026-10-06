"""Build the DEMO sandbox around the demo mentions (used by scripts/seed_demo.py).

Every demo mention becomes a post or a comment inside a sandbox thread, with
fictional context around it, and gets that sandbox permalink as its ``url``:

- fixture rows: hand-written threads in ``demo_content.FIXTURE_THREADS``
  (the scenario thread included); unknown rows become a quick-takes post;
- synthetic posts: comments in weekly check-in threads per community (forum),
  comments on a fictional account's promo post per week (photos), or one
  review each (reviews); urgent-history posts get their own threads.

A duplicate fixture row (same permalink plus tracking parameters) maps to the
same sandbox permalink plus the same tracking parameters, so ``url_norm``
dedupe still collapses it. Deterministic for a given seed.
"""

import random
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit

from harvey.sandbox import demo_content as content
from harvey.sandbox import urls
from harvey.sandbox.store import SandboxStore
from harvey.urls import _is_tracking, normalize_url

PLATFORM_KIND = {
    "reddit": "forum", "x": "forum", "facebook": "forum", "web": "forum", "other": "forum",
    "youtube": "photo", "instagram": "photo", "tiktok": "photo",
    "trustpilot": "review", "bbb": "review", "google_reviews": "review",
}
FORUM_TOPICS = [  # (pattern, community) for synthetic forum posts
    (r"minoxidil|shedding|hair", "hairloss_help"),
    (r"tadalafil", "menshealth_q"),
    (r"tirzepatide|\btirz\b|mounjaro", "tirzepatide_talk"),
    (r"NAD\+|peptide", "peptide_questions"),
    (r"charged|billing|price|refund", "telehealth_reviews"),
]
BILLING_RE = re.compile(r"fraud|lawyer|charge|refund|billing|cancel", re.I)
NEGATIVE_RE = re.compile(r"delay|stuck|twice|never|backorder|plateau|rough|hike|shortage|took", re.I)
POSITIVE_RE = re.compile(r"quiet|best part|on time|clear update|adjusted|works fine|better", re.I)
MAX_FILLERS = 8


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _naive(value) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is None else \
            value.astimezone(timezone.utc).replace(tzinfo=None)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def _handle(raw: str) -> str:
    raw = (raw or "").strip()
    for prefix in ("u/", "/u/", "@"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
    return raw or "anonymous_member"


def _slug(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9_]", "_", _handle(raw).lower()).strip("_")
    return (slug or "demo_account")[:40].ljust(2, "_")


def _title(text: str, limit: int = 90) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _with_tracking(path: str, original_url: str) -> str:
    """``path`` (maybe with #fragment) plus the tracking params of ``original_url``."""
    path, _, fragment = path.partition("#")
    tracking = [(k, v) for k, v in parse_qsl(urlsplit(original_url).query, keep_blank_values=True)
                if _is_tracking(k)]
    return path + (f"?{urlencode(tracking)}" if tracking else "") + (f"#{fragment}" if fragment else "")


class SandboxSeeder:
    """Writes threads into ``store`` and hands back each mention's permalink."""

    def __init__(self, store: SandboxStore, base_url: str, seed: int = 5):
        self.store = store
        self.base = base_url.rstrip("/")
        self.rng = random.Random(seed)
        self._communities: set[str] = set()

    # --- low level ---------------------------------------------------------------

    def _community(self, name: str, kind: str, title: str = "", description: str = "") -> None:
        if name in self._communities:
            return
        if name in content.COMMUNITIES:
            kind, title, description = content.COMMUNITIES[name]
        self.store.add_community(name, kind, title or f"@{name}", description)
        self._communities.add(name)

    def _thread(self, kind: str, community: str, *, title: str, body: str, author: str, at: datetime,
                points: int, flair: str = "", rating: int | None = None, video: bool = False) -> int:
        self._community(community, kind)
        return self.store.add_thread(kind=kind, community=community, title=title, body=body, author=author,
                                     created_at=_iso(at), points=points, image_seed=self.rng.randint(1, 10**6),
                                     flair=("Video" if video else flair), rating=rating)

    def _filler(self, pool: str) -> str:
        return self.rng.choice(content.FILLERS[pool])

    def _who(self) -> str:
        return self.rng.choice(content.FILLER_HANDLES)

    # --- fixture rows ------------------------------------------------------------

    def place_fixture(self, rows: list[dict]) -> list[dict]:
        """``rows`` with every http(s) permalink rewritten to its sandbox permalink."""
        placed: dict[str, str] = {}
        out = []
        for raw in rows:
            url = str(raw.get("url") or "")
            if urlsplit(url).scheme.lower() not in ("http", "https"):
                out.append(dict(raw))  # the collector skips it, as before
                continue
            key = normalize_url(url)
            if key not in placed:
                spec = content.FIXTURE_THREADS.get(str(raw.get("external_id") or ""))
                placed[key] = self._fixture_thread(raw, spec or self._fallback_spec())
            out.append({**raw, "url": self.base + _with_tracking(placed[key], url)})
        return out

    @staticmethod
    def _fallback_spec() -> dict:
        return {"kind": "forum", "community": "quick_takes", "role": "op", "comments": [
            ("lurker_lin", "Interesting, following.", 2, None), ("teapot_tom", "Anyone else seen this?", 1, None)]}

    def _fixture_thread(self, raw: dict, spec: dict) -> str:
        at = _naive(raw.get("posted_at") or datetime.now(timezone.utc))
        text = str(raw.get("text") or "").strip()
        title = str(raw.get("title") or "").strip()
        author = _handle(str(raw.get("author_handle") or ""))
        kind = spec["kind"]
        community = spec.get("community") or (spec.get("account") if kind == "photo" else "") or _slug(author)
        comments = spec["comments"]
        gap = timedelta(minutes=self.rng.randint(20, 90))
        if spec["role"] == "op":
            thread_id = self._thread(kind, community, title=title or ("" if kind == "photo" else _title(text)),
                                     body=text, author=author, at=at, points=spec.get("points", 12),
                                     flair=spec.get("flair", ""), rating=spec.get("rating"),
                                     video=spec.get("video", False))
            self._comments(thread_id, comments, at, gap, None)
            return urls.thread_path(kind, community, thread_id)
        index = next(i for i, c in enumerate(comments) if c[0] == content.MENTION)
        start = at - gap * (index + 1)
        if kind == "photo":
            op_author, op_body, op_points = community, content.PHOTO_ACCOUNTS.get(community, ""), 300
            op_title = ""
        else:
            op_author, op_body, op_points = spec["op"]
            op_title = spec["title"]
        thread_id = self._thread(kind, community, title=op_title, body=op_body, author=op_author, at=start,
                                 points=op_points, flair=spec.get("flair", ""))
        ids = self._comments(thread_id, comments, start, gap, (author, text))
        return urls.comment_path(kind, community, thread_id, ids[index])

    def _comments(self, thread_id: int, comments, start: datetime, gap: timedelta, mention) -> list[int]:
        ids: list[int] = []
        for i, (who, body, points, parent) in enumerate(comments):
            if who == content.MENTION:
                who, body = mention
            parent_id = ids[parent] if parent is not None else None
            ids.append(self.store.add_comment(thread_id, parent_id, who, body, _iso(start + gap * (i + 1)), points))
        return ids

    # --- synthetic mentions ------------------------------------------------------

    def place_synthetic(self, mentions: list) -> dict[str, str]:
        """external_id -> absolute sandbox permalink for each synthetic mention."""
        placed: dict[str, str] = {}
        groups: dict[tuple, list] = defaultdict(list)
        for m in sorted(mentions, key=lambda m: m.posted_at or m.collected_at):
            kind = PLATFORM_KIND.get(m.platform.value, "forum")
            if m.external_id.startswith("demo-urg-"):
                placed[m.external_id] = self._urgent_thread(m)
            elif kind == "review":
                placed[m.external_id] = self._review_thread(m)
            else:
                week = self._week(m)
                where = self._forum_community(m.text) if kind == "forum" else self._account(week)
                groups[(kind, where, week)].append(m)
        for (kind, where, week), members in sorted(groups.items(), key=lambda kv: kv[0][2]):
            placed.update(self._weekly_thread(kind, where, week, members))
        return {key: self.base + path for key, path in placed.items()}

    @staticmethod
    def _when(m) -> datetime:
        return m.posted_at or m.collected_at

    def _week(self, m) -> datetime:
        day = self._when(m)
        return datetime(day.year, day.month, day.day) - timedelta(days=day.weekday())

    @staticmethod
    def _forum_community(text: str) -> str:
        return next((name for pattern, name in FORUM_TOPICS if re.search(pattern, text, re.I)), "glp1_journey")

    @staticmethod
    def _account(week: datetime) -> str:
        accounts = ("slimscript_rx", "glowpath_health")
        return accounts[week.toordinal() // 7 % len(accounts)]

    def _urgent_thread(self, m) -> str:
        text = m.text
        title = _title(re.sub(r"^DEMO:\s*", "", text), 80)
        thread_id = self._thread("forum", "glp1_journey", title=title, body=text, author=_handle(m.author_handle),
                                 at=self._when(m), points=self.rng.randint(8, 70), flair="Help")
        pool = "urgent_billing" if BILLING_RE.search(text) else "urgent_health"
        picks = self.rng.sample(content.FILLERS[pool], k=self.rng.randint(2, 4))
        at = self._when(m)
        for i, body in enumerate(picks):
            self.store.add_comment(thread_id, None, self._who(), body,
                                   _iso(at + timedelta(minutes=12 * (i + 1))), self.rng.randint(1, 30))
        return urls.thread_path("forum", "glp1_journey", thread_id)

    def _review_thread(self, m) -> str:
        community = "wellpeps" if "wellpeps" in m.text.lower() else "telehealth_providers"
        rating = 2 if NEGATIVE_RE.search(m.text) else 5 if POSITIVE_RE.search(m.text) else 3
        at = self._when(m)
        thread_id = self._thread("review", community, title=_title(m.text, 48), body=m.text,
                                 author=_handle(m.author_handle), at=at, points=self.rng.randint(0, 9),
                                 rating=rating)
        for i in range(2):
            self.store.add_comment(thread_id, None, self._who(), self._filler("review"),
                                   _iso(at + timedelta(hours=3 * (i + 1))), self.rng.randint(0, 5))
        return urls.thread_path("review", community, thread_id)

    def _weekly_thread(self, kind: str, where: str, week: datetime, members: list) -> dict[str, str]:
        first = min(self._when(m) for m in members)
        opened = min(week + timedelta(hours=8), first - timedelta(minutes=30))
        if kind == "photo":
            self._community(where, "photo", f"@{where}", "Fictional account (demo).")
            thread_id = self._thread("photo", where, title="", body=content.PHOTO_ACCOUNTS[where], author=where,
                                     at=opened, points=self.rng.randint(150, 900))
            pool = "photo"
        else:
            op_author, op_body = content.WEEKLY_OP
            thread_id = self._thread("forum", where, title=f"Weekly check-in thread — week of {week:%b %d}",
                                     body=op_body, author=op_author, at=opened, points=self.rng.randint(5, 60),
                                     flair="Weekly")
            pool = "forum"
        placed, budget = {}, MAX_FILLERS
        for k in range(min(2, budget)):  # early top-level context
            self.store.add_comment(thread_id, None, self._who(), self._filler(pool),
                                   _iso(opened + timedelta(minutes=20 * (k + 1))), self.rng.randint(1, 25))
            budget -= 1
        for m in members:
            at = self._when(m)
            cid = self.store.add_comment(thread_id, None, _handle(m.author_handle), m.text, _iso(at),
                                         self.rng.randint(1, 60))
            placed[m.external_id] = urls.comment_path(kind, where, thread_id, cid)
            if budget and self.rng.random() < 0.25:
                self.store.add_comment(thread_id, cid, self._who(), self._filler("reply"),
                                       _iso(at + timedelta(minutes=self.rng.randint(10, 300))),
                                       self.rng.randint(1, 12))
                budget -= 1
        return placed
