"""Test helper: treat every registry link as not live for one test.

The guide pages went live on wellpeps.com (links.yaml `live: true`, 2026-10-22).
Tests of the "link not live yet" safeguard (yellow in the filter, approval
blocked) call this so they keep exercising it whatever the real registry says.
"""

from harvey import knowledge


def links_not_live(monkeypatch) -> None:
    original = knowledge.links
    monkeypatch.setattr(knowledge, "links", lambda: tuple(
        link.model_copy(update={"live": False, "checked_at": None}) for link in original()))
