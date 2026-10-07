"""Knowledge loaders for the YAML files in ``config/``.

Each file is parsed and validated once (``lru_cache`` keyed on the config
directory) and returned as frozen pydantic models. ``reload()`` drops every
cache, e.g. after an operator edits a file or a test swaps the directory.

The config directory defaults to the repo's ``config/``; ``PULSE_CONFIG_DIR``
overrides it.
"""

import os
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, TypeVar
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ValidationError

from harvey.models.mention import (
    MAX_MENTION_TEXT_CHARS,
    PROTOCOL_DECISIONS,
    PROTOCOL_INTENTS,
    PROTOCOL_NEEDS,
    TRIAGE_SUBTYPES,
    Category,
)
from harvey.models.knowledge import (
    Claim,
    ClaimsFile,
    ClaimsPolicy,
    CommunitiesFile,
    CommunityEntry,
    CompetitorsFile,
    EngagementGuideFile,
    ComplianceRulesFile,
    KeywordsFile,
    LinksFile,
    ProductsFile,
    PublicLink,
    ReplyExamplesFile,
)
from harvey.paths import PROJECT_ROOT

M = TypeVar("M", bound=BaseModel)


class KnowledgeError(Exception):
    """A knowledge file is missing, unparsable, or fails validation."""


def config_dir() -> Path:
    override = os.environ.get("PULSE_CONFIG_DIR", "").strip()
    return Path(override) if override else PROJECT_ROOT / "config"


def _load(directory: Path, filename: str, model: type[M]) -> M:
    path = directory / filename
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise KnowledgeError(f"missing knowledge file: {path}") from exc
    except yaml.YAMLError as exc:
        raise KnowledgeError(f"invalid YAML in {path}: {exc}") from exc
    try:
        return model.model_validate(raw or {})
    except ValidationError as exc:
        raise KnowledgeError(f"invalid {filename}: {exc}") from exc


# --- Cached per-file loaders (keyed on directory) ---------------------------


@lru_cache(maxsize=None)
def _competitors(directory: Path) -> CompetitorsFile:
    return _load(directory, "competitors.yaml", CompetitorsFile)


@lru_cache(maxsize=None)
def _products(directory: Path) -> ProductsFile:
    return _load(directory, "products.yaml", ProductsFile)


@lru_cache(maxsize=None)
def _keywords(directory: Path) -> KeywordsFile:
    return _load(directory, "keywords.yaml", KeywordsFile)


@lru_cache(maxsize=None)
def _compliance_rules(directory: Path) -> ComplianceRulesFile:
    return _load(directory, "compliance_rules.yaml", ComplianceRulesFile)


@lru_cache(maxsize=None)
def _claims_file(directory: Path) -> ClaimsFile:
    return _load(directory, "claims.yaml", ClaimsFile)


@lru_cache(maxsize=None)
def _claims(directory: Path) -> tuple[Claim, ...]:
    claims = tuple(_claims_file(directory).claims)
    ids = [c.id for c in claims]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise KnowledgeError(f"duplicate claim ids in claims.yaml: {dupes}")
    linked = sorted({c.link_id for c in claims if c.link_id})
    if linked:
        known = {link.id for link in _links(directory).links}
        unknown = [lid for lid in linked if lid not in known]
        if unknown:
            raise KnowledgeError(f"claims.yaml link_id not in links.yaml: {unknown}")
    return claims


# Paths a public reply link may never point at (R8: no sign-up/checkout).
_BLOCKED_LINK_PATH = re.compile(r"assessment|intake|checkout|get-started|cart|buy|login|signup|sign-up",
                                re.IGNORECASE)


def host_allowed(host: str, domains) -> bool:
    """``host`` is one of ``domains`` or a subdomain of one."""
    host = (host or "").strip().lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in domains)


