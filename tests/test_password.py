"""Password management: change my password, admin reset, forced change on next
sign-in, `pulse user reset-password`, and schema v6 (must_change_password).

Temp SQLite DBs only; argon2 runs with tiny parameters (dashboard_helpers).
"""

import json
import re
import sqlite3
from pathlib import Path

import pytest

import harvey.auth as auth
import harvey.cli as cli
import harvey.dashboard as dashboard
import harvey.state as state_module
from harvey.state import MIGRATIONS, StateManager
from tests.dashboard_helpers import (
    ADMIN, PASSWORD, REVIEWER, VIEWER, client_for, fast_hashing, post, run, setup_app, teardown_app,
)

NEW_PASSWORD = "fixture-new-phrase-02"
TEMP_PASSWORD = "fixture-temp-phrase-03"
PG_DIR = Path(__file__).resolve().parent.parent / "db" / "postgres"


@pytest.fixture
def app_state(tmp_path, monkeypatch):
    state, _ = setup_app(tmp_path, monkeypatch)
    yield state
    teardown_app()


def _change(client, csrf, current=PASSWORD, new=NEW_PASSWORD):
    return post(client, csrf, "/api/me/password", {"current_password": current, "new_password": new})


def _login(client, email, password):
    return client.post("/api/login", json={"email": email, "password": password})


def _actions(state, action_type):
    async def _rows():
        async with state.connect() as db:
            async with db.execute("SELECT agent, details_json FROM actions WHERE action_type = ?",
                                  (action_type,)) as cursor:
                return [dict(r) for r in await cursor.fetchall()]
    return run(_rows())


def _session_count(state, email):
    async def _count():
        async with state.connect() as db:
            async with db.execute(
                    "SELECT COUNT(*) AS n FROM sessions s JOIN users u ON u.id = s.user_id "
                    "WHERE u.email = ?", (email,)) as cursor:
                return (await cursor.fetchone())["n"]
    return run(_count())


# --- Change my password ------------------------------------------------------------------------


def test_change_password_keeps_current_session_revokes_others_and_rotates_csrf(app_state):
    client, csrf = client_for(REVIEWER)
    other, _ = client_for(REVIEWER)

    resp = _change(client, csrf)

    assert resp.status_code == 200, resp.text
    new_csrf = resp.json()["csrf"]
    assert new_csrf and new_csrf != csrf
    assert client.get("/api/me").status_code == 200
    assert client.get("/api/me").json()["csrf"] == new_csrf
    assert other.get("/api/me").status_code == 401
    assert _session_count(app_state, REVIEWER) == 1
    # the old CSRF token is dead, the new one works
    assert post(client, csrf, "/api/logout").status_code == 403
    fresh, _ = client_for(None)
    assert _login(fresh, REVIEWER, PASSWORD).status_code == 401
    assert _login(fresh, REVIEWER, NEW_PASSWORD).status_code == 200


def test_change_password_is_logged_without_password_material(app_state):
    client, csrf = client_for(REVIEWER)

    assert _change(client, csrf).status_code == 200

    rows = _actions(app_state, "password_changed")
    assert len(rows) == 1
    details = json.loads(rows[0]["details_json"])
    assert details["email"] == REVIEWER and details["by"] == REVIEWER
    blob = json.dumps(rows)
    assert PASSWORD not in blob and NEW_PASSWORD not in blob and "argon2" not in blob


def test_change_password_stamps_password_changed_at(app_state):
    client, csrf = client_for(VIEWER)
    assert _change(client, csrf).status_code == 200
    user = run(auth.AuthStore(app_state).get_user(VIEWER))
    assert user["password_changed_at"]
    assert user["must_change_password"] is False


def test_wrong_current_password_is_generic(app_state):
    client, csrf = client_for(REVIEWER)

    resp = _change(client, csrf, current="not-the-right-password")

    assert resp.status_code == 400
    assert resp.json()["error"] == "current_password_incorrect"
    assert "argon2" not in resp.text
    fresh, _ = client_for(None)
    assert _login(fresh, REVIEWER, PASSWORD).status_code == 200


