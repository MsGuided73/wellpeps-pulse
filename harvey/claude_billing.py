"""Which account pays for Pulse's Claude calls (user decision, 2026-10-07).

"Subscription for demo and API for deployed":

- ``subscription``: the logged-in Claude subscription (``claude login``) pays.
  Any ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN is removed from the ``claude``
  CLI's environment, because the CLI bills an API key in preference to the
  subscription whenever one is set (and the dashboard and Slack notifier load
  .env into the process environment). Default on a laptop: local runs and
  demos.
- ``api``: ANTHROPIC_API_KEY (environment, else .env) pays; the CLI gets the
  key explicitly. Default where PULSE_REQUIRE_POSTGRES is on, i.e. the
  deployed docker-compose / Coolify services, so the deployment needs no new
  variable beyond ANTHROPIC_API_KEY.

PULSE_CLAUDE_BILLING=subscription|api overrides the default either way.
"""

import os

from harvey.config import env_setting, parse_bool

BILLING_ENV = "PULSE_CLAUDE_BILLING"
API_KEY_ENV = "ANTHROPIC_API_KEY"
SUBSCRIPTION = "subscription"
API = "api"
MODES = (SUBSCRIPTION, API)
# Credentials the claude CLI prefers over the subscription login.
_API_CREDENTIAL_VARS = (API_KEY_ENV, "ANTHROPIC_AUTH_TOKEN")
_REQUIRE_POSTGRES_ENV = "PULSE_REQUIRE_POSTGRES"


class ClaudeBillingError(RuntimeError):
    """The billing setting is invalid, or API billing has no key."""


def deployed() -> bool:
    raw = env_setting(_REQUIRE_POSTGRES_ENV)
    return parse_bool(raw, _REQUIRE_POSTGRES_ENV) if raw else False


def billing_mode() -> str:
    """subscription | api (explicit setting, else api when deployed)."""
    raw = env_setting(BILLING_ENV).lower()
    if not raw:
        return API if deployed() else SUBSCRIPTION
    if raw not in MODES:
        raise ClaudeBillingError(f"{BILLING_ENV} must be 'subscription' or 'api', got '{raw}'.")
    return raw


def cli_env(mode: str | None = None) -> dict[str, str]:
    """The environment for a ``claude`` CLI call under ``mode`` (default:
    billing_mode()). A new dict; os.environ is never modified."""
    mode = mode or billing_mode()
    env = {k: v for k, v in os.environ.items() if k not in _API_CREDENTIAL_VARS}
    if mode == SUBSCRIPTION:
        return env
    key = env_setting(API_KEY_ENV)
    if not key:
        raise ClaudeBillingError(
            f"{BILLING_ENV} is 'api' (the default when deployed) but {API_KEY_ENV} is not set: add it as a "
            f"runtime variable, or set {BILLING_ENV}=subscription to use the logged-in Claude subscription.")
    env[API_KEY_ENV] = key
    return env


def describe(mode: str | None = None) -> str:
    """Human-readable, secret-free: for logs and `pulse health`."""
    mode = mode or billing_mode()
    if mode == SUBSCRIPTION:
        return "Claude subscription (claude login); API keys are not passed to the CLI"
    return f"Anthropic API key ({API_KEY_ENV} {'set' if env_setting(API_KEY_ENV) else 'MISSING'})"
