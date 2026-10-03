"""PULSE_DEV_NO_AUTH: local-only sign-in bypass for development.

Off by default; only honored for loopback peers; refused at startup for a
non-loopback bind or inside a container (PULSE_REQUIRE_POSTGRES).
"""

import pytest
from fastapi.testclient import TestClient

from harvey import dashboard

LOCAL = ("127.0.0.1", 50000)
REMOTE = ("203.0.113.9", 50000)


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "DB_PATH", tmp_path / "dev.db")
    return tmp_path


def _client(peer):
    return TestClient(dashboard.app, client=peer)


def test_off_by_default_requires_login(app_db):
    assert _client(LOCAL).get("/api/me").status_code == 401


def test_on_signs_in_loopback_as_local_admin(app_db, monkeypatch):
    monkeypatch.setenv("PULSE_DEV_NO_AUTH", "true")

    resp = _client(LOCAL).get("/api/me")

    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"
    assert resp.json()["must_change_password"] is False


def test_on_still_requires_csrf_for_writes(app_db, monkeypatch):
    monkeypatch.setenv("PULSE_DEV_NO_AUTH", "true")
    client = _client(LOCAL)
    csrf = client.get("/api/me").json()["csrf"]

    assert client.post("/api/briefs/generate", json={"period": "daily"}).status_code == 403
    assert csrf


def test_on_ignored_for_non_loopback_peers(app_db, monkeypatch):
    monkeypatch.setenv("PULSE_DEV_NO_AUTH", "true")

    assert _client(REMOTE).get("/api/me").status_code == 401


def test_home_page_skips_login_redirect_when_on(app_db, monkeypatch):
    monkeypatch.setenv("PULSE_DEV_NO_AUTH", "true")

    resp = _client(LOCAL).get("/", follow_redirects=False)

    assert resp.status_code == 200


def test_ignored_inside_containers(app_db, monkeypatch):
    monkeypatch.setenv("PULSE_DEV_NO_AUTH", "true")
    monkeypatch.setenv("PULSE_REQUIRE_POSTGRES", "true")

    assert dashboard.dev_no_auth() is False


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20"])
def test_startup_refuses_dev_mode_off_loopback(app_db, monkeypatch, capsys, host):
    monkeypatch.setenv("PULSE_DEV_NO_AUTH", "true")
    started = []
    monkeypatch.setattr(dashboard, "_serve", lambda h, p: started.append((h, p)))

    with pytest.raises(SystemExit) as exc:
        dashboard.start_dashboard(port=5555, host=host)

    assert exc.value.code == 2
    assert started == []
    assert "PULSE_DEV_NO_AUTH" in capsys.readouterr().out


def test_dev_user_cannot_change_password(app_db, monkeypatch):
    monkeypatch.setenv("PULSE_DEV_NO_AUTH", "true")
    client = _client(LOCAL)
    csrf = client.get("/api/me").json()["csrf"]

    resp = client.post("/api/me/password", headers={"X-CSRF-Token": csrf},
                       json={"current_password": "x" * 12, "new_password": "y" * 12})

    assert resp.status_code == 400
    assert resp.json()["error"] == "dev_no_auth"


def test_invalid_value_is_a_config_error(app_db, monkeypatch):
    monkeypatch.setenv("PULSE_DEV_NO_AUTH", "maybe")

    with pytest.raises(Exception):
        dashboard.dev_no_auth()