def _link_problem(link: PublicLink, domains: list[str], programs: set[str]) -> str:
    parts = urlsplit(link.url)
    if parts.scheme != "https":
        return "must be https"
    if not host_allowed(parts.hostname or "", domains):
        return f"host not in allowed_domains {domains}"
    if parts.query or parts.fragment or parts.username or parts.password or parts.port:
        return "no query string, fragment, credentials or port (UTM is added at draft time)"
    if _BLOCKED_LINK_PATH.search(parts.path):
        return "sign-up, checkout and assessment paths are not allowed (R8)"
    unknown = sorted(set(link.programs) - programs)
    if unknown or not link.programs:
        return f"unknown programs {unknown or link.programs} (use products.yaml category names or '*')"
    return ""


@lru_cache(maxsize=None)
def _links(directory: Path) -> LinksFile:
    data = _load(directory, "links.yaml", LinksFile)
    domains = [d.strip().lower() for d in data.allowed_domains if d.strip()]
    if not domains:
        raise KnowledgeError("links.yaml needs at least one allowed_domains entry")
    programs = {c.name for c in _products(directory).categories} | {"*"}
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    for link in data.links:
        problem = _link_problem(link, domains, programs)
        if problem:
            raise KnowledgeError(f"links.yaml {link.id}: {problem}")
        url_key = link.url.rstrip("/").lower()
        if link.id in seen_ids or url_key in seen_urls:
            raise KnowledgeError(f"links.yaml {link.id}: duplicate id or url")
        seen_ids.add(link.id)
        seen_urls.add(url_key)
    return data


@lru_cache(maxsize=None)
def _competitor_lookup(directory: Path) -> Mapping[str, str]:
    lookup: dict[str, str] = {}
    for comp in _competitors(directory).all():
        for term in (comp.name, *comp.aliases):
            key = term.strip().lower()
            owner = lookup.setdefault(key, comp.name)
            if owner != comp.name:
                raise KnowledgeError(f"alias {term!r} claimed by both {owner!r} and {comp.name!r}")
    return MappingProxyType(lookup)


@lru_cache(maxsize=None)
def _product_lookup(directory: Path) -> Mapping[str, str]:
    """Lower-cased product name/alias -> canonical product name.

    An alias shared by two products is ambiguous, so it maps to neither.
    """
    lookup: dict[str, str] = {}
    ambiguous: set[str] = set()
    for product in _products(directory).products:
        for term in (product.name, *(a.term for a in product.aliases)):
            key = term.strip().lower()
            owner = lookup.setdefault(key, product.name)
            if owner != product.name:
                ambiguous.add(key)
    for key in ambiguous:
        del lookup[key]
    return MappingProxyType(lookup)


@lru_cache(maxsize=None)
def _urgent_patterns(directory: Path) -> tuple[tuple[str, str, re.Pattern[str]], ...]:
    overrides = _keywords(directory).urgent_overrides
    return tuple(
        (category, pattern, re.compile(pattern, re.IGNORECASE))
        for category, patterns in overrides.items()
        for pattern in patterns
    )


@lru_cache(maxsize=None)
def _drug_lookup(directory: Path) -> Mapping[str, str]:
    """Lower-cased drug term -> canonical generic/category name.

    Sources, first spelling wins: generic names in products.yaml, the
    never-offered list (BPC-157 etc.), and keywords.yaml generics. Brand
    equivalents map to their generic when every product listing that brand
    agrees on a single generic (Ozempic -> semaglutide; Rogaine, listed with
    several generics, maps to nothing).
    """
    prods = _products(directory)
    lookup: dict[str, str] = {}
    terms = [
        *(n for p in prods.products for n in p.generic_names),
        *prods.never_offered,
        *_keywords(directory).products.generics,
    ]
    for term in terms:
        lookup.setdefault(term.strip().lower(), term.strip())
    brands: dict[str, set[str]] = {}
    for product in prods.products:
        generic = product.generic_names[0] if len(product.generic_names) == 1 else None
        for brand in product.brand_equivalents:
            brands.setdefault(brand.strip().lower(), set()).add(generic or "")
    for key, generics in brands.items():
        if len(generics) == 1 and "" not in generics:
            lookup.setdefault(key, lookup.get(next(iter(generics)).lower(), next(iter(generics))))
    return MappingProxyType(lookup)


