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

import yaml
from pydantic import BaseModel, ValidationError

from harvey.models.mention import MAX_MENTION_TEXT_CHARS
from harvey.models.knowledge import (
    Claim,
    ClaimsFile,
    CompetitorsFile,
    ComplianceRulesFile,
    KeywordsFile,
    ProductsFile,
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
def _claims(directory: Path) -> tuple[Claim, ...]:
    claims = tuple(_load(directory, "claims.yaml", ClaimsFile).claims)
    ids = [c.id for c in claims]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise KnowledgeError(f"duplicate claim ids in claims.yaml: {dupes}")
    return claims


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


_CACHED = (
    _competitors, _products, _keywords, _compliance_rules, _claims,
    _competitor_lookup, _product_lookup, _urgent_patterns,
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


def publishable_claim_ids(today: date | None = None) -> set[str]:
    """Claims signed off by a named approver and not expired."""
    day = today or date.today()
    return {c.id for c in claims() if c.is_publishable(day)}


def competitor_lookup() -> Mapping[str, str]:
    """Lower-cased name/alias -> canonical competitor name (incl. adjacent)."""
    return _competitor_lookup(config_dir())


def product_lookup() -> Mapping[str, str]:
    """Lower-cased WellPeps product name/alias -> canonical product name."""
    return _product_lookup(config_dir())


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
    text = (text or "")[:MAX_MENTION_TEXT_CHARS]
    for category, pattern, compiled in _urgent_patterns(config_dir()):
        if compiled.search(text):
            return category, pattern
    return None
