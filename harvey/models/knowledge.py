"""Typed shapes for the knowledge YAML in ``config/`` (pydantic v2).

These mirror the files one-to-one. ``extra="forbid"`` makes an unexpected
field a load error: config that feeds agents must not quietly grow new
fields (for example, internal cost columns).
"""

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator


def _check_regex(pattern: str) -> str:
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"invalid regex {pattern!r}: {exc}") from exc
    return pattern


Regex = Annotated[str, AfterValidator(_check_regex)]

CategoryCode = Literal["weight", "sexual", "hair", "hormones", "peptides", "lab"]
Tier = Literal["multi-category", "weight", "sexual", "hair", "hormones", "peptides", "lab"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- competitors.yaml -------------------------------------------------------


class Competitor(_Strict):
    name: str
    aliases: list[str] = Field(default_factory=list)
    categories: list[CategoryCode]
    website: str | None
    handles: list[str] = Field(default_factory=list)
    complaint_themes: list[str] = Field(default_factory=list)
    tier: Tier


class CompetitorsFile(_Strict):
    competitors: list[Competitor]
    adjacent: list[Competitor] = Field(default_factory=list)

    def all(self) -> list[Competitor]:
        return [*self.competitors, *self.adjacent]


# --- products.yaml ----------------------------------------------------------


class Alias(_Strict):
    term: str
    inferred: bool = False


class SitePrice(_Strict):
    amount_usd: int = Field(gt=0)
    per: Literal["month"] | None = None
    member_price: bool = False


Status = Literal["live", "coming_soon", "waitlist"]


class Product(_Strict):
    name: str
    generic_names: list[str]
    brand_equivalents: list[str] = Field(default_factory=list)
    formulations: list[str] = Field(default_factory=list)
    form: str
    category: str
    site_price: SitePrice | None
    status: Status
    aliases: list[Alias] = Field(default_factory=list)


class ProductCategory(_Strict):
    name: str
    page: str | None
    status: Status
    membership: bool
    aliases: list[Alias] = Field(default_factory=list)
    # Plain words that point a post at this program ("hair" -> Hair
    # Restoration), so the drafter can offer the program-status claim.
    keywords: list[str] = Field(default_factory=list)


class Membership(_Strict):
    name: str
    price_text: str
    covers: list[str]
    shipping_note: str


class ProductsFile(_Strict):
    membership: Membership
    categories: list[ProductCategory]
    products: list[Product]
    never_offered: list[str] = Field(default_factory=list)


# --- keywords.yaml ----------------------------------------------------------


class BrandTerms(_Strict):
    exact: list[str]
    variants: list[str] = Field(default_factory=list)
    taglines: list[str] = Field(default_factory=list)
    partners: list[str] = Field(default_factory=list)


class ProductTerms(_Strict):
    names: list[str]
    generics: list[str]
    brand_names: list[str]
    slang: list[str] = Field(default_factory=list)
    misspellings: list[str] = Field(default_factory=list)


class Community(_Strict):
    name: str
    kind: Literal["subreddit", "hashtag", "group", "forum"]
    platform: str
    topic: str
    role: Literal["listen", "approved", "avoid", "competitor"]
    verified: bool = False


class KeywordsFile(_Strict):
    brand: BrandTerms
    products: ProductTerms
    competitors: list[str]
    category_intent: list[str]
    risk: dict[str, list[str]]
    gray_market: list[str]
    communities: list[Community]
    urgent_overrides: dict[str, list[Regex]]


# --- compliance_rules.yaml --------------------------------------------------


class PatternRule(_Strict):
    id: Annotated[str, Field(pattern=r"^R\d{1,2}$")]
    pattern: Regex
    reason: str
    source: str
    unless_toggle: str | None = None
    # A match that lies inside a match of this pattern is not a hit (e.g. a
    # negated "no outcome can be guaranteed" for the "guaranteed" rule).
    except_pattern: Regex | None = None
    # Plain, readable phrases the pattern blocks. They are shown to the
    # drafter as a "never write" list, so each one must match the pattern.
    examples: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _examples_match(self) -> "PatternRule":
        rx = re.compile(self.pattern, re.IGNORECASE)
        stray = [e for e in self.examples if not rx.search(e)]
        if self.except_pattern:
            exempt = re.compile(self.except_pattern, re.IGNORECASE)
            stray += [e for e in self.examples if exempt.search(e)]
        if stray:
            raise ValueError(f"{self.id} examples don't match its pattern: {stray}")
        return self


class Toggles(_Strict):
    forbid_medication_names_in_replies: bool = True
    allow_certification_claims: bool = False


class Limits(_Strict):
    max_links: int = Field(ge=0)
    max_hashtags: dict[str, int]
    max_chars: dict[str, int]

    @field_validator("max_hashtags", "max_chars")
    @classmethod
    def _needs_default(cls, value: dict[str, int]) -> dict[str, int]:
        if "default" not in value:
            raise ValueError("limit map needs a 'default' entry")
        return value

    def max_hashtags_for(self, platform: str) -> int:
        return self.max_hashtags.get(platform, self.max_hashtags["default"])

    def max_chars_for(self, platform: str) -> int:
        return self.max_chars.get(platform, self.max_chars["default"])


class Disclosure(_Strict):
    """R2/R3: the first sentence of every reply must carry an approved form."""

    required: bool = False
    forms: list[str] = Field(default_factory=list)
    # Approved forms for Brand Ambassadors / partners (Guide §4). They count
    # as a disclosure, but Pulse drafts as the official account or an
    # identified employee, so using one is a yellow hit (R47).
    influencer_forms: list[str] = Field(default_factory=list)
    default_form: str = ""        # what the drafter opens with (Guide §4 primary form)
    third_person: list[PatternRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def _forms_when_required(self) -> "Disclosure":
        if self.required and not any(f.strip() for f in self.forms):
            raise ValueError("disclosure.required needs at least one form")
        if self.default_form and not any(f.strip().lower() in self.default_form.lower()
                                         for f in self.forms if f.strip()):
            raise ValueError("disclosure.default_form must contain one of the approved forms")
        return self


class ComplianceRulesFile(_Strict):
    toggles: Toggles
    limits: Limits
    prohibited: list[PatternRule]
    patient_confirmation: list[PatternRule]
    yellow: list[PatternRule]
    disclosure: Disclosure = Field(default_factory=Disclosure)


# --- claims.yaml ------------------------------------------------------------

PENDING = "PENDING"
# approved_by for wording taken verbatim from WellPeps' Approved Messaging &
# Response Guide. Publishable only while claims_policy.trust_approved_messaging_guide
# is true (compliance can switch every guide-sourced claim back to pending).
GUIDE_APPROVER = "Approved Messaging & Response Guide v1.0 (Aug 2026)"
# approved_by for an item marked APPROVED in the Approved Messaging Live
# Reference (its controlled companion, §1 "May be used as written, subject to
# current company approval controls"). Same toggle as the guide.
LIVE_REFERENCE_APPROVER = "Approved Messaging Live Reference v1.0 (Aug 2026), status APPROVED"
GUIDE_APPROVERS = frozenset({GUIDE_APPROVER, LIVE_REFERENCE_APPROVER})
# A bracketed slot left for a human ("[approved link]", "[insert ...]").
PLACEHOLDER_RE = re.compile(r"\[[^\]]{2,80}\]")


class Claim(_Strict):
    id: str
    text: str
    products: list[str]
    tier: Literal["green", "yellow"]
    approved_by: str = PENDING
    approved_at: date | None = None
    expires: date | None = None
    source: str
    link_id: str | None = None  # an id from config/links.yaml
    # Verbatim from the Approved Messaging & Response Guide (see GUIDE_APPROVER).
    guide: bool = False
    # FINALIZE: the guide marks something this wording depends on as not yet
    # established by WellPeps. Never publishable while unresolved.
    finalize: bool = False
    finalize_missing: str = ""         # what WellPeps still has to provide
    # An engagement_guide.yaml finalize item whose value, once WellPeps sets
    # it, resolves this claim. None = only a human rewrite resolves it (slots).
    finalize_key: str | None = None
    influencer_only: bool = False      # Brand Ambassador / partner wording: never offered to the drafter
    promotional: bool = False          # counts toward the 20 of the 80/20 rule
    note: str = ""                     # e.g. the guide claim this one duplicates
    # A program-status claim ("... is coming soon"): the products.yaml category
    # whose availability it states. The drafter offers it whenever a post is
    # about that program (harvey.agents.drafter.status_claims).
    program: str | None = None

    @model_validator(mode="after")
    def _guide_rules(self) -> "Claim":
        if self.approved_by in GUIDE_APPROVERS and not self.guide:
            raise ValueError(f"{self.id}: approved_by guide needs guide: true")
        if self.finalize and not self.finalize_missing.strip():
            raise ValueError(f"{self.id}: finalize needs finalize_missing (what WellPeps must provide)")
        if self.finalize_key and not self.finalize:
            raise ValueError(f"{self.id}: finalize_key needs finalize: true")
        # A bracketed slot is a template, not publishable wording: it stays
        # finalize with no finalize_key, so no config value can ever make it
        # publishable; a human fills the slot (and the filled text is checked
        # like any edit: an unfilled slot is red, R42).
        if PLACEHOLDER_RE.search(self.text) and (not self.finalize or self.finalize_key):
            raise ValueError(f"{self.id}: a bracketed slot needs finalize: true and no finalize_key "
                             "(a human must fill it)")
        return self

    @property
    def has_placeholder(self) -> bool:
        return bool(PLACEHOLDER_RE.search(self.text))

    def is_publishable(self, today: date, *, trust_guide: bool = False,
                       finalized: frozenset[str] | set[str] = frozenset()) -> bool:
        approved = bool(self.approved_by.strip()) and self.approved_by != PENDING
        not_expired = self.expires is None or self.expires > today
        if self.finalize and not (self.finalize_key and self.finalize_key in finalized):
            return False
        if self.guide and self.approved_by in GUIDE_APPROVERS:
            return trust_guide and not_expired
        return approved and self.approved_at is not None and not_expired


class ClaimsPolicy(_Strict):
    # Treat wording copied verbatim from the Approved Messaging & Response
    # Guide as approved (it is WellPeps' approved messaging). false -> every
    # guide-sourced claim is pending again.
    trust_approved_messaging_guide: bool = False


class ClaimsFile(_Strict):
    claims_policy: ClaimsPolicy = Field(default_factory=ClaimsPolicy)
    claims: list[Claim]


# --- links.yaml ---------------------------------------------------------------


class PublicLink(_Strict):
    id: Annotated[str, Field(pattern=r"^LNK-[A-Z0-9-]+$")]
    url: str
    label: str
    programs: list[str]
    live: bool = False
    checked_at: date | None = None


class LinksFile(_Strict):
    allowed_domains: list[str]
    links: list[PublicLink] = Field(default_factory=list)


# --- reply_examples.yaml -------------------------------------------------------


class ReplyExample(_Strict):
    id: str
    category: Literal["purchase_intent", "question", "praise"]
    platform: str
    program: str = ""
    post: str
    reply: str
    claim_ids: list[str]


class ReplyExamplesFile(_Strict):
    status: str                  # PENDING until compliance signs the examples off
    usage: str
    examples: list[ReplyExample] = Field(default_factory=list)


# --- guides.yaml ---------------------------------------------------------------
# The Smart Patient's Guides catalog (harvey/guides.py picks one per mention).


class GuideChapter(_Strict):
    title: str                                    # verbatim from the site source
    keywords: list[str] = Field(default_factory=list)
    subtypes: list[str] = Field(default_factory=list)
    needs: list[str] = Field(default_factory=list)


class Guide(_Strict):
    slug: Annotated[str, Field(pattern=r"^[a-z0-9-]+$")]
    title: str
    short: str
    subtitle: str
    claim_id: str
    link_id: str
    programs: list[str] = Field(default_factory=list)
    pages: int = Field(default=0, ge=0)
    keywords: list[str] = Field(default_factory=list)
    prefer_keywords: list[str] = Field(default_factory=list)
    chapters: list[GuideChapter] = Field(default_factory=list)   # ebooks.ts `inside`
    landing: list[GuideChapter] = Field(default_factory=list)    # guide-pages.ts `inside` extras
    default_chapter: str

    @model_validator(mode="after")
    def _default_is_listed(self) -> "Guide":
        titles = [c.title for c in (*self.chapters, *self.landing)]
        if self.default_chapter not in titles:
            raise ValueError(f"guide {self.slug}: default_chapter is not one of its chapters / landing topics")
        return self


class GuidesFile(_Strict):
    guides: list[Guide]
    series: Guide


# --- engagement_guide.yaml ----------------------------------------------------
# WellPeps' community engagement guidelines as Pulse enforces them. The human
# readable version is docs/RULES-OF-ENGAGEMENT.md (the source of truth); this
# file is the machine part: situations, templates, personas, FINALIZE items.

ReplyMode = Literal["draft", "boundary_only", "stop", "no_reply"]
LinkPolicyName = Literal["if_directly_relevant", "none"]
Persona = Literal["official_account", "identified_employee"]
_DISPLAY_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .'-]{0,39}$")


class Engagement(_Strict):
    # Who Pulse drafts as. There is deliberately no anonymous persona
    # (Community Groups and Forums, Option 5; Guide §4).
    persona: Persona = "official_account"
    display_name: str = ""        # identified_employee only: "Hi, I'm <name> - I work with WellPeps."

    @model_validator(mode="after")
    def _name(self) -> "Engagement":
        name = self.display_name.strip()
        if self.persona == "identified_employee":
            if not _DISPLAY_NAME_RE.match(name):
                raise ValueError("identified_employee needs display_name (letters, spaces, . ' -; max 40)")
            if re.match(r"(?i)^(?:dr|doctor|nurse|np|rn|md)\b", name):
                raise ValueError("display_name must not carry a clinical title (R2)")
        elif name:
            raise ValueError("display_name is only used with persona identified_employee")
        return self


class ReplyTemplate(_Strict):
    id: Literal["A", "B", "C", "D", "E"]
    name: str
    structure: str
    source: str
    pulse_use: str = ""


class SituationMatch(_Strict):
    categories: list[str] = Field(default_factory=lambda: ["*"])
    subtypes: list[str] = Field(default_factory=lambda: ["*"])
    subject: Literal["any", "wellpeps", "other"] = "any"
    # Competitor / switching protocol classification (harvey/protocol.py);
    # "" = the mention is outside the protocol's scope. "*" = any, in or out.
    decisions: list[str] = Field(default_factory=lambda: ["*"])
    # The protocol's escalation route (adverse_event | privacy | legal |
    # billing_fraud | support), "" when it isn't an ESCALATE.
    routes: list[str] = Field(default_factory=lambda: ["*"])

    @property
    def matches_everything(self) -> bool:
        return (self.categories == ["*"] and self.subtypes == ["*"] and self.subject == "any"
                and self.decisions == ["*"] and self.routes == ["*"])


class Situation(_Strict):
    id: Annotated[str, Field(pattern=r"^[a-z0-9_]+$")]
    label: str
    match: SituationMatch
    reply: ReplyMode
    template: Literal["A", "B", "C", "D", "E"] | None = None
    # boundary_only: the approved response (a claim id), optionally per program.
    approved_response: str | None = None
    approved_response_by_program: dict[str, str] = Field(default_factory=dict)
    disclosure_claim: str = "CLM-AMG-04-WORK-WITH"
    preferred_claims: list[str] = Field(default_factory=list)
    escalate: str | None = None           # escalation kind (harvey.escalation decides; tested to agree)
    clinical_approval: bool = False       # only role clinical or admin may approve
    link_policy: LinkPolicyName = "if_directly_relevant"
    # Education only: no promotion of any kind (no call to action, price,
    # guide or assessment offer). Protocol EDUCATIONAL ONLY replies.
    education_only: bool = False
    # Smart Patient's Guide reference (binding user instruction 2026-10-07):
    # required = every draft must point to the most relevant guide and say how
    # it helps; if_specific = only when a program-specific guide fits (never
    # the series index); none = not required.
    guide: Literal["required", "if_specific", "none"] = "none"
    notes: str = ""
    sources: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _shape(self) -> "Situation":
        if self.guide != "none" and self.reply != "draft":
            raise ValueError(f"situation {self.id}: a guide reference is only for reply: draft")
        if self.reply == "boundary_only" and not (self.approved_response or self.approved_response_by_program):
            raise ValueError(f"situation {self.id}: boundary_only needs approved_response")
        if self.reply != "boundary_only" and (self.approved_response or self.approved_response_by_program):
            raise ValueError(f"situation {self.id}: approved_response is only for boundary_only")
        return self


class LinkRules(_Strict):
    repeat_window_days: int = Field(default=7, ge=1, le=90)
    max_repeats: int = Field(default=1, ge=1, le=20)    # same link, same community, within the window
    min_words_with_link: int = Field(default=12, ge=1, le=100)


class EightyTwenty(_Strict):
    # A planning metric, never a per-reply quota (Protocol §1; Operations
    # Manual §2.1): shown on the review desk and the Analytics card only.
    window_replies: int = Field(default=10, ge=1, le=100)
    max_promotional_share: float = Field(default=0.2, ge=0.0, le=1.0)
    analytics_days: int = Field(default=30, ge=1, le=366)
    promotion_signals: list[Regex] = Field(default_factory=list)


class ProtocolNeed(_Strict):
    """An underlying need (Protocol §3) and the approved WellPeps claims that
    can speak to it (Protocol §6 capability matching)."""

    label: str
    focus: str                                  # Protocol §3 "Permitted response focus"
    direct: list[str] = Field(default_factory=list)   # claims that directly support it (fit 2)
    partial: list[str] = Field(default_factory=list)  # claims that partly support it (fit 1)
    clinical: bool = False                      # the need itself is clinical (Protocol §4 step 3)
    note: str = ""


class SwitchingProtocol(_Strict):
    """WellPeps_AI_Competitor_Mentions_and_Provider_Switching_Protocol_V1
    as Pulse applies it (harvey/protocol.py)."""

    version: str
    source: str
    # Triage subtypes that put a mention in the protocol's scope (besides a
    # named competitor or a switching / comparison / venting intent).
    scope_subtypes: list[str] = Field(default_factory=list)
    scope_intents: list[str] = Field(default_factory=list)
    scope_needs: list[str] = Field(default_factory=list)
    # Subtypes that are individual clinical matters (Protocol §4 step 3).
    clinical_subtypes: list[str] = Field(default_factory=list)
    needs: dict[str, ProtocolNeed]
    high_min: int = Field(default=6, ge=0, le=8)       # Protocol §9: 6-8 high
    moderate_min: int = Field(default=3, ge=0, le=8)   # 3-5 moderate, 0-2 low
    # Untrusted instructions inside a post (Protocol §2, Example 24).
    injection_patterns: list[Regex] = Field(default_factory=list)

    @model_validator(mode="after")
    def _bands(self) -> "SwitchingProtocol":
        if self.moderate_min > self.high_min:
            raise ValueError("switching_protocol: moderate_min must be <= high_min")
        return self


class StopRules(_Strict):
    max_wellpeps_replies_per_thread: int = Field(default=2, ge=1, le=20)
    individual_advice_subtypes: list[str] = Field(default_factory=list)


class FinalizeItem(_Strict):
    key: Annotated[str, Field(pattern=r"^[a-z0-9_]+$")]
    item: str
    needed_from: str = "WellPeps"
    sources: list[str]
    pulse_effect: str                 # what Pulse does until it is provided
    blocks_approval: bool = False     # claims that depend on it cannot be approved
    # Pulse cannot go live (real posting) until it is provided, even if no
    # claim depends on it (e.g. escalation contacts, the Protocol §13 checklist).
    blocks_go_live: bool = False
    value: str | None = None          # set by WellPeps; resolves finalize claims with this key


class EngagementGuideFile(_Strict):
    engagement: Engagement = Field(default_factory=Engagement)
    templates: list[ReplyTemplate]
    situations: list[Situation]
    link_rules: LinkRules = Field(default_factory=LinkRules)
    eighty_twenty: EightyTwenty = Field(default_factory=EightyTwenty)
    stop_rules: StopRules = Field(default_factory=StopRules)
    switching_protocol: SwitchingProtocol
    finalize: list[FinalizeItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _catch_all_last(self) -> "EngagementGuideFile":
        if not self.situations:
            raise ValueError("engagement_guide.yaml needs situations")
        if not self.situations[-1].match.matches_everything:
            raise ValueError("the last situation must match everything (categories/subtypes/decisions/routes "
                             "'*', subject any)")
        ids = [s.id for s in self.situations]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate situation ids")
        keys = [f.key for f in self.finalize]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate finalize keys")
        return self

    def finalized_keys(self) -> frozenset[str]:
        return frozenset(f.key for f in self.finalize if (f.value or "").strip())


# --- communities.yaml ---------------------------------------------------------

Participation = Literal["allowed", "with_permission", "prohibited", "unknown"]


class CommunityEntry(_Strict):
    id: str                       # "<platform>:<name>", lowercase, e.g. "reddit:r/semaglutide"
    platform: str
    name: str
    brand_participation: Participation = "unknown"
    links_allowed: bool | Literal["unknown"] = "unknown"
    permission_obtained: bool = False
    moderator_contact: str = ""   # a role or public modmail address; never a private person's details
    rules_checked_at: date | None = None
    notes: str = ""
    demo: bool = False            # a fictional sandbox community (DEMO config only)

    @model_validator(mode="after")
    def _consistent(self) -> "CommunityEntry":
        if self.id != f"{self.platform}:{self.name}".lower():
            raise ValueError(f"community id {self.id!r} must be '<platform>:<name>' in lowercase")
        if self.brand_participation in ("allowed", "with_permission") and self.rules_checked_at is None \
                and not self.demo:
            raise ValueError(f"{self.id}: set rules_checked_at when brand participation is {self.brand_participation}")
        if self.permission_obtained and self.brand_participation != "with_permission":
            raise ValueError(f"{self.id}: permission_obtained only applies to with_permission")
        return self


class CommunitiesFile(_Strict):
    communities: list[CommunityEntry] = Field(default_factory=list)
