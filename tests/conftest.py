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


@pytest.fixture(autouse=True)
def _sqlite_by_default(monkeypatch):
    monkeypatch.setenv("PULSE_DATABASE_URL", "")
