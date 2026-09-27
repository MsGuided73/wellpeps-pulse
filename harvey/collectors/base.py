"""Collector contract and registry.

A collector is a deterministic gatherer: ``collect(since)`` yields
``Mention`` objects and never calls a model. Concrete collectors register
themselves by ``name`` so config and the CLI can build them with
``get_collector(name, **cfg)``.
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import AsyncIterator, ClassVar

from harvey.models import Mention, Platform
from harvey.models.mention import MAX_MENTION_TEXT_CHARS, MAX_MENTION_TITLE_CHARS

__all__ = [
    "MAX_MENTION_TEXT_CHARS", "MAX_MENTION_TITLE_CHARS", "Collector", "REGISTRY",
    "bound_mention", "get_collector", "register",
]

logger = logging.getLogger("harvey.collectors")


def bound_mention(mention: Mention) -> Mention:
    """Return a copy with text/title cut to the storage limits.

    A cut mention gets ``engagement["truncated"] = True`` and a log line, so
    a reviewer knows the stored text is not the whole post.
    """
    text_over = len(mention.text) > MAX_MENTION_TEXT_CHARS
    title_over = len(mention.title) > MAX_MENTION_TITLE_CHARS
    if not (text_over or title_over):
        return mention
    logger.warning(
        f"truncating oversized mention {mention.url} "
        f"(text {len(mention.text)} chars, title {len(mention.title)} chars)"
    )
    return mention.model_copy(update={
        "text": mention.text[:MAX_MENTION_TEXT_CHARS],
        "title": mention.title[:MAX_MENTION_TITLE_CHARS],
        "engagement": {**mention.engagement, "truncated": True},
    })


class Collector(ABC):
    """Base class for every collector.

    Class attributes:
        name: registry key, also the ``runs.stage`` and ``sources.collector``.
        platform_default: platform assumed when a raw post doesn't say.
        cost_note: one line on what running it costs (API credits, free...).
    """

    name: ClassVar[str] = ""
    platform_default: ClassVar[Platform] = Platform.OTHER
    cost_note: ClassVar[str] = ""

    @abstractmethod
    def collect(self, since: datetime | None = None) -> AsyncIterator[Mention]:
        """Yield mentions posted after ``since`` (all of them when None).

        Implement as an ``async def`` generator.
        """


REGISTRY: dict[str, type[Collector]] = {}


def register(cls: type[Collector]) -> type[Collector]:
    """Class decorator: add a collector to REGISTRY under ``cls.name``."""
    name = (getattr(cls, "name", "") or "").strip()
    if not name:
        raise ValueError(f"collector {cls.__name__} needs a non-empty name")
    if name in REGISTRY and REGISTRY[name] is not cls:
        raise ValueError(f"collector {name!r} is already registered")
    REGISTRY[name] = cls
    return cls


def get_collector(name: str, **cfg) -> Collector:
    """Build the registered collector ``name`` with ``cfg`` as kwargs."""
    try:
        cls = REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(REGISTRY)) or "none"
        raise ValueError(f"unknown collector {name!r} (known: {known})") from None
    return cls(**cfg)
