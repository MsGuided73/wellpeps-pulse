"""scripts/seed_demo.py: deterministic DEMO DATA seeding (no network, no claude)."""

import asyncio
import importlib.util
from pathlib import Path

import pytest

from harvey.models import MentionStatus
from harvey.state import StateManager

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "seed_demo.py"


def _load():
    spec = importlib.util.spec_from_file_location("seed_demo", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seed_fills_every_dashboard_tab(tmp_path):
    seed_demo = _load()
    db = str(tmp_path / "demo.db")

    asyncio.run(seed_demo.seed(db))

    state = StateManager(db)
    summary = asyncio.run(state.get_state_summary())
    assert summary["mentions"][MentionStatus.IN_REVIEW.value] >= 1
    assert summary["mentions"][MentionStatus.ESCALATED.value] >= 1
    assert summary["open_escalations"] >= 1


def test_seed_refuses_without_a_throwaway_db(monkeypatch):
    seed_demo = _load()
    monkeypatch.delenv("PULSE_DB_PATH", raising=False)
    with pytest.raises(SystemExit):
        seed_demo._check_target()