@lru_cache(maxsize=None)
def _wellpeps_rx(directory: Path) -> re.Pattern[str]:
    brand = _keywords(directory).brand
    names = sorted({t.strip() for t in (*brand.exact, *brand.variants) if t.strip()},
                   key=len, reverse=True)
    alternation = "|".join(re.escape(n) for n in names)
    return re.compile(rf"(?<!\w)(?:{alternation})(?!\w)", re.IGNORECASE)


@lru_cache(maxsize=None)
def _reply_examples(directory: Path) -> ReplyExamplesFile:
    data = _load(directory, "reply_examples.yaml", ReplyExamplesFile)
    known = {c.id for c in _claims(directory)}
    for example in data.examples:
        unknown = [cid for cid in example.claim_ids if cid not in known]
        if unknown:
            raise KnowledgeError(f"reply_examples.yaml {example.id}: unknown claim ids {unknown}")
    return data


def _check_situations(data: EngagementGuideFile, directory: Path) -> None:
    claims = {c.id: c for c in _claims(directory)}
    categories = {c.value for c in Category} | {"*"}
    subtypes = set(TRIAGE_SUBTYPES) | {"*", ""}
    programs = {c.name for c in _products(directory).categories} | {"*"}
    for sit in data.situations:
        bad = sorted(set(sit.match.categories) - categories) + sorted(set(sit.match.subtypes) - subtypes)
        if bad:
            raise KnowledgeError(f"engagement_guide.yaml {sit.id}: unknown category/subtype {bad}")
        unknown_programs = sorted(set(sit.approved_response_by_program) - programs)
        if unknown_programs:
            raise KnowledgeError(f"engagement_guide.yaml {sit.id}: unknown programs {unknown_programs}")
        responses = [sit.approved_response, *sit.approved_response_by_program.values()]
        refs = [r for r in (*responses, sit.disclosure_claim, *sit.preferred_claims) if r]
        missing = [r for r in refs if r not in claims]
        if missing:
            raise KnowledgeError(f"engagement_guide.yaml {sit.id}: unknown claim ids {missing}")
        for cid in (r for r in responses if r):
            if claims[cid].influencer_only or claims[cid].has_placeholder:
                raise KnowledgeError(f"engagement_guide.yaml {sit.id}: {cid} cannot be an approved response "
                                     "(influencer-only or has a [slot])")
    decisions = set(PROTOCOL_DECISIONS) | {"*", ""}
    routes = {"*", "", "adverse_event", "privacy", "legal", "billing_fraud", "support"}
    for sit in data.situations:
        bad = sorted(set(sit.match.decisions) - decisions) + sorted(set(sit.match.routes) - routes)
        if bad:
            raise KnowledgeError(f"engagement_guide.yaml {sit.id}: unknown protocol decision/route {bad}")
    sub_ok = set(TRIAGE_SUBTYPES)
    stray = sorted(set(data.stop_rules.individual_advice_subtypes) - sub_ok)
    if stray:
        raise KnowledgeError(f"engagement_guide.yaml stop_rules: unknown subtypes {stray}")
    proto = data.switching_protocol
    stray = sorted((set(proto.scope_subtypes) | set(proto.clinical_subtypes)) - sub_ok)
    if stray:
        raise KnowledgeError(f"engagement_guide.yaml switching_protocol: unknown subtypes {stray}")
    stray = sorted(set(proto.scope_intents) - set(PROTOCOL_INTENTS)) + sorted(set(proto.scope_needs)
                                                                           - set(PROTOCOL_NEEDS))
    if stray:
        raise KnowledgeError(f"engagement_guide.yaml switching_protocol: unknown scope intents/needs {stray}")
    if set(proto.needs) != set(PROTOCOL_NEEDS):
        raise KnowledgeError("engagement_guide.yaml switching_protocol.needs must list exactly "
                             f"{sorted(PROTOCOL_NEEDS)}")
    missing = sorted({cid for need in proto.needs.values() for cid in (*need.direct, *need.partial)
                      if cid not in claims})
    if missing:
        raise KnowledgeError(f"engagement_guide.yaml switching_protocol: unknown claim ids {missing}")
    keys = {f.key for f in data.finalize}
    dangling = sorted({c.finalize_key for c in claims.values() if c.finalize_key and c.finalize_key not in keys})
    if dangling:
        raise KnowledgeError(f"claims.yaml finalize_key not in engagement_guide.yaml finalize: {dangling}")


