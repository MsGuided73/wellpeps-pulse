"""WellPeps Pulse domain models (pydantic v2).

A Mention is one public post/comment/review that talks about WellPeps, a
competitor, or a product category. Everything downstream (triage, drafts,
escalations, the audit trail) hangs off a mention id.
"""

from datetime import datetime, timezone
from enum import Enum
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


# Upper bounds on stored mention text. Collectors may hand us anything; the
# ingest step truncates before storing (harvey.collectors.base.bound_mention)
# and every regex scan over mention text stops at the text limit.
MAX_MENTION_TEXT_CHARS = 20000
MAX_MENTION_TITLE_CHARS = 500


def _utcnow() -> datetime:
    """Naive UTC now — matches how timestamps are stored in SQLite."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Platform(str, Enum):
    REDDIT = "reddit"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    TIKTOK = "tiktok"
    X = "x"
    YOUTUBE = "youtube"
    TRUSTPILOT = "trustpilot"
    BBB = "bbb"
    GOOGLE_REVIEWS = "google_reviews"
    WEB = "web"
    OTHER = "other"


class MentionStatus(str, Enum):
    NEW = "new"
    TRIAGED = "triaged"
    DRAFTED = "drafted"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    POSTED = "posted"
    DROPPED = "dropped"
    ESCALATED = "escalated"


class Urgency(str, Enum):
    URGENT = "urgent"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class Category(str, Enum):
    COMPLAINT = "complaint"
    QUESTION = "question"
    PURCHASE_INTENT = "purchase_intent"
    PRAISE = "praise"
    MISINFORMATION = "misinformation"
    ADVERSE_EVENT = "adverse_event"
    LEGAL_REGULATORY = "legal_regulatory"
    PRIVACY = "privacy"
    BILLING_FRAUD = "billing_fraud"
    OTHER = "other"


class ReviewVerdict(str, Enum):
    PASS = "pass"
    REJECT = "reject"
    NEEDS_HUMAN = "needs_human"


class AuditEventType(str, Enum):
    COLLECTED = "collected"
    TRIAGED = "triaged"
    DRAFTED = "drafted"
    FILTERED = "filtered"
    REVIEWED = "reviewed"
    EDITED = "edited"
    APPROVED = "approved"
    REJECTED = "rejected"
    POSTED = "posted"
    COPIED = "copied"
    ESCALATED = "escalated"
    ACKED = "acked"


class Mention(BaseModel):
    id: int | None = None
    source_id: int | None = None
    platform: Platform
    external_id: str = ""
    url: str
    url_norm: str = ""  # computed by the state layer on insert
    author_handle: str = ""
    parent_external_id: str = ""
    text: str = ""
    title: str = ""
    lang: str = ""
    posted_at: datetime | None = None
    collected_at: datetime = Field(default_factory=_utcnow)
    engagement: dict = Field(default_factory=dict)
    owned_channel: bool = False
    status: MentionStatus = MentionStatus.NEW
    run_id: str = ""

    @field_validator("url")
    @classmethod
    def _url_required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("every mention needs its permalink (url)")
        if urlsplit(v).scheme.lower() not in ("http", "https"):
            raise ValueError("permalink must be an http(s) URL")
        return v


class Triage(BaseModel):
    mention_id: int
    relevant: bool = True
    subject_type: str = ""   # wellpeps | competitor | product | category | none
    subject: str = ""
    competitor: str = ""     # canonical competitor name, "" when none/unknown
    # Canonical WellPeps product (SKU) name. Set only when the mention is
    # about WellPeps (subject_type "wellpeps" or the text names WellPeps);
    # a post about semaglutide in general is not about a WellPeps SKU.
    product: str = ""
    drug: str = ""           # generic/category drug discussed, e.g. "semaglutide", "BPC-157"
    category: Category = Category.OTHER
    sentiment: str = ""      # positive | neutral | negative | mixed
    sentiment_score: float = 0.0  # -1.0 (very negative) .. 1.0 (very positive)
    urgency: Urgency = Urgency.NORMAL
    urgency_reason: str = ""
    reply_appropriate: bool = False
    phrases: list[str] = Field(default_factory=list)
    model: str = ""
    created_at: datetime = Field(default_factory=_utcnow)


class Draft(BaseModel):
    id: int | None = None
    mention_id: int
    version: int = 0  # assigned by the state layer (1, 2, ... per mention)
    text: str
    claim_ids: list[str] = Field(default_factory=list)
    model: str = ""
    filter_ok: bool | None = None
    filter_hits: list[str] = Field(default_factory=list)
    review_verdict: ReviewVerdict | None = None
    review_reasons: list[str] = Field(default_factory=list)
    tier: str = ""
    created_at: datetime = Field(default_factory=_utcnow)


class Escalation(BaseModel):
    id: int | None = None
    mention_id: int
    kind: str = ""           # usually a Category value, e.g. adverse_event
    owner: str = ""
    notified_at: datetime | None = None
    sla_due_at: datetime | None = None
    acked_at: datetime | None = None
    acked_by: str = ""
    breached: bool = False
    created_at: datetime = Field(default_factory=_utcnow)


class AuditEvent(BaseModel):
    id: int | None = None
    mention_id: int | None = None
    draft_id: int | None = None
    event: AuditEventType
    actor: str               # "system", an agent name, or a user email
    claim_ids: list[str] = Field(default_factory=list)
    filter_result: dict = Field(default_factory=dict)
    verdict: dict = Field(default_factory=dict)
    final_text: str = ""
    permalink: str = ""
    at: datetime = Field(default_factory=_utcnow)


class Brief(BaseModel):
    """A daily/weekly Pulse brief (harvey/briefs.py); one per (period, window_start)."""

    id: int | None = None
    period: str = "daily"    # daily | weekly
    window_start: datetime | None = None
    window_end: datetime | None = None
    status: str = "ok"       # ok | fallback (tables only)
    headline: str = ""
    summary_md: str = ""
    action_cards: list[dict] = Field(default_factory=list)
    watchlist: list[str] = Field(default_factory=list)
    data: dict = Field(default_factory=dict)
    model: str = ""
    slack_sent_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class User(BaseModel):
    id: int | None = None
    email: str
    display_name: str = ""
    role: str = "reviewer"   # admin | reviewer | viewer
    password_hash: str = Field(default="", repr=False)
    active: bool = True
    created_at: datetime = Field(default_factory=_utcnow)
