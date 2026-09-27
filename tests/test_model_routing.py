"""Tests for per-agent model routing (usage.models -> `claude --model`)."""

import asyncio
import json
import os
import tempfile

import pytest
import pytest_asyncio
from pydantic import ValidationError

from harvey.brain import Brain
from harvey.config import DEFAULT_MODELS, UsageConfig
from harvey.state import StateManager


@pytest_asyncio.fixture
async def state():
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(os.path.join(tmpdir, "test.db"))
        await sm.init_db()
        yield sm


class _FakeProcess:
    returncode = 0

    async def communicate(self):
        return json.dumps({"type": "result", "result": "ok", "usage": {}}).encode(), b""


def _capture_cmd(monkeypatch) -> list:
    """Patch the subprocess launcher and return the list the argv lands in."""
    captured = []

    async def fake_exec(*cmd, **kwargs):
        captured.append(list(cmd))
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return captured


def _model_arg(cmd: list) -> str | None:
    return cmd[cmd.index("--model") + 1] if "--model" in cmd else None


# ── model_for precedence ──


@pytest.mark.asyncio
async def test_agent_task_key_beats_agent_key(state):
    brain = Brain(state, models={"handler": "sonnet", "handler.classify_intent": "haiku"})

    assert brain.model_for("handler", "classify_intent") == "haiku"
    assert brain.model_for("handler", "generate_response") == "sonnet"


@pytest.mark.asyncio
async def test_unlisted_and_unlabelled_calls_get_no_model(state):
    brain = Brain(state, models={"scout": "haiku"})

    assert brain.model_for("writer", "write_sequence") is None
    assert brain.model_for("", "") is None


@pytest.mark.asyncio
async def test_brain_without_models_routes_nothing(state):
    assert Brain(state).model_for("scout", "score_contacts") is None


# ── the CLI command ──


@pytest.mark.asyncio
async def test_think_passes_configured_model_to_cli(state, monkeypatch):
    captured = _capture_cmd(monkeypatch)
    brain = Brain(state, models={"scout": "haiku"})

    await brain.think("score these", agent="scout", task="score_contacts", max_retries=0)

    assert _model_arg(captured[0]) == "haiku"


@pytest.mark.asyncio
async def test_think_omits_model_flag_for_unrouted_agent(state, monkeypatch):
    captured = _capture_cmd(monkeypatch)
    brain = Brain(state, models={"scout": "haiku"})

    await brain.think("write it", agent="writer", task="write_sequence", max_retries=0)

    assert "--model" not in captured[0]


# ── config ──


def test_usage_config_defaults_route_structured_calls_to_haiku():
    models = UsageConfig().models

    assert models == DEFAULT_MODELS
    assert "writer" not in models
    assert "handler.generate_response" not in models


def test_empty_models_map_opts_out_of_routing():
    assert UsageConfig(models={}).models == {}


def test_blank_model_name_is_rejected():
    with pytest.raises(ValidationError):
        UsageConfig(models={"scout": "  "})