def test_wrong_current_password_counts_toward_the_login_throttle(app_state):
    client, csrf = client_for(REVIEWER)
    for _ in range(auth.MAX_LOGIN_FAILURES):
        assert _change(client, csrf, current="not-the-right-password").status_code == 400

    # now blocked, even with the right current password
    resp = _change(client, csrf)
    assert resp.status_code == 429
    assert resp.json()["error"] == "too_many_attempts"
    # and the same counter guards sign-in for that email
    fresh, _ = client_for(None)
    assert _login(fresh, REVIEWER, PASSWORD).status_code == 429


@pytest.mark.parametrize("new,code", [
    ("short", "password_too_short"),
    (PASSWORD, "password_reused"),
    (REVIEWER, "password_is_email"),
    ("x" * (auth.MAX_PASSWORD_LENGTH + 1), None),
])
def test_change_password_policy(app_state, new, code):
    client, csrf = client_for(REVIEWER)

    resp = _change(client, csrf, new=new)

    assert resp.status_code in (400, 422)
    if code:
        assert resp.json()["error"] == code
    fresh, _ = client_for(None)
    assert _login(fresh, REVIEWER, PASSWORD).status_code == 200


def test_password_equal_to_email_is_rejected_case_insensitively():
    email = "long.address@pulse.test"
    with pytest.raises(auth.PasswordPolicyError) as exc:
        auth.check_new_password(email.upper(), email=email)
    assert exc.value.code == "password_is_email"


