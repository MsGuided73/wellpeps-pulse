"""docs/RULES-OF-ENGAGEMENT.md is the source of truth: it must keep the
priority order, the superseded / retained tables, and list every FINALIZE item
the config knows about."""

from harvey import knowledge
from harvey.paths import PROJECT_ROOT

DOC = (PROJECT_ROOT / "docs" / "RULES-OF-ENGAGEMENT.md").read_text(encoding="utf-8")


def test_priority_order_and_tables_are_present():
    assert "## Priority order (binding, user instruction 2026-10-07)" in DOC
    assert "## Superseded earlier rules" in DOC
    assert "## Retained earlier safeguards — confirm with WellPeps" in DOC
    assert "## FINALIZE — needed from WellPeps" in DOC
    assert "## 12. Enforced by" in DOC


def test_every_finalize_item_is_in_the_checklist():
    for item in knowledge.engagement_guide().finalize:
        assert f"`{item.key}`" in DOC, item.key


def test_every_protocol_classification_is_documented():
    from harvey import protocol

    for label in protocol.LABELS.values():
        assert label in DOC, label


def test_claude_md_points_to_the_rules_of_engagement():
    claude = (PROJECT_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "docs/RULES-OF-ENGAGEMENT.md" in claude and "source of truth" in claude
