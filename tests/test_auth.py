"""Phase 7 auth: hashing, login, rate limit, sessions, CSRF, roles, bootstrap,
bind guard, and security headers. Temp DB only; no network."""

import re
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import harvey.auth as auth
import harvey.dashboard as dashboard
from harvey.config import DashboardConfig, PulseConfig
from harvey.models import MentionStatus
from tests.dashboard_helpers import (
    ADMIN,
    CLINICAL,
    PASSWORD,
    REVIEWER,
    VIEWER,
    add_review_item,
    client_for,
    fast_hashing,
    post,
    run,
    setup_app,
    teardown_app,
)
from harvey.state import StateManager


@pytest.fixture
def app_state(tmp_path, monkeypatch):
    state, notifier = setup_app(tmp_path, monkeypatch)
    yield state
    teardown_app()


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- Hashing -------------------------------------------------------------------------


def test_password_hash_is_argon2_and_verifies(monkeypatch):
    fast_hashing(monkeypatch)
    hashed = auth.hash_password(PASSWORD)

    assert hashed.startswith("$argon2id$")
    assert PASSWORD not in hashed
    assert auth.verify_password(hashed, PASSWORD)
    assert not auth.verify_password(hashed, PASSWORD + "x")
    assert not auth.verify_password("not-a-hash", PASSWORD)


def test_short_passwords_are_rejected(monkeypatch):
    fast_hashing(monkeypatch)
    with pytest.raises(ValueError, match="12"):
        auth.hash_password("short-pass")


def test_unknown_role_is_rejected(app_state):
    store = auth.AuthStore(app_state)
    with pytest.raises(ValueError, match="role"):
        run(store.create_user("x@pulse.test", PASSWORD, "superuser"))


def test_duplicate_email_is_rejected_case_insensitively(app_state):
    store = auth.AuthStore(app_state)
    with pytest.raises(ValueError, match="exists"):
        run(store.create_user(ADMIN.upper(), PASSWORD, "viewer"))


def test_password_hash_never_leaves_the_store(app_state):
    users = run(auth.AuthStore(app_state).list_users())
    assert users and all("password_hash" not in u for u in users)


# --- Login / logout ----------------------------------------------------------------


def test_login_success_sets_hardened_cookie(app_state):
    client, _ = client_for()
    resp = client.post("/api/login", json={"email": REVIEWER, "password": PASSWORD})

    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == REVIEWER and body["role"] == "reviewer" and body["csrf"]
    cookie = resp.headers["set-cookie"]
    assert f"{dashboard.SESSION_COOKIE}=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie or "SameSite=Strict" in cookie
    assert "Secure" not in cookie


def test_secure_cookie_flag_from_config(tmp_path, monkeypatch):
    setup_app(tmp_path, monkeypatch, config=PulseConfig(dashboard=DashboardConfig(secure_cookies=True)))
    try:
        client, _ = client_for()
        resp = client.post("/api/login", json={"email": REVIEWER, "password": PASSWORD})
        assert "Secure" in resp.headers["set-cookie"]
    finally:
        teardown_app()


def test_only_the_token_hash_is_stored(app_state):
    client, _ = client_for(REVIEWER)
    token = client.cookies.get(dashboard.SESSION_COOKIE)

    with sqlite3.connect(app_state.db_path) as db:
        ids = [row[0] for row in db.execute("SELECT id FROM sessions")]
    assert token not in ids
    assert auth.token_digest(token) in ids


@pytest.mark.parametrize("email,password", [
    (REVIEWER, "wrong-password-123"),
    ("nobody@pulse.test", PASSWORD),
])
def test_login_failure_is_generic(app_state, email, password):
    client, _ = client_for()
    resp = client.post("/api/login", json={"email": email, "password": password})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid credentials"
    assert dashboard.SESSION_COOKIE not in resp.headers.get("set-cookie", "")


def test_disabled_user_cannot_log_in(app_state):
    run(auth.AuthStore(app_state).disable_user(VIEWER))
    client, _ = client_for()

    resp = client.post("/api/login", json={"email": VIEWER, "password": PASSWORD})

    assert resp.status_code == 401


def test_me_returns_identity_and_csrf(app_state):
    client, csrf = client_for(CLINICAL)
    body = client.get("/api/me").json()

    assert body == {"email": CLINICAL, "name": "Clinical", "role": "clinical", "csrf": csrf}


