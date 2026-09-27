"""Shared prompt plumbing for the agents.

Untrusted public text (the mention) always goes between nonce-tagged
``BEGIN_UNTRUSTED_MENTION`` / ``END_UNTRUSTED_MENTION`` markers; the nonce
is fresh per prompt so the text can't forge a closing marker. Templates
use ``{{name}}`` placeholders, substituted in a single pass so placeholders
that appear inside inserted text stay literal.
"""

import re
import secrets
from pathlib import Path

from harvey.models import Mention
from harvey.paths import PROJECT_ROOT

PROMPTS_DIR = PROJECT_ROOT / "prompts"
MAX_MENTION_CHARS = 4000  # bound prompt size; no agent needs more
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def new_nonce() -> str:
    return secrets.token_hex(8)


def mention_block(mention: Mention, max_chars: int = MAX_MENTION_CHARS) -> str:
    text = mention.text[:max_chars]
    return f"Title: {mention.title}\n\n{text}" if mention.title else text


def fill(template: str, values: dict[str, str]) -> str:
    """Single-pass ``{{name}}`` substitution; unknown names are left as-is."""
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def load(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def render(path: Path, values: dict[str, str]) -> str:
    return fill(path.read_text(encoding="utf-8"), values)
