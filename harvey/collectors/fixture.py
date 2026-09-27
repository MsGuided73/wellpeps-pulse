"""Fixture collector: replays raw posts from JSONL files.

Used for tests, demos, and ``pulse ingest --fixture``. Each non-blank line
is one raw post::

    {platform, external_id, url, author_handle, text,
     title?, posted_at?, parent_external_id?, engagement?}

Only the fields above are read. Anything else a raw post carries (display
name, email, avatar, profile link, location...) is dropped here, so it can
never reach the database. Rows that can't become a valid Mention (bad JSON,
missing or non-http(s) permalink) are skipped and logged, never fatal.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from pydantic import ValidationError

from harvey.collectors.base import Collector, register
from harvey.models import Mention, Platform
from harvey.paths import PROJECT_ROOT

logger = logging.getLogger("harvey.collectors.fixture")

DEFAULT_FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "mentions"

_PLATFORMS = {p.value for p in Platform}


def _naive_utc(value) -> datetime | None:
    """Parse an ISO timestamp to naive UTC; None when absent or unparsable."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _text(raw: dict, key: str) -> str:
    value = raw.get(key)
    return value.strip() if isinstance(value, str) else ""


def to_mention(raw: dict, platform_default: Platform = Platform.OTHER) -> Mention:
    """Map one raw post to a Mention, keeping only allow-listed fields.

    Raises ValidationError when the permalink is missing or not http(s).
    """
    platform_raw = _text(raw, "platform").lower()
    platform = Platform(platform_raw) if platform_raw in _PLATFORMS else platform_default
    engagement = raw.get("engagement")
    return Mention(
        platform=platform,
        external_id=_text(raw, "external_id"),
        url=_text(raw, "url"),
        author_handle=_text(raw, "author_handle"),
        parent_external_id=_text(raw, "parent_external_id"),
        text=_text(raw, "text"),
        title=_text(raw, "title"),
        posted_at=_naive_utc(raw.get("posted_at")),
        engagement=dict(engagement) if isinstance(engagement, dict) else {},
    )


@register
class FixtureCollector(Collector):
    name = "fixture"
    platform_default = Platform.OTHER
    cost_note = "free: reads local JSONL files, no network"

    def __init__(self, directory: str | Path | None = None):
        self.directory = Path(directory) if directory else DEFAULT_FIXTURE_DIR
        self.skipped = 0

    def _files(self) -> list[Path]:
        if not self.directory.is_dir():
            logger.warning(f"fixture directory not found: {self.directory}")
            return []
        return sorted(self.directory.glob("*.jsonl"))

    def _skip(self, where: str, why: str) -> None:
        self.skipped += 1
        logger.warning(f"skipping fixture row {where}: {why}")

    async def collect(self, since: datetime | None = None) -> AsyncIterator[Mention]:
        self.skipped = 0
        for path in self._files():
            lines = path.read_text(encoding="utf-8").splitlines()
            for lineno, line in enumerate(lines, start=1):
                if not line.strip():
                    continue
                where = f"{path.name}:{lineno}"
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as exc:
                    self._skip(where, f"invalid JSON ({exc.msg})")
                    continue
                if not isinstance(raw, dict):
                    self._skip(where, "not a JSON object")
                    continue
                try:
                    mention = to_mention(raw, self.platform_default)
                except ValidationError as exc:
                    first = exc.errors()[0].get("msg", "invalid") if exc.errors() else "invalid"
                    self._skip(where, first)
                    continue
                if since and mention.posted_at and mention.posted_at < since:
                    continue
                yield mention
