"""Mention collectors — deterministic gatherers that emit mentions.

Rules every collector follows:
- Public data only. Nothing behind a login the brand does not own, no DMs.
- Minimal author info: a public handle at most. No names, photos, locations,
  or profile scraping — a mention is about what was said, not who said it.
- Every mention keeps its permalink (``Mention.url`` is required); it is the
  dedupe key and the only way a reviewer can check the source.
- No model calls: collection is free and repeatable. Triage happens later.
"""

from harvey.collectors.base import REGISTRY, Collector, get_collector, register

# Import concrete collectors so they register themselves.
from harvey.collectors import fixture  # noqa: E402,F401

__all__ = ["REGISTRY", "Collector", "get_collector", "register"]
