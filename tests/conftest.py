"""Suite-wide guards.

Every test runs on SQLite unless it explicitly opts into Postgres. Setting
PULSE_DATABASE_URL to "" (rather than deleting it) also stops python-dotenv
from pulling a real URL out of a developer's .env mid-test: load_dotenv never
overrides a variable that is already set.
"""

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "postgres: needs a disposable Postgres at PULSE_TEST_DATABASE_URL (skipped when unset)",
    )


# Deployment overrides a developer's .env (or shell) might carry; "" = unset.
_PINNED_BLANK = (
    "PULSE_DATABASE_URL",
    "PULSE_REQUIRE_POSTGRES",
    "PULSE_SECURE_COOKIES",
    "PULSE_DASHBOARD_URL",
    "PULSE_TRUSTED_PROXIES",
    "PULSE_DEV_NO_AUTH",
    # Demo sandbox and demo config: off unless a test turns them on.
    "PULSE_DEMO_SANDBOX",
    "PULSE_SANDBOX_DB_PATH",
    "PULSE_SANDBOX_BRAND_HANDLE",
    "PULSE_CONFIG_DIR",
    # Slack: never pick up a developer's real webhooks or tokens.
    "SLACK_WEBHOOK_URL",
    "SLACK_BRIEFS_WEBHOOK_URL",
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "SLACK_QUERY_CHANNEL_ID",
)


@pytest.fixture(autouse=True)
def _sqlite_by_default(monkeypatch):
    for name in _PINNED_BLANK:
        monkeypatch.setenv(name, "")
