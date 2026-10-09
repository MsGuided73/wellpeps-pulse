"""Claude billing: subscription locally (demo), API key when deployed (user decision 2026-10-07)."""

import asyncio

import pytest

from harvey import claude_billing as cb
from harvey.brain import Brain

KEY = "sk-ant-test-not-a-real-key"


@pytest.fixture
def env(monkeypatch):
    """Pin every setting in os.environ so the repo's .env is never read."""
    for name in (cb.BILLING_ENV, cb.API_KEY_ENV, "ANTHROPIC_AUTH_TOKEN", "PULSE_REQUIRE_POSTGRES"):
        monkeypatch.setenv(name, "")
    return monkeypatch


def test_local_default_is_the_subscription_and_strips_api_credentials(env):
    env.setenv(cb.API_KEY_ENV, KEY)
    env.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    assert cb.billing_mode() == cb.SUBSCRIPTION
    cli = cb.cli_env()
    assert cb.API_KEY_ENV not in cli and "ANTHROPIC_AUTH_TOKEN" not in cli
    assert "PATH" in cli or "Path" in cli


def test_deployed_default_is_the_api_key(env):
    env.setenv("PULSE_REQUIRE_POSTGRES", "true")
    env.setenv(cb.API_KEY_ENV, KEY)
    assert cb.billing_mode() == cb.API
    assert cb.cli_env()[cb.API_KEY_ENV] == KEY


def test_explicit_setting_overrides_the_default(env):
    env.setenv("PULSE_REQUIRE_POSTGRES", "true")
    env.setenv(cb.BILLING_ENV, "Subscription")
    assert cb.billing_mode() == cb.SUBSCRIPTION
    env.setenv("PULSE_REQUIRE_POSTGRES", "false")
    env.setenv(cb.BILLING_ENV, "api")
    env.setenv(cb.API_KEY_ENV, KEY)
    assert cb.billing_mode() == cb.API


def test_api_billing_without_a_key_is_a_clear_error(env):
    env.setenv(cb.BILLING_ENV, "api")
    with pytest.raises(cb.ClaudeBillingError, match="ANTHROPIC_API_KEY is not set"):
        cb.cli_env()
    assert "MISSING" in cb.describe()


def test_invalid_setting_is_rejected(env):
    env.setenv(cb.BILLING_ENV, "both")
    with pytest.raises(cb.ClaudeBillingError, match="must be 'subscription' or 'api'"):
        cb.billing_mode()


def test_describe_never_reveals_the_key(env):
    env.setenv(cb.BILLING_ENV, "api")
    env.setenv(cb.API_KEY_ENV, KEY)
    assert KEY not in cb.describe() and "set" in cb.describe()


class _State:
    async def increment_usage(self):
        pass


def _capture(monkeypatch):
    seen = {}

    async def fake_exec(*cmd, **kwargs):
        seen["env"] = kwargs.get("env")

        class P:
            returncode = 0

            async def communicate(self):
                return b'{"result": "ok"}', b""

        return P()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return seen


def test_brain_passes_the_billing_environment_to_the_cli(env):
    env.setenv(cb.API_KEY_ENV, KEY)
    seen = _capture(env)
    brain = Brain(_State())
    brain._record_usage = lambda *a, **k: asyncio.sleep(0)
    asyncio.run(brain.think("hi", max_retries=0))
    assert seen["env"] is not None and cb.API_KEY_ENV not in seen["env"]       # subscription locally

    env.setenv("PULSE_REQUIRE_POSTGRES", "true")
    asyncio.run(brain.think("hi", max_retries=0))
    assert seen["env"][cb.API_KEY_ENV] == KEY                                   # API when deployed


def test_brain_refuses_to_call_claude_when_api_billing_has_no_key(env):
    env.setenv(cb.BILLING_ENV, "api")
    seen = _capture(env)
    assert asyncio.run(Brain(_State()).think("hi", max_retries=0)) == ""
    assert "env" not in seen
