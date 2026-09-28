"""Container runtime guards: PULSE_REQUIRE_POSTGRES, and no ~/.claude credentials."""

import asyncio

import pytest

import harvey.cli as cli
import harvey.state as state_module
from harvey.config import ConfigError
from harvey.db import PostgresRequiredError, require_postgres
from harvey.state import StateManager

PG_URL = "postgresql://u@db.invalid:5432/postgres"


def test_require_postgres_off_by_default():
    assert require_postgres() is False


@pytest.mark.parametrize("raw", ["true", "1", "YES"])
def test_require_postgres_refuses_env_sqlite(monkeypatch, raw):
    monkeypatch.setenv("PULSE_REQUIRE_POSTGRES", raw)

    with pytest.raises(PostgresRequiredError, match="PULSE_DATABASE_URL"):
        StateManager()
    with pytest.raises(PostgresRequiredError):
        StateManager.from_env("data/whatever.db")


def test_require_postgres_error_is_a_config_error():
    assert issubclass(PostgresRequiredError, ConfigError)


def test_require_postgres_allows_postgres(monkeypatch):
    monkeypatch.setenv("PULSE_REQUIRE_POSTGRES", "true")
    monkeypatch.setenv("PULSE_DATABASE_URL", PG_URL)

    assert StateManager().backend == "postgres"
    assert StateManager.from_env("x.db").backend == "postgres"


def test_require_postgres_false_keeps_sqlite(monkeypatch):
    monkeypatch.setenv("PULSE_REQUIRE_POSTGRES", "false")

    assert StateManager().backend == "sqlite"


def test_invalid_require_postgres_value_is_an_error(monkeypatch):
    monkeypatch.setenv("PULSE_REQUIRE_POSTGRES", "sometimes")

    with pytest.raises(ConfigError, match="PULSE_REQUIRE_POSTGRES"):
        StateManager()


def test_explicit_sqlite_path_is_still_allowed(monkeypatch, tmp_path):
    # Tests and the demo seeder pass a path on purpose.
    monkeypatch.setenv("PULSE_REQUIRE_POSTGRES", "true")

    assert StateManager(str(tmp_path / "x.db")).backend == "sqlite"


@pytest.mark.parametrize("argv", [
    ["status"], ["ingest", "--fixture"], ["user", "list"], ["escalations"], ["trends"],
    ["dashboard"], ["health"],
])
def test_cli_commands_exit_with_a_clear_error(monkeypatch, tmp_path, capsys, argv):
    monkeypatch.setenv("PULSE_REQUIRE_POSTGRES", "true")
    monkeypatch.setattr(state_module, "DB_PATH", tmp_path / "never.db")
    monkeypatch.setattr("sys.argv", ["pulse", *argv])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code not in (0, None)
    assert "PULSE_REQUIRE_POSTGRES" in capsys.readouterr().out
    assert not (tmp_path / "never.db").exists()


def test_run_exits_with_a_clear_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PULSE_REQUIRE_POSTGRES", "true")
    monkeypatch.setattr(state_module, "DB_PATH", tmp_path / "never.db")
    monkeypatch.setattr("sys.argv", ["pulse", "run"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code not in (0, None)
    assert "PULSE_REQUIRE_POSTGRES" in capsys.readouterr().out
    assert not (tmp_path / "never.db").exists()


# --- no ~/.claude credentials in the container --------------------------------------------


@pytest.fixture
def no_claude_login(monkeypatch, tmp_path):
    from pathlib import Path

    import harvey.integrations.quota as quota

    empty = tmp_path / "empty-home"
    empty.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(empty / ".claude"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: empty))
    monkeypatch.setattr(quota.sys, "platform", "linux")

    def no_network(*a, **kw):
        raise AssertionError("no network call expected without a token")

    monkeypatch.setattr(quota.httpx, "AsyncClient", no_network)
    return quota


def test_quota_degrades_to_none_without_credentials(no_claude_login):
    quota = no_claude_login

    assert quota.get_oauth_token() is None
    assert asyncio.run(quota.QuotaClient().get_utilization()) is None


def test_brain_budget_falls_back_to_call_counter_without_credentials(no_claude_login, tmp_path):
    from harvey.brain import Brain

    state = StateManager(str(tmp_path / "b.db"))
    asyncio.run(state.init_db())
    brain = Brain(state, models={})

    assert asyncio.run(brain.is_within_budget(10, max_percent=80.0)) is True
    assert asyncio.run(brain.is_within_budget(0, max_percent=80.0)) is False
