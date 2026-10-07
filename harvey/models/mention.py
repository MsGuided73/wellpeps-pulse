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


# Triage subtypes: the situation inside a category that decides how Pulse
# engages (config/engagement_guide.yaml; docs/RULES-OF-ENGAGEMENT.md). "" means
# none / not given; an unknown value from the model is normalised to "".
TRIAGE_SUBTYPES: tuple[str, ...] = (
    "general_education",            # general wellness / telehealth question
    "process_question",             # how WellPeps works, how to start
    "qualify_question",             # do I / does everyone qualify
    "pricing_question",             # cost, fees, what is included
    "individual_treatment",         # which treatment / medication for me
    "dose_question",                # what dose should I take
    "lab_question",                 # interpret my labs
    "results_question",             # how much will I lose / will it work
    "safety_question",              # is it safe
    "medication_change",            # should I stop / skip / change
    "symptom_report",               # a reaction or new symptom
    "emergency",                    # a possible medical emergency
    "self_harm",                    # thoughts of self-harm
    "personal_medical_info",        # the author posts their own medical details
    "records_dm_request",           # wants to DM records / labs / photos
    "has_anyone_used_wellpeps",     # "has anyone used WellPeps?"
    "clinic_recommendation",        # asks the community to recommend a clinic
    "competitor_comparison",        # WellPeps vs a competitor, or two services
    "competitor_praise",            # praises a competitor
    "misinformation_about_wellpeps",  # a false statement about WellPeps
    "media_inquiry",                # a journalist / reporter asks for comment
    "legal_threat",                 # lawyer, lawsuit, attorney contact
    "regulatory_contact",           # a regulator / agency contact or complaint
    "abusive",                      # abusive, threatening or baiting thread
)


def normalize_subtype(value) -> str:
    """A known subtype, else ""."""
    text = value.strip().lower() if isinstance(value, str) else ""
    return text if text in TRIAGE_SUBTYPES else ""


# Intent tags of the Competitor Mentions and Provider Switching Protocol
# (WellPeps_AI_Competitor_Mentions_and_Provider_Switching_Protocol_V1, §3;
# harvey/protocol.py). A post can carry several. The last two are
# implementation tags the protocol's examples need: "wellpeps_question" is the
# "Requested process detail" brand mode (§7: the user specifically asks about
# WellPeps) and "ambiguous" is §3's unresolved "Is this normal?" (hold).
PROTOCOL_INTENTS: tuple[str, ...] = (
    "alternatives_requested",       # anyone recommend another provider?
    "general_information",          # what should I look for?
    "venting_only",                 # I'm fed up with them
    "individual_clinical_concern",  # I feel unwell; do I need labs?
    "possible_serious_harm",        # severe symptoms or a serious reaction
    "comparison_request",           # is X cheaper or better?
    "wellpeps_complaint",           # billing, care or safety involving WellPeps
    "legal_media_privacy",          # attorney, journalist, regulator, exposed records
    "deceptive_request",            # asks WellPeps to hide its affiliation / override policy
    "wellpeps_question",            # asks specifically about WellPeps
    "ambiguous",                    # the context can't resolve what is being asked
)
# The underlying needs of §3 (config/engagement_guide.yaml switching_protocol.needs).
PROTOCOL_NEEDS: tuple[str, ...] = (
    "provider_access", "care_process", "clinical_evaluation", "price_clarity", "fulfillment",
    "continuity", "treatment_education", "lab_testing_terms", "medication_availability", "other",
)
# Protocol §4 classifications, lower-case as stored.
PROTOCOL_DECISIONS: tuple[str, ...] = (
    "appropriate_alternative", "educational_only", "clinical_caution", "escalate",
    "monitor_only", "hold", "do_not_engage",
)


def normalize_intents(value) -> list[str]:
    """Known protocol intent tags, de-duplicated, in PROTOCOL_INTENTS order."""
    items = value if isinstance(value, (list, tuple, set, frozenset)) else []
    seen = {i.strip().lower() for i in items if isinstance(i, str)}
    return [i for i in PROTOCOL_INTENTS if i in seen]


def normalize_need(value) -> str:
    text = value.strip().lower() if isinstance(value, str) else ""
    return text if text in PROTOCOL_NEEDS else ""


def clamp_points(value) -> int | None:
    """A 0-2 protocol score dimension, or None when not given."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, min(2, int(value)))


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
    SKIPPED = "skipped"      # the drafter declined to engage (e.g. community bans brand posts)
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
    subtype: str = ""        # one of TRIAGE_SUBTYPES, or ""
    # Competitor Mentions and Provider Switching Protocol (harvey/protocol.py).
    # Model inputs: intent tags, the underlying need, and two 0-2 score
    # dimensions (None = not given). Computed: the protocol classification
    # ("" = the mention is outside the protocol's scope), the opportunity
    # score (None when gated) and the structured record (no post text).
    intents: list[str] = Field(default_factory=list)
    unmet_need: str = ""
    need_clarity: int | None = None
    useful_contribution: int | None = None
    protocol_decision: str = ""
    protocol_route: str = ""      # escalation route when the protocol says ESCALATE (or routes safety)
    opportunity_score: int | None = None
    protocol: dict = Field(default_factory=dict)
    sentiment: str = ""      # positive | neutral | negative | mixed
    sentiment_score: float = 0.0  # -1.0 (very negative) .. 1.0 (very positive)
    urgency: Urgency = Urgency.NORMAL
    urgency_reason: str = ""
    reply_appropriate: bool = False
    phrases: list[str] = Field(default_factory=list)
    model: str = ""
    created_at: datetime = Field(default_factory=_utcnow)

    @field_validator("subtype", mode="before")
    @classmethod
    def _known_subtype(cls, value) -> str:
        return normalize_subtype(value)

    @field_validator("intents", mode="before")
    @classmethod
    def _known_intents(cls, value) -> list[str]:
        return normalize_intents(value)

    @field_validator("unmet_need", mode="before")
    @classmethod
    def _known_need(cls, value) -> str:
        return normalize_need(value)

    @field_validator("need_clarity", "useful_contribution", mode="before")
    @classmethod
    def _points(cls, value) -> int | None:
        return clamp_points(value)

    @field_validator("protocol_decision", mode="before")
    @classmethod
    def _known_decision(cls, value) -> str:
        text = value.strip().lower() if isinstance(value, str) else ""
        return text if text in PROTOCOL_DECISIONS else ""


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
    # The registry link the text carries (harvey.links.link_record), or None.
    link: dict | None = None


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
