"""Test helper: register a community whose rules allow a WellPeps reply.

Approval is refused in a community whose rules are unverified ("human posts /
held until permission"; docs/REVISIONS-LOG.md R-20). Tests that exercise the
rest of the approval path post in ``reddit:r/test`` (tests/dashboard_helpers.py)
and register it as allowed with this helper.
"""

from datetime import date

from harvey import knowledge
from harvey.models.knowledge import CommunityEntry

TEST_COMMUNITY = "reddit:r/test"


def allowed_entry(cid: str = TEST_COMMUNITY, links_allowed: bool = True) -> CommunityEntry:
    platform, name = cid.split(":", 1)
    return CommunityEntry(id=cid, platform=platform, name=name, brand_participation="allowed",
                          links_allowed=links_allowed, rules_checked_at=date(2026, 10, 20))


def allow_community(monkeypatch, cid: str = TEST_COMMUNITY, links_allowed: bool = True) -> None:
    """``knowledge.communities()`` also lists ``cid`` as allowed for this test."""
    original = knowledge.communities
    entry = allowed_entry(cid, links_allowed)
    monkeypatch.setattr(knowledge, "communities",
                        lambda: (*(c for c in original() if c.id != cid), entry))
