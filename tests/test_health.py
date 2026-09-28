"""Health: /healthz, `pulse health [--worker]`, the heartbeat liveness stamp,
and proxy-aware login throttling."""

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import harvey.cli as cli
import harvey.dashboard as dashboard
import harvey.health as health
import harvey.state as state_module
from harvey.config import PulseConfig
from harvey.state import StateManager
from tests.dashboard_helpers import ADMIN, PASSWORD, run, setup_app, teardown_app

NOW = datetime(2026, 9, 28, 12, 0, 0)


# --- health module ---------------------------------------------------------------------


def test_default_max_age_follows_the_heartbeat_settings():
    assert health.default_max_age_minutes(PulseConfig()) == 2 * 15 + 10
    cfg = PulseConfig(usage={"heartbeat_interval_minutes": 2, "urgent_tick_minutes": 7})
    assert health.default_max_age_minutes(cfg) == 2 * 7 + 10


def test_record_heartbeat_stores_naive_utc_iso(tmp_path):
    state = StateManager(str(tmp_path / "h.db"))
    run(state.init_db())

    run(health.record_heartbeat(state, now=NOW))

    assert run(state.get_setting(health.HEARTBEAT_KEY)) == "2026-09-28T12:00:00"


def test_worker_health_fresh_stale_missing_and_garbage(tmp_path):
    state = StateManager(str(tmp_path / "h.db"))
    run(state.init_db())
    cfg = PulseConfig()

    ok, reason = run(health.worker_health(state, cfg, now=NOW))
    assert not ok and "no heartbeat" in reason

    run(health.record_heartbeat(state, now=NOW - timedelta(minutes=39)))
    ok, reason = run(health.worker_health(state, cfg, now=NOW))
    assert ok, reason

    ok, reason = run(health.worker_health(state, cfg, max_age_minutes=30, now=NOW))
    assert not ok and "39" in reason

    run(health.record_heartbeat(state, now=NOW - timedelta(minutes=41)))
    ok, reason = run(health.worker_health(state, cfg, now=NOW))
    assert not ok and "stale" in reason

    run(state.set_setting(health.HEARTBEAT_KEY, "not-a-date"))
    ok, reason = run(health.worker_health(state, cfg, now=NOW))
    assert not ok


def test_check_database_on_sqlite(tmp_path):
    state = StateManager(str(tmp_path / "h.db"))
    run(state.init_db())

    run(health.check_database(state))  # no exception


def test_check_database_verifies_the_postgres_schema(monkeypatch):
    calls = []

    async def fake_verify(url, expected, location):
        calls.append((url, expected))
        raise RuntimeError("schema too old")

    monkeypatch.setattr(health.postgres, "verify_schema", fake_verify)
    state = StateManager(database_url="postgresql://u@db.invalid/postgres")

    with pytest.raises(RuntimeError, match="schema too old"):
        run(health.check_database(state))
    assert calls and calls[0][1] == len(state_module.MIGRATIONS)


# --- CLI ------------------------------------------------------------------------------------


@pytest.fixture
def cli_db(tmp_path, monkeypatch):
    path = tmp_path / "cli.db"
    monkeypatch.setattr(state_module, "DB_PATH", path)
    monkeypatch.setattr(cli, "_health_config", lambda: PulseConfig())
    return StateManager(str(path))


def _health(argv):
    args = cli.build_parser().parse_args(["health", *argv])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    return exc.value.code


def test_health_db_only_passes(cli_db, capsys):
    assert _health([]) == 0
    assert "ok" in capsys.readouterr().out.lower()


def test_health_worker_passes_with_a_fresh_heartbeat(cli_db, monkeypatch, capsys):
    run(cli_db.init_db())
    run(health.record_heartbeat(cli_db, now=NOW - timedelta(minutes=5)))
    monkeypatch.setattr(health, "utcnow", lambda: NOW)

    assert _health(["--worker"]) == 0


def test_health_worker_fails_when_stale(cli_db, monkeypatch, capsys):
    run(cli_db.init_db())
    run(health.record_heartbeat(cli_db, now=NOW - timedelta(minutes=50)))
    monkeypatch.setattr(health, "utcnow", lambda: NOW)

    assert _health(["--worker"]) == 1
    out = capsys.readouterr().out.strip()
    assert "stale" in out and len(out.splitlines()) == 1


def test_health_worker_honors_max_age(cli_db, monkeypatch):
    run(cli_db.init_db())
    run(health.record_heartbeat(cli_db, now=NOW - timedelta(minutes=50)))
    monkeypatch.setattr(health, "utcnow", lambda: NOW)

    assert _health(["--worker", "--max-age-minutes", "60"]) == 0
    assert _health(["--worker", "--max-age-minutes", "45"]) == 1


def test_health_worker_fails_without_any_heartbeat(cli_db):
    assert _health(["--worker"]) == 1


def test_health_fails_when_the_database_is_unreachable(cli_db, monkeypatch, capsys):
    async def broken(state):
        raise OSError("connection refused")

    monkeypatch.setattr(health, "check_database", broken)

    assert _health([]) == 1
    assert "connection refused" in capsys.readouterr().out