def test_change_password_needs_csrf(app_state):
    client, _ = client_for(REVIEWER)
    resp = client.post("/api/me/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert resp.status_code == 403


def test_change_password_needs_a_session(app_state):
    client, _ = client_for(None)
    resp = client.post("/api/me/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert resp.status_code == 401


# --- Admin reset ---------------------------------------------------------------------------------


def _reset(client, csrf, email, password=TEMP_PASSWORD):
    return post(client, csrf, "/api/users/reset-password", {"email": email, "new_password": password})


def test_admin_reset_revokes_sessions_and_forces_a_change(app_state):
    victim, _ = client_for(REVIEWER)
    admin, csrf = client_for(ADMIN)

    resp = _reset(admin, csrf, REVIEWER)

    assert resp.status_code == 200, resp.text
    assert victim.get("/api/me").status_code == 401
    assert _session_count(app_state, REVIEWER) == 0
    user = run(auth.AuthStore(app_state).get_user(REVIEWER))
    assert user["must_change_password"] is True
    fresh, _ = client_for(None)
    assert _login(fresh, REVIEWER, PASSWORD).status_code == 401
    assert _login(fresh, REVIEWER, TEMP_PASSWORD).status_code == 200
    rows = _actions(app_state, "password_reset")
    assert len(rows) == 1 and json.loads(rows[0]["details_json"])["by"] == ADMIN
    assert TEMP_PASSWORD not in json.dumps(rows)


@pytest.mark.parametrize("email", [VIEWER, REVIEWER])
def test_only_admins_reset_passwords(app_state, email):
    client, csrf = client_for(email)
    assert _reset(client, csrf, ADMIN).status_code == 403


def test_admin_cannot_reset_own_password_via_admin_route(app_state):
    admin, csrf = client_for(ADMIN)
    resp = _reset(admin, csrf, ADMIN.upper())
    assert resp.status_code == 400
    assert resp.json()["error"] == "use_change_password"
    assert admin.get("/api/me").status_code == 200


def test_admin_reset_unknown_user_is_404(app_state):
    admin, csrf = client_for(ADMIN)
    assert _reset(admin, csrf, "nobody@pulse.test").status_code == 404


def test_admin_reset_enforces_policy(app_state):
    admin, csrf = client_for(ADMIN)
    assert _reset(admin, csrf, REVIEWER, "short").json()["error"] == "password_too_short"
    assert _reset(admin, csrf, REVIEWER, REVIEWER).status_code == 400


def test_admin_reset_needs_csrf(app_state):
    admin, _ = client_for(ADMIN)
    resp = admin.post("/api/users/reset-password", json={"email": REVIEWER, "new_password": TEMP_PASSWORD})
    assert resp.status_code == 403


# --- Forced change on next sign-in ------------------------------------------------------------------


def _forced_client(app_state):
    admin, csrf = client_for(ADMIN)
    assert _reset(admin, csrf, REVIEWER).status_code == 200
    client, _ = client_for(None)
    resp = _login(client, REVIEWER, TEMP_PASSWORD)
    assert resp.status_code == 200
    assert resp.json()["must_change_password"] is True
    return client, resp.json()["csrf"]


@pytest.mark.parametrize("path", ["/api/urgent", "/api/mentions", "/api/summary", "/api/claims",
                                  "/api/harvey/status", "/api/analytics/options"])
def test_forced_change_blocks_other_gets(app_state, path):
    client, _ = _forced_client(app_state)
    resp = client.get(path)
    assert resp.status_code == 403
    assert resp.json()["error"] == "password_change_required"


def test_forced_change_blocks_other_posts(app_state):
    client, csrf = _forced_client(app_state)
    resp = post(client, csrf, "/api/mentions/1/approve")
    assert resp.status_code == 403
    assert resp.json()["error"] == "password_change_required"


def test_forced_change_allows_me_and_logout(app_state):
    client, csrf = _forced_client(app_state)
    me = client.get("/api/me")
    assert me.status_code == 200 and me.json()["must_change_password"] is True
    assert post(client, csrf, "/api/logout").status_code == 200


def test_forced_change_clears_on_success(app_state):
    client, csrf = _forced_client(app_state)

    resp = _change(client, csrf, current=TEMP_PASSWORD)

    assert resp.status_code == 200, resp.text
    assert client.get("/api/me").json()["must_change_password"] is False
    assert client.get("/api/urgent").status_code == 200
    assert run(auth.AuthStore(app_state).get_user(REVIEWER))["must_change_password"] is False


def test_forced_change_cannot_reuse_the_temporary_password(app_state):
    client, csrf = _forced_client(app_state)
    resp = _change(client, csrf, current=TEMP_PASSWORD, new=TEMP_PASSWORD)
    assert resp.json()["error"] == "password_reused"
    assert client.get("/api/urgent").status_code == 403


# --- New users -------------------------------------------------------------------------------------


def test_users_created_in_the_ui_must_change_by_default(app_state):
    admin, csrf = client_for(ADMIN)
    assert post(admin, csrf, "/api/users", {"email": "new@pulse.test", "name": "New", "role": "viewer",
                                           "password": PASSWORD}).status_code == 200
    assert post(admin, csrf, "/api/users", {"email": "optout@pulse.test", "name": "Opt", "role": "viewer",
                                           "password": PASSWORD, "must_change_password": False}).status_code == 200
    users = {u["email"]: u for u in admin.get("/api/users").json()}
    assert users["new@pulse.test"]["must_change_password"] is True
    assert users["optout@pulse.test"]["must_change_password"] is False
    assert all("password_hash" not in u for u in users.values())


def test_store_create_user_does_not_force_by_default(app_state):
    assert run(auth.AuthStore(app_state).get_user(ADMIN))["must_change_password"] is False


def test_bootstrap_admin_is_not_forced_to_change(tmp_path, monkeypatch):
    fast_hashing(monkeypatch)
    state = StateManager(str(tmp_path / "boot.db"))
    run(state.init_db())
    store = auth.AuthStore(state)
    run(auth.bootstrap_admin(store, "boot@pulse.test", PASSWORD))
    assert run(store.get_user("boot@pulse.test"))["must_change_password"] is False


# --- CLI ------------------------------------------------------------------------------------------


@pytest.fixture
def cli_db(tmp_path, monkeypatch):
    fast_hashing(monkeypatch)
    path = tmp_path / "cli.db"
    monkeypatch.setattr(state_module, "DB_PATH", path)
    state = StateManager(str(path))
    run(state.init_db())
    run(auth.AuthStore(state).create_user(REVIEWER, PASSWORD, "reviewer", name="R"))
    return state


def _cli(argv):
    args = cli.build_parser().parse_args(argv)
    args.func(args)


def _passwords(monkeypatch, *values):
    answers = list(values)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": answers.pop(0))


def test_cli_user_add_forces_change_by_default(cli_db, monkeypatch):
    _passwords(monkeypatch, PASSWORD, PASSWORD, PASSWORD, PASSWORD)
    _cli(["user", "add", "a@pulse.test", "--role", "viewer", "--name", "A"])
    _cli(["user", "add", "b@pulse.test", "--role", "viewer", "--name", "B", "--no-force-change"])
    store = auth.AuthStore(cli_db)
    assert run(store.get_user("a@pulse.test"))["must_change_password"] is True
    assert run(store.get_user("b@pulse.test"))["must_change_password"] is False


def test_cli_reset_password_revokes_sessions_and_forces_change(cli_db, monkeypatch, capsys):
    store = auth.AuthStore(cli_db)
    user = run(store.get_user(REVIEWER))
    token, _ = run(store.create_session(user["id"]))
    _passwords(monkeypatch, TEMP_PASSWORD, TEMP_PASSWORD)

    _cli(["user", "reset-password", REVIEWER.upper()])

    out = capsys.readouterr().out
    assert TEMP_PASSWORD not in out and "reset" in out
    assert run(store.session_user(token)) is None
    assert run(store.get_user(REVIEWER))["must_change_password"] is True
    assert run(store.authenticate(REVIEWER, TEMP_PASSWORD)) is not None
    rows = _actions(cli_db, "password_reset")
    assert len(rows) == 1 and rows[0]["agent"] == "cli"


def test_cli_reset_password_no_force_change(cli_db, monkeypatch):
    _passwords(monkeypatch, TEMP_PASSWORD, TEMP_PASSWORD)
    _cli(["user", "reset-password", REVIEWER, "--no-force-change"])
    assert run(auth.AuthStore(cli_db).get_user(REVIEWER))["must_change_password"] is False


def test_cli_reset_password_mismatch_changes_nothing(cli_db, monkeypatch):
    _passwords(monkeypatch, TEMP_PASSWORD, TEMP_PASSWORD + "x")
    with pytest.raises(SystemExit) as exc:
        _cli(["user", "reset-password", REVIEWER])
    assert exc.value.code != 0
    assert run(auth.AuthStore(cli_db).authenticate(REVIEWER, PASSWORD)) is not None


def test_cli_reset_password_policy_and_unknown_user(cli_db, monkeypatch, capsys):
    _passwords(monkeypatch, "short", "short", TEMP_PASSWORD, TEMP_PASSWORD)
    with pytest.raises(SystemExit):
        _cli(["user", "reset-password", REVIEWER])
    assert "12" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        _cli(["user", "reset-password", "nobody@pulse.test"])


def test_cli_user_list_shows_pending_change(cli_db, monkeypatch, capsys):
    _passwords(monkeypatch, TEMP_PASSWORD, TEMP_PASSWORD)
    _cli(["user", "reset-password", REVIEWER])
    capsys.readouterr()
    _cli(["user", "list"])
    assert "must change password" in capsys.readouterr().out


# --- Schema ------------------------------------------------------------------------------------------


def test_migration_v6_upgrades_a_v5_database_and_keeps_users(tmp_path, monkeypatch):
    fast_hashing(monkeypatch)
    path = tmp_path / "v5.db"
    with sqlite3.connect(path) as db:
        for script in MIGRATIONS[:5]:
            db.executescript(script)
        db.execute("PRAGMA user_version = 5")
        db.execute("INSERT INTO users (email, display_name, role, password_hash, active) "
                   "VALUES ('old@pulse.test', 'Old', 'admin', ?, 1)", (auth.HASHER.hash(PASSWORD),))
        db.commit()

    state = StateManager(str(path))
    run(state.init_db())

    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS) >= 6
    user = run(auth.AuthStore(state).get_user("old@pulse.test"))
    assert user["role"] == "admin" and user["display_name"] == "Old"
    assert user["must_change_password"] is False and user["password_changed_at"] is None
    assert run(auth.AuthStore(state).authenticate("old@pulse.test", PASSWORD)) is not None


def test_postgres_0002_adds_the_columns_idempotently_and_bumps_the_version():
    sql = (PG_DIR / "0002_password_management.sql").read_text(encoding="utf-8")
    body = re.sub(r"--[^\n]*", "", sql).lower()
    assert "add column if not exists must_change_password boolean not null default false" in body
    assert "add column if not exists password_changed_at timestamp" in body
    assert re.search(r"values\s*\(\s*6\s*,", body)
    assert "on conflict (version) do nothing" in body
    assert len(MIGRATIONS) == 6
