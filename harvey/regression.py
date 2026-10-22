"""The regression set: real-world post shapes and the handling Pulse must keep.

Memo to Derek Goldberg (2026-10-22, item 4); the Selection Module's release check
("re-run after prompt, model, configuration or approved-source changes").
The posts live in tests/fixtures/regression_posts.yaml; ``scripts/regression_check.py``
runs them through the real triage (subscription only, temporary database) and
reports any post whose handling changed. This module holds the pure parts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from harvey.paths import PROJECT_ROOT

SET_PATH = PROJECT_ROOT / "tests" / "fixtures" / "regression_posts.yaml"
DECISIONS = ("RESPOND", "SKIP", "HOLD", "ROUTE")
EXPECT_KEYS = frozenset({"decision", "drafts", "escalates", "absent_intents", "present_intents"})
_LINK_OR_HANDLE = re.compile(r"https?://|www\.|\bu/[A-Za-z0-9_-]+|@[A-Za-z0-9_]{3,}")


@dataclass(frozen=True)
class Case:
    id: str
    title: str
    text: str
    expect: dict
    source: str = ""


@dataclass(frozen=True)
class Observed:
    decision: str
    drafts: bool
    escalates: bool
    intents: tuple[str, ...] = field(default_factory=tuple)


def load(path: Path = SET_PATH) -> list[Case]:
    """The cases, validated: unique ids, known expectation keys, no links or handles in a post."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases, seen = [], set()
    for raw in data.get("posts") or []:
        case = Case(id=str(raw["id"]), title=str(raw.get("title") or ""), text=str(raw["text"]),
                    expect=dict(raw.get("expect") or {}), source=str(raw.get("source") or ""))
        if case.id in seen:
            raise ValueError(f"duplicate regression id {case.id}")
        seen.add(case.id)
        unknown = set(case.expect) - EXPECT_KEYS
        if unknown:
            raise ValueError(f"{case.id}: unknown expectation keys {sorted(unknown)}")
        if "decision" in case.expect and case.expect["decision"] not in DECISIONS:
            raise ValueError(f"{case.id}: decision must be one of {DECISIONS}")
        if _LINK_OR_HANDLE.search(f"{case.title} {case.text}"):
            raise ValueError(f"{case.id}: no links or handles in regression posts")
        cases.append(case)
    return cases


def mismatches(expect: dict, observed: Observed) -> list[str]:
    """What differs from the expected handling, in plain words ([] = as expected)."""
    problems = []
    if "decision" in expect and observed.decision != expect["decision"]:
        problems.append(f"decision {observed.decision}, expected {expect['decision']}")
    for key, word in (("drafts", "drafted a reply"), ("escalates", "escalated")):
        if key in expect and bool(getattr(observed, key)) != bool(expect[key]):
            problems.append(f"{word}: {getattr(observed, key)}, expected {bool(expect[key])}")
    intents = set(observed.intents)
    for tag in expect.get("absent_intents") or ():
        if tag in intents:
            problems.append(f"tagged {tag}")
    for tag in expect.get("present_intents") or ():
        if tag not in intents:
            problems.append(f"missing tag {tag}")
    return problems
