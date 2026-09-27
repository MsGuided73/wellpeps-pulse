"""Typed shapes for the knowledge YAML in ``config/`` (pydantic v2).

These mirror the files one-to-one. ``extra="forbid"`` makes an unexpected
field a load error: config that feeds agents must not quietly grow new
fields (for example, internal cost columns).
"""

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator


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


class ComplianceRulesFile(_Strict):
    toggles: Toggles
    limits: Limits
    prohibited: list[PatternRule]
    patient_confirmation: list[PatternRule]
    yellow: list[PatternRule]


# --- claims.yaml ------------------------------------------------------------

PENDING = "PENDING"


class Claim(_Strict):
    id: str
    text: str
    products: list[str]
    tier: Literal["green", "yellow"]
    approved_by: str = PENDING
    approved_at: date | None = None
    expires: date | None = None
    source: str

    def is_publishable(self, today: date) -> bool:
        approved = bool(self.approved_by.strip()) and self.approved_by != PENDING
        not_expired = self.expires is None or self.expires > today
        return approved and self.approved_at is not None and not_expired


class ClaimsFile(_Strict):
    claims: list[Claim]