def test_logout_deletes_the_session(app_state):
    client, csrf = client_for(REVIEWER)
    token = client.cookies.get(dashboard.SESSION_COOKIE)

    assert post(client, csrf, "/api/logout").status_code == 200

    with sqlite3.connect(app_state.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    client.cookies.set(dashboard.SESSION_COOKIE, token)
    assert client.get("/api/me").status_code == 401


def test_disabling_a_user_ends_their_sessions(app_state):
    client, _ = client_for(VIEWER)
    run(auth.AuthStore(app_state).disable_user(VIEWER))

    assert client.get("/api/me").status_code == 401


# --- Rate limit ---------------------------------------------------------------------


def test_five_failures_per_email_lock_the_email(app_state):
    client, _ = client_for()
    for _ in range(5):
        assert client.post("/api/login", json={"email": REVIEWER, "password": "wrong-password-1"}).status_code == 401

    resp = client.post("/api/login", json={"email": REVIEWER, "password": PASSWORD})

    assert resp.status_code == 429


def test_five_failures_per_ip_lock_the_ip(app_state):
    client, _ = client_for()
    for i in range(5):
        client.post("/api/login", json={"email": f"nobody{i}@pulse.test", "password": "wrong-password-1"})

    resp = client.post("/api/login", json={"email": ADMIN, "password": PASSWORD})

    assert resp.status_code == 429


def test_rate_limit_window_expires():
    now = [1000.0]
    limiter = auth.LoginRateLimiter(max_failures=5, window_seconds=900, clock=lambda: now[0])
    for _ in range(5):
        limiter.record_failure("email:a")
    assert limiter.is_blocked("email:a")

    now[0] += 901

    assert not limiter.is_blocked("email:a")


def test_success_resets_the_email_counter():
    limiter = auth.LoginRateLimiter(max_failures=2, window_seconds=900)
    limiter.record_failure("email:a")
    limiter.reset("email:a")
    limiter.record_failure("email:a")

    assert not limiter.is_blocked("email:a")


# --- Sessions -----------------------------------------------------------------------


def _set_expiry(db_path, when: datetime):
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE sessions SET expires_at = ?", (when.isoformat(),))


def _expiry(db_path) -> datetime:
    with sqlite3.connect(db_path) as db:
        return datetime.fromisoformat(db.execute("SELECT expires_at FROM sessions").fetchone()[0])


def test_expired_session_is_rejected_and_removed(app_state):
    client, _ = client_for(REVIEWER)
    _set_expiry(app_state.db_path, _utcnow() - timedelta(minutes=1))

    assert client.get("/api/me").status_code == 401
    with sqlite3.connect(app_state.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_session_expiry_slides_on_use(app_state):
    client, _ = client_for(REVIEWER)
    soon = _utcnow() + timedelta(minutes=5)
    _set_expiry(app_state.db_path, soon)

    assert client.get("/api/me").status_code == 200

    assert _expiry(app_state.db_path) > _utcnow() + timedelta(hours=11)


def test_default_session_lifetime_is_12_hours(app_state):
    client_for(REVIEWER)
    remaining = _expiry(app_state.db_path) - _utcnow()
    assert timedelta(hours=11, minutes=59) < remaining <= timedelta(hours=12)


def test_garbage_cookie_is_rejected(app_state):
    client, _ = client_for()
    client.cookies.set(dashboard.SESSION_COOKIE, "forged-token")
    assert client.get("/api/me").status_code == 401


# --- CSRF -------------------------------------------------------------------------------


@pytest.fixture
def review_item(app_state):
    return run(add_review_item(app_state))


def test_state_change_without_csrf_header_is_forbidden(app_state, review_item):
    client, _ = client_for(REVIEWER)
    resp = client.post(f"/api/mentions/{review_item}/reject", json={"reason": "off-topic"})
    assert resp.status_code == 403


def test_state_change_with_wrong_csrf_is_forbidden(app_state, review_item):
    client, _ = client_for(REVIEWER)
    resp = post(client, "not-the-token", f"/api/mentions/{review_item}/reject", {"reason": "x"})
    assert resp.status_code == 403
    assert run(app_state.get_mention(review_item)).status is MentionStatus.IN_REVIEW


def test_csrf_token_of_another_session_is_forbidden(app_state, review_item):
    _, other_csrf = client_for(ADMIN)
    client, _ = client_for(REVIEWER)
    resp = post(client, other_csrf, f"/api/mentions/{review_item}/reject", {"reason": "x"})
    assert resp.status_code == 403


def test_logout_needs_csrf(app_state):
    client, _ = client_for(REVIEWER)
    assert client.post("/api/logout").status_code == 403


# --- Role matrix --------------------------------------------------------------------------

PROTECTED_GETS = [
    "/api/me", "/api/urgent", "/api/mentions", "/api/mentions/1", "/api/usage",
    "/api/summary", "/api/users", "/api/claims", "/api/harvey/status", "/api/harvey/logs",
]


@pytest.mark.parametrize("path", PROTECTED_GETS)
def test_every_api_route_needs_a_session(app_state, path):
    client, _ = client_for()
    assert client.get(path).status_code == 401


def test_every_api_post_needs_a_session(app_state):
    client, _ = client_for()
    for path in ("/api/mentions/1/approve", "/api/users", "/api/harvey/start", "/api/logout"):
        assert client.post(path, json={}).status_code == 401, path


@pytest.mark.parametrize("email,path,expected", [
    (VIEWER, "/api/users", 403),
    (REVIEWER, "/api/users", 403),
    (CLINICAL, "/api/users", 403),
    (ADMIN, "/api/users", 200),
    (VIEWER, "/api/mentions", 200),
    (VIEWER, "/api/urgent", 200),
])
def test_role_matrix_for_reads(app_state, email, path, expected):
    client, _ = client_for(email)
    assert client.get(path).status_code == expected


@pytest.mark.parametrize("email", [VIEWER, CLINICAL])
def test_non_reviewers_cannot_act_on_drafts(app_state, review_item, email):
    client, csrf = client_for(email)
    for action, body in (("edit", {"text": "x", "claim_ids": []}), ("approve", {}),
                         ("reject", {"reason": "x"}), ("copied", {}), ("mark-posted", {}),
                         ("escalate", {"kind": "legal"})):
        resp = post(client, csrf, f"/api/mentions/{review_item}/{action}", body)
        assert resp.status_code == 403, action


@pytest.mark.parametrize("email", [VIEWER, REVIEWER, CLINICAL])
def test_only_admin_starts_or_stops_the_heartbeat(app_state, email):
    client, csrf = client_for(email)
    assert post(client, csrf, "/api/harvey/start").status_code == 403
    assert post(client, csrf, "/api/harvey/stop").status_code == 403


def test_admin_creates_and_disables_users(app_state):
    client, csrf = client_for(ADMIN)
    resp = post(client, csrf, "/api/users",
                {"email": "new@pulse.test", "name": "New", "role": "viewer", "password": PASSWORD})
    assert resp.status_code == 200
    assert any(u["email"] == "new@pulse.test" for u in client.get("/api/users").json())

    assert post(client, csrf, "/api/users", {"email": "bad@pulse.test", "name": "B", "role": "viewer",
                                             "password": "short"}).status_code == 400
    assert post(client, csrf, "/api/users/disable", {"email": "new@pulse.test"}).status_code == 200
    users = {u["email"]: u for u in client.get("/api/users").json()}
    assert users["new@pulse.test"]["active"] is False


def test_admin_cannot_disable_the_last_admin(app_state):
    client, csrf = client_for(ADMIN)
    resp = post(client, csrf, "/api/users/disable", {"email": ADMIN})
    assert resp.status_code == 409


# --- Bootstrap & bind guard -----------------------------------------------------------------


def _empty_store(tmp_path, monkeypatch):
    fast_hashing(monkeypatch)
    state = StateManager(str(tmp_path / "boot.db"))
    run(state.init_db())
    return auth.AuthStore(state)


def test_bootstrap_creates_admin_when_no_users(tmp_path, monkeypatch):
    store = _empty_store(tmp_path, monkeypatch)

    message = run(auth.bootstrap_admin(store, "Boss@Pulse.test", PASSWORD))

    assert "created" in message
    user = run(store.get_user("boss@pulse.test"))
    assert user["role"] == "admin" and user["active"]


def test_bootstrap_does_nothing_when_users_exist(tmp_path, monkeypatch):
    store = _empty_store(tmp_path, monkeypatch)
    run(store.create_user("first@pulse.test", PASSWORD, "viewer"))

    run(auth.bootstrap_admin(store, "boss@pulse.test", PASSWORD))

    assert run(store.get_user("boss@pulse.test")) is None


def test_bootstrap_without_env_does_nothing(tmp_path, monkeypatch):
    store = _empty_store(tmp_path, monkeypatch)
    run(auth.bootstrap_admin(store, "", ""))
    assert run(store.count_users()) == 0


def test_bootstrap_refuses_a_short_password(tmp_path, monkeypatch):
    store = _empty_store(tmp_path, monkeypatch)

    message = run(auth.bootstrap_admin(store, "boss@pulse.test", "too-short"))

    assert "12" in message
    assert run(store.count_users()) == 0


@pytest.mark.parametrize("role,kind,allowed", [
    ("clinical", "adverse_event", True), ("clinical", "legal", False),
    ("clinical", "privacy", False), ("clinical", "billing_fraud", False),
    ("reviewer", "adverse_event", False), ("reviewer", "legal", True),
    ("admin", "adverse_event", True), ("admin", "privacy", True),
    ("viewer", "legal", False),
])
def test_ack_scoping(role, kind, allowed):
    assert auth.can_ack(role, kind) is allowed


@pytest.mark.parametrize("host,secure", [("0.0.0.0", False), ("192.168.1.20", False)])
def test_bind_guard_requires_secure_cookies_off_loopback(host, secure):
    error = auth.bind_error(host, 1, secure_cookies=secure)
    assert error is not None and "secure_cookies" in error


@pytest.mark.parametrize("host,admins,allowed", [
    ("127.0.0.1", 0, True), ("localhost", 0, True), ("::1", 0, True),
    ("0.0.0.0", 0, False), ("0.0.0.0", 1, True), ("192.168.1.20", 0, False),
])
def test_bind_guard(host, admins, allowed):
    error = auth.bind_error(host, admins, secure_cookies=True)
    assert (error is None) is allowed
    if not allowed:
        assert "admin" in error


def _secure_config(monkeypatch):
    from harvey.config import PulseConfig
    cfg = PulseConfig(dashboard={"secure_cookies": True})
    monkeypatch.setattr(dashboard, "_config", lambda: cfg)


def test_start_dashboard_refuses_public_bind_without_admin(tmp_path, monkeypatch, capsys):
    fast_hashing(monkeypatch)
    _secure_config(monkeypatch)
    monkeypatch.setattr(dashboard, "DB_PATH", tmp_path / "guard.db")
    monkeypatch.delenv("PULSE_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("PULSE_ADMIN_PASSWORD", raising=False)
    started = []
    monkeypatch.setattr(dashboard, "_serve", lambda host, port: started.append((host, port)))

    with pytest.raises(SystemExit) as exc:
        dashboard.start_dashboard(port=5555, host="0.0.0.0")

    assert exc.value.code == 2
    assert started == []
    assert "admin" in capsys.readouterr().out


def test_start_dashboard_refuses_public_bind_without_secure_cookies(tmp_path, monkeypatch, capsys):
    fast_hashing(monkeypatch)
    monkeypatch.setattr(dashboard, "DB_PATH", tmp_path / "guard.db")
    monkeypatch.setenv("PULSE_ADMIN_EMAIL", "boss@pulse.test")
    monkeypatch.setenv("PULSE_ADMIN_PASSWORD", PASSWORD)
    started = []
    monkeypatch.setattr(dashboard, "_serve", lambda host, port: started.append((host, port)))

    with pytest.raises(SystemExit) as exc:
        dashboard.start_dashboard(port=5555, host="0.0.0.0")

    assert exc.value.code == 2
    assert started == []
    assert "secure_cookies" in capsys.readouterr().out


def test_start_dashboard_bootstraps_admin_then_allows_public_bind(tmp_path, monkeypatch):
    fast_hashing(monkeypatch)
    _secure_config(monkeypatch)
    monkeypatch.setattr(dashboard, "DB_PATH", tmp_path / "guard.db")
    monkeypatch.setenv("PULSE_ADMIN_EMAIL", "boss@pulse.test")
    monkeypatch.setenv("PULSE_ADMIN_PASSWORD", PASSWORD)
    started = []
    monkeypatch.setattr(dashboard, "_serve", lambda host, port: started.append((host, port)))

    dashboard.start_dashboard(port=5555, host="0.0.0.0")

    assert started == [("0.0.0.0", 5555)]


# --- Security headers & static policy ---------------------------------------------------------


@pytest.mark.parametrize("path", ["/login", "/api/me", "/static/app.css", "/api/login"])
def test_security_headers_on_every_response(app_state, path):
    client, _ = client_for()
    resp = client.get(path)
    csp = resp.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"


def test_login_page_and_static_assets_are_public(app_state):
    client, _ = client_for()
    assert client.get("/login").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/login.js").status_code == 200


def test_root_redirects_to_login_without_session(app_state):
    client, _ = client_for()
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 303, 307)
    assert resp.headers["location"] == "/login"


def test_root_serves_dashboard_with_session(app_state):
    client, _ = client_for(VIEWER)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "WellPeps Pulse" in resp.text


@pytest.mark.parametrize("name", ["index.html", "login.html"])
def test_pages_have_no_inline_script_or_style(name):
    html = (dashboard.WEB_DIR / name).read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    assert not re.search(r"\son[a-z]+\s*=", html, re.I)
    assert "style=" not in html
    assert "<style" not in html


@pytest.mark.parametrize("name", ["app.js", "login.js"])
def test_scripts_build_no_inline_styles_or_handlers(name):
    js = (dashboard.WEB_DIR / name).read_text(encoding="utf-8")
    assert "style=" not in js
    assert not re.search(r"\son[a-z]+=", js)


def test_rate_limiter_prunes_expired_keys(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(auth, "MAX_TRACKED_KEYS", 3)
    limiter = auth.LoginRateLimiter(window_seconds=10, clock=lambda: now[0])
    for i in range(3):
        limiter.record_failure(f"email:{i}")
    now[0] = 100.0

    limiter.record_failure("email:new")

    assert list(limiter._failures) == ["email:new"]