def test_health_rejects_nonpositive_max_age(cli_db):
    assert _health(["--worker", "--max-age-minutes", "0"]) == 2


# --- heartbeat loop stamps liveness, even on idle / quiet cycles ---------------------------


@pytest.mark.asyncio
async def test_heartbeat_records_liveness_on_an_idle_quiet_cycle(tmp_path, monkeypatch):
    import harvey.main as main_mod
    from harvey.escalation import SweepReport

    config = PulseConfig(usage={"quiet_hours": {"start": "00:00", "end": "23:59", "timezone": "UTC"}})
    db_path = str(tmp_path / "pulse.db")

    async def fake_sweep(state, notifier, cfg, now=None):
        return SweepReport()

    async def fake_sleep(seconds, stop_event):
        stop_event.set()
        return True

    monkeypatch.setattr(main_mod, "load_config", lambda: config)
    monkeypatch.setattr(main_mod, "StateManager", lambda: StateManager(db_path))
    monkeypatch.setattr(main_mod, "sweep", fake_sweep)
    monkeypatch.setattr(main_mod.SlackNotifier, "from_config", classmethod(lambda cls, cfg: cls(None)))
    monkeypatch.setattr(main_mod, "_interruptible_sleep", fake_sleep)
    monkeypatch.setattr(health, "utcnow", lambda: NOW)

    await main_mod.heartbeat(asyncio.Event())

    stamp = await StateManager(db_path).get_setting(health.HEARTBEAT_KEY)
    assert stamp == NOW.isoformat()


# --- dashboard /healthz ----------------------------------------------------------------------


@pytest.fixture
def app(tmp_path, monkeypatch):
    setup_app(tmp_path, monkeypatch)
    yield
    teardown_app()


def test_healthz_is_public_and_minimal(app):
    resp = TestClient(dashboard.app).get("/healthz")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert resp.headers["Content-Security-Policy"].startswith("default-src 'self'")
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Cache-Control"] == "no-store"


def test_healthz_returns_503_without_detail_when_the_db_fails(app, monkeypatch):
    async def broken(state):
        raise RuntimeError("postgres at secret-host is down")

    monkeypatch.setattr(health, "check_database", broken)
    resp = TestClient(dashboard.app).get("/healthz")

    assert resp.status_code == 503
    assert resp.json() == {"ok": False}
    assert "secret-host" not in resp.text
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_healthz_returns_503_when_state_cannot_open(app, monkeypatch):
    async def no_state():
        raise ValueError("PULSE_DATABASE_URL must be a postgresql:// URL")

    monkeypatch.setattr(dashboard, "get_state", no_state)

    assert TestClient(dashboard.app).get("/healthz").status_code == 503


# --- login throttle keys on the real client IP -----------------------------------------------


def _fail_logins(client, n, xff):
    for _ in range(n):
        client.post("/api/login", json={"email": f"x{_}@pulse.test", "password": "wrong-password-x"},
                    headers={"X-Forwarded-For": xff})


def test_throttle_uses_forwarded_ip_from_a_trusted_proxy(tmp_path, monkeypatch):
    cfg = PulseConfig(dashboard={"trusted_proxies": ["10.0.0.0/8"]})
    setup_app(tmp_path, monkeypatch, config=cfg)
    try:
        proxy = TestClient(dashboard.app, client=("10.0.0.2", 1234))
        _fail_logins(proxy, 5, "198.51.100.4")

        blocked = proxy.post("/api/login", json={"email": ADMIN, "password": PASSWORD},
                             headers={"X-Forwarded-For": "198.51.100.4"})
        other = proxy.post("/api/login", json={"email": ADMIN, "password": PASSWORD},
                           headers={"X-Forwarded-For": "198.51.100.5"})

        assert blocked.status_code == 429
        assert other.status_code == 200  # a teammate behind the same proxy is not locked out
    finally:
        teardown_app()


def test_throttle_ignores_forwarded_ip_from_an_untrusted_peer(tmp_path, monkeypatch):
    cfg = PulseConfig(dashboard={"trusted_proxies": ["10.0.0.0/8"]})
    setup_app(tmp_path, monkeypatch, config=cfg)
    try:
        attacker = TestClient(dashboard.app, client=("203.0.113.9", 1234))
        # Rotating spoofed XFF values must not dodge the per-IP limit.
        for i in range(5):
            _fail_logins(attacker, 1, f"198.51.100.{i}")

        resp = attacker.post("/api/login", json={"email": ADMIN, "password": PASSWORD},
                             headers={"X-Forwarded-For": "198.51.100.200"})
        assert resp.status_code == 429
    finally:
        teardown_app()


def test_uvicorn_is_started_without_its_own_proxy_header_rewriting(monkeypatch):
    import uvicorn

    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))

    dashboard._serve("0.0.0.0", 5555)

    assert seen["proxy_headers"] is False
    assert seen["host"] == "0.0.0.0" and seen["port"] == 5555