@lru_cache(maxsize=None)
def _engagement_guide(directory: Path) -> EngagementGuideFile:
    data = _load(directory, "engagement_guide.yaml", EngagementGuideFile)
    _check_situations(data, directory)
    return data


@lru_cache(maxsize=None)
def _communities(directory: Path) -> tuple[CommunityEntry, ...]:
    data = _load(directory, "communities.yaml", CommunitiesFile)
    ids = [c.id for c in data.communities]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise KnowledgeError(f"duplicate community ids in communities.yaml: {dupes}")
    if any(c.demo for c in data.communities):
        from harvey.sandbox.demo_config import is_demo_config as _marker

        if not _marker(directory):
            raise KnowledgeError("communities.yaml: demo communities are allowed only in a DEMO config copy")
    return tuple(data.communities)


_CACHED = (
    _competitors, _products, _keywords, _compliance_rules, _claims_file, _claims, _links, _reply_examples,
    _engagement_guide, _communities,
    _competitor_lookup, _product_lookup, _urgent_patterns, _drug_lookup,
    _wellpeps_rx,
)


def reload() -> None:
    """Forget every cached file so the next call re-reads config/."""
    for fn in _CACHED:
        fn.cache_clear()


# --- Public API --------------------------------------------------------------


def competitors() -> CompetitorsFile:
    return _competitors(config_dir())


def products() -> ProductsFile:
    return _products(config_dir())


def keywords() -> KeywordsFile:
    return _keywords(config_dir())


def compliance_rules() -> ComplianceRulesFile:
    return _compliance_rules(config_dir())


def claims() -> tuple[Claim, ...]:
    return _claims(config_dir())


def claims_by_id() -> dict[str, Claim]:
    return {c.id: c for c in claims()}


def claims_policy() -> ClaimsPolicy:
    return _claims_file(config_dir()).claims_policy


def engagement_guide() -> EngagementGuideFile:
    """WellPeps' rules of engagement, machine part (config/engagement_guide.yaml)."""
    return _engagement_guide(config_dir())


def communities() -> tuple[CommunityEntry, ...]:
    """The community rules registry (config/communities.yaml)."""
    return _communities(config_dir())


def finalized_keys() -> frozenset[str]:
    """FINALIZE items WellPeps has provided a value for."""
    return engagement_guide().finalized_keys()


def publishable_claim_ids(today: date | None = None) -> set[str]:
    """Claims that may be published: signed off by a named approver (or
    verbatim from the Approved Messaging & Response Guide while
    ``claims_policy.trust_approved_messaging_guide`` is on), not expired, and
    with no unresolved FINALIZE item."""
    day = today or date.today()
    trust = claims_policy().trust_approved_messaging_guide
    done = finalized_keys()
    return {c.id for c in claims() if c.is_publishable(day, trust_guide=trust, finalized=done)}


def unresolved_finalize(claim_ids) -> list[tuple[str, str]]:
    """(claim id, what is missing) for cited claims with an open FINALIZE item."""
    by_id = claims_by_id()
    done = finalized_keys()
    out = []
    for cid in claim_ids:
        claim = by_id.get(cid)
        if claim and claim.finalize and not (claim.finalize_key and claim.finalize_key in done):
            out.append((cid, claim.finalize_missing))
    return out


