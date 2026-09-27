"""`pulse user add|list|disable` and `pulse dashboard --host`. Temp DB; getpass faked."""

import pytest

import harvey.auth as auth
import harvey.cli as cli
import harvey.state as state_module
from harvey.state import StateManager
from tests.dashboard_helpers import PASSWORD, fast_hashing, run


@pytest.fixture
def db(tmp_path, monkeypatch):
    fast_hashing(monkeypatch)
    path = tmp_path / "cli.db"
    monkeypatch.setattr(state_module, "DB_PATH", path)
    return StateManager(str(path))


def _run(argv):
    args = cli.build_parser().parse_args(argv)
    args.func(args)


def _passwords(monkeypatch, *values):
    answers = list(values)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": answers.pop(0))


def test_user_add_prompts_for_password_twice(db, monkeypatch, capsys):
    _passwords(monkeypatch, PASSWORD, PASSWORD)

    _run(["user", "add", "Nurse@Pulse.test", "--role", "clinical", "--name", "Nurse Jo"])

    user = run(auth.AuthStore(db).get_user("nurse@pulse.test"))
    assert user["role"] == "clinical" and user["display_name"] == "Nurse Jo" and user["active"]
    assert "created" in capsys.readouterr().out
    assert PASSWORD not in capsys.readouterr().out


def test_user_add_mismatched_confirmation_fails(db, monkeypatch):
    _passwords(monkeypatch, PASSWORD, PASSWORD + "x")

    with pytest.raises(SystemExit) as exc:
        _run(["user", "add", "a@pulse.test", "--role", "viewer", "--name", "A"])

    assert exc.value.code != 0
    run(db.init_db())
    assert run(auth.AuthStore(db).get_user("a@pulse.test")) is None


def test_user_add_short_password_fails(db, monkeypatch, capsys):
    _passwords(monkeypatch, "short", "short")

    with pytest.raises(SystemExit):
        _run(["user", "add", "a@pulse.test", "--role", "viewer", "--name", "A"])

    assert "12" in capsys.readouterr().out


def test_user_add_rejects_unknown_role(db):
    with pytest.raises(SystemExit):
        _run(["user", "add", "a@pulse.test", "--role", "root", "--name", "A"])


def test_user_list_and_disable(db, monkeypatch, capsys):
    _passwords(monkeypatch, PASSWORD, PASSWORD, PASSWORD, PASSWORD)
    _run(["user", "add", "a@pulse.test", "--role", "admin", "--name", "A"])
    _run(["user", "add", "b@pulse.test", "--role", "reviewer", "--name", "B"])
    capsys.readouterr()

    _run(["user", "disable", "b@pulse.test"])
    _run(["user", "list"])

    out = capsys.readouterr().out
    assert "a@pulse.test" in out and "admin" in out
    assert "b@pulse.test" in out and "disabled" in out
    assert "argon2" not in out


def test_user_disable_unknown_email_fails(db):
    with pytest.raises(SystemExit):
        _run(["user", "disable", "ghost@pulse.test"])


def test_dashboard_host_flag_is_passed_through(monkeypatch):
    import harvey.dashboard as dashboard

    seen = []
    monkeypatch.setattr(dashboard, "start_dashboard", lambda port, host: seen.append((host, port)))

    _run(["dashboard", "--host", "0.0.0.0", "--port", "6000"])

    assert seen == [("0.0.0.0", 6000)]


def test_dashboard_defaults_to_loopback(monkeypatch):
    import harvey.dashboard as dashboard

    seen = []
    monkeypatch.setattr(dashboard, "start_dashboard", lambda port, host: seen.append((host, port)))

    _run(["dashboard"])

    assert seen == [("127.0.0.1", 5555)]