def links() -> tuple[PublicLink, ...]:
    """The public links registry (config/links.yaml)."""
    return tuple(_links(config_dir()).links)


def reply_examples() -> ReplyExamplesFile:
    """Few-shot style examples for the drafter (status PENDING: guidance only)."""
    return _reply_examples(config_dir())


def links_by_id() -> dict[str, PublicLink]:
    return {link.id: link for link in links()}


def is_demo_config() -> bool:
    """The active config dir is a DEMO copy (scripts/seed_demo.py --sandbox):
    claims there are marked approved for demonstration only."""
    from harvey.sandbox.demo_config import is_demo_config as _marker

    return _marker(config_dir())


def allowed_link_domains() -> list[str]:
    return [d.strip().lower() for d in _links(config_dir()).allowed_domains if d.strip()]


def program_of_product(product: str) -> str:
    """products.yaml category of a WellPeps product name ("" if unknown)."""
    name = (product or "").strip().lower()
    return next((p.category for p in products().products if p.name.lower() == name), "")


def products_for_drug(drug: str) -> list[str]:
    """WellPeps product names for a drug term (generic, brand, product name),
    else every product in a category whose alias is the term ("GLP-1")."""
    term = (drug or "").strip().lower()
    if not term:
        return []
    prods = products().products
    direct = [p.name for p in prods
              if term in {n.lower() for n in (p.name, *p.generic_names, *p.brand_equivalents)}]
    if direct:
        return direct
    categories = {c.name for c in products().categories if term in {a.term.lower() for a in c.aliases}}
    return [p.name for p in prods if p.category in categories]


def program_for(product: str = "", drug: str = "") -> str:
    """The program (products.yaml category) a mention is about: the WellPeps
    product's category, else the single category its drug maps to, else ""."""
    by_product = program_of_product(product)
    if by_product:
        return by_product
    categories = {program_of_product(name) for name in products_for_drug(drug)}
    return categories.pop() if len(categories) == 1 else ""


def competitor_lookup() -> Mapping[str, str]:
    """Lower-cased name/alias -> canonical competitor name (incl. adjacent)."""
    return _competitor_lookup(config_dir())


def product_lookup() -> Mapping[str, str]:
    """Lower-cased WellPeps product name/alias -> canonical product name."""
    return _product_lookup(config_dir())


def drug_lookup() -> Mapping[str, str]:
    """Lower-cased drug term (generic, never-offered, or brand) -> canonical
    generic/category name."""
    return _drug_lookup(config_dir())


def names_wellpeps(text: str) -> bool:
    """True if ``text`` names WellPeps (brand terms from keywords.yaml)."""
    return bool(_wellpeps_rx(config_dir()).search((text or "")[:MAX_MENTION_TEXT_CHARS]))


def medication_names() -> list[str]:
    """Generic and brand drug names from products.yaml (R38 filter input)."""
    names = {
        name
        for product in products().products
        for name in (*product.generic_names, *product.brand_equivalents)
    }
    return sorted(names, key=str.lower)


def urgent_override(text: str) -> tuple[str, str] | None:
    """First (category, pattern) whose regex matches ``text``, else None.

    Scans at most MAX_MENTION_TEXT_CHARS characters.
    """
    hit = urgent_override_match(text)
    return (hit[0], hit[1]) if hit else None


def urgent_override_match(text: str) -> tuple[str, str, re.Match[str]] | None:
    """Like ``urgent_override`` but also returns the regex match, so callers
    can show the words in the post that tripped the rule (never the regex)."""
    text = (text or "")[:MAX_MENTION_TEXT_CHARS]
    for category, pattern, compiled in _urgent_patterns(config_dir()):
        match = compiled.search(text)
        if match:
            return category, pattern, match
    return None
