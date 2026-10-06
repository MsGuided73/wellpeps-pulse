"""Structure of docker-compose.yml (Coolify), the local override, Dockerfile
and .dockerignore. Docker itself isn't needed: the files are parsed as YAML/text."""

import json
import re

import pytest
import yaml

from harvey.paths import PROJECT_ROOT

BASE = PROJECT_ROOT / "docker-compose.yml"
LOCAL = PROJECT_ROOT / "docker-compose.local.yml"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
VAR = re.compile(r"\$\{([A-Z0-9_]+)(?::?[-?][^}]*)?\}")
DOCKER_NETS = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1/32"


@pytest.fixture(scope="module")
def base() -> dict:
    return yaml.safe_load(BASE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def local() -> dict:
    return yaml.safe_load(LOCAL.read_text(encoding="utf-8"))


def _env(service: dict) -> dict[str, str]:
    """environment: list form -> {NAME: raw value}."""
    items = service.get("environment") or []
    assert isinstance(items, list), "use the list form of environment:"
    out = {}
    for item in items:
        name, _, value = item.partition("=")
        out[name] = value
    return out


def test_three_services_from_the_same_build(base):
    services = base["services"]
    assert set(services) == {"worker", "dashboard", "slackbot"}
    for name in ("worker", "dashboard", "slackbot"):
        assert services[name]["build"] == services["worker"]["build"]
        assert services[name]["restart"] == "unless-stopped"
        assert services[name]["init"] is True
        assert services[name]["logging"]["driver"] == "json-file"
        assert services[name]["logging"]["options"]["max-size"]


def test_commands(base):
    s = base["services"]
    assert s["worker"]["command"] == ["python", "-m", "harvey", "run"]
    assert s["dashboard"]["command"] == [
        "python", "-m", "harvey", "dashboard", "--host", "0.0.0.0", "--port", "5555"]
    assert s["slackbot"]["command"] == ["python", "-m", "harvey", "slackbot"]


def test_dashboard_is_exposed_not_published(base):
    s = base["services"]
    assert s["dashboard"]["expose"] == ["5555"]
    for svc in s.values():
        assert "ports" not in svc


def test_no_env_file_and_no_bind_mounts_in_base(base):
    for name, svc in base["services"].items():
        assert "env_file" not in svc, name
        for volume in svc.get("volumes") or []:
            text = volume if isinstance(volume, str) else str(volume.get("source", ""))
            assert ".claude" not in text and "data" not in text and "harvey.yaml" not in text, volume
            assert not text.startswith((".", "/", "~")), f"bind mount in base file: {volume}"
    assert not base.get("volumes")


def test_environment_variables(base):
    worker, dash = (_env(base["services"][n]) for n in ("worker", "dashboard"))
    shared = {"PULSE_DATABASE_URL", "ANTHROPIC_API_KEY", "SLACK_WEBHOOK_URL", "APIFY_TOKEN",
              "META_ACCESS_TOKEN", "PULSE_DASHBOARD_URL", "PULSE_REQUIRE_POSTGRES"}
    assert shared <= set(worker) and shared <= set(dash)
    assert {"PULSE_ADMIN_EMAIL", "PULSE_ADMIN_PASSWORD", "PULSE_SECURE_COOKIES",
            "PULSE_TRUSTED_PROXIES"} <= set(dash)
    assert "PULSE_ADMIN_PASSWORD" not in worker and "PULSE_ADMIN_EMAIL" not in worker
    for env in (worker, dash):
        assert env["PULSE_REQUIRE_POSTGRES"] == "true"
        assert env["PULSE_DATABASE_URL"] == "${PULSE_DATABASE_URL}"
    assert dash["PULSE_SECURE_COOKIES"] == "${PULSE_SECURE_COOKIES:-true}"
    assert dash["PULSE_TRUSTED_PROXIES"] == "${PULSE_TRUSTED_PROXIES:-" + DOCKER_NETS + "}"
    # Briefs channel: whoever builds briefs (worker heartbeat, dashboard button).
    assert worker["SLACK_BRIEFS_WEBHOOK_URL"] == dash["SLACK_BRIEFS_WEBHOOK_URL"] == \
        "${SLACK_BRIEFS_WEBHOOK_URL:-}"


def test_slackbot_service(base):
    bot = base["services"]["slackbot"]
    env = _env(bot)
    assert env["PULSE_REQUIRE_POSTGRES"] == "true"
    assert env["PULSE_DATABASE_URL"] == "${PULSE_DATABASE_URL}"
    for name in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_QUERY_CHANNEL_ID"):
        assert env[name] == "${" + name + ":-}", name  # optional: unset = idle, not crash
    assert {"ANTHROPIC_API_KEY", "PULSE_DASHBOARD_URL"} <= set(env)
    # Read-only and outbound only: no admin bootstrap, no webhooks, no ports.
    for name in ("PULSE_ADMIN_EMAIL", "PULSE_ADMIN_PASSWORD", "SLACK_WEBHOOK_URL", "SLACK_BRIEFS_WEBHOOK_URL"):
        assert name not in env, name
    assert "expose" not in bot and "ports" not in bot
    for name in ("worker", "dashboard"):
        svc_env = _env(base["services"][name])
        assert "SLACK_BOT_TOKEN" not in svc_env and "SLACK_APP_TOKEN" not in svc_env


def test_slackbot_healthcheck_fits_its_heartbeat(base):
    from harvey.health import SLACKBOT_MAX_AGE_MINUTES
    from harvey.slackbot.app import HEARTBEAT_SECONDS

    check = base["services"]["slackbot"]["healthcheck"]
    assert check["test"] == ["CMD", "python", "-m", "harvey", "health", "--slackbot"]
    assert HEARTBEAT_SECONDS / 60 < SLACKBOT_MAX_AGE_MINUTES
    assert _minutes(check["interval"]) <= SLACKBOT_MAX_AGE_MINUTES
    assert _minutes(check["timeout"]) < _minutes(check["interval"])


def test_every_referenced_variable_is_documented():
    documented = ENV_EXAMPLE.read_text(encoding="utf-8")
    referenced = set()
    for path in (BASE, LOCAL):
        referenced |= set(VAR.findall(path.read_text(encoding="utf-8")))
    assert referenced
    missing = sorted(v for v in referenced if v not in documented)
    assert not missing, f"not documented in .env.example: {missing}"


def test_healthchecks(base):
    dash = base["services"]["dashboard"]["healthcheck"]
    worker = base["services"]["worker"]["healthcheck"]
    assert "http://127.0.0.1:5555/healthz" in " ".join(dash["test"])
    assert worker["test"][-4:] == ["-m", "harvey", "health", "--worker"]
    for check in (dash, worker):
        assert check["test"][0] == "CMD"
        for key in ("interval", "timeout", "retries", "start_period"):
            assert key in check


def _minutes(value: str) -> float:
    number, unit = re.fullmatch(r"(\d+)([smh])", value).groups()
    return int(number) * {"s": 1 / 60, "m": 1, "h": 60}[unit]


def test_worker_healthcheck_fits_the_default_max_age(base):
    from harvey.config import PulseConfig
    from harvey.health import default_max_age_minutes

    worker = base["services"]["worker"]["healthcheck"]
    max_age = default_max_age_minutes(PulseConfig())
    # Probing more often than the staleness window is pointless; slower hides outages.
    assert _minutes(worker["interval"]) <= max_age
    assert _minutes(worker["timeout"]) < _minutes(worker["interval"])


def test_local_override(local):
    dash = local["services"]["dashboard"]
    worker = local["services"]["worker"]
    bot = local["services"]["slackbot"]
    assert dash["ports"] == ["127.0.0.1:5555:5555"]
    assert "ports" not in bot
    for svc in (dash, worker, bot):
        assert _env(svc)["PULSE_REQUIRE_POSTGRES"] == "false"
        mounts = " ".join(svc["volumes"])
        assert "./data:/app/data" in mounts
        assert "/home/harvey/.claude" in mounts


def test_dockerfile():
    text = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "HEALTHCHECK" not in text
    assert "|| true" not in text
    assert "claude --version" in text
    assert "USER harvey" in text
    for path in ("config/", "prompts/", "skills/", "harvey.yaml", "db/", "harvey/"):
        assert re.search(rf"^COPY .*\b{re.escape(path)}", text, re.M), path


def test_dockerignore():
    lines = {l.strip() for l in (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()}
    for entry in (".venv", "data", ".env", ".git", "docs/dashboard.gif", "*.egg-info", "__pycache__",
                  ".pytest_cache"):
        assert entry in lines or f"{entry}/" in lines or f"**/{entry}" in lines, entry
    for keep in ("config", "prompts", "skills", "db", "harvey.yaml", "harvey"):
        assert keep not in lines and f"{keep}/" not in lines


def test_slack_variables_are_documented():
    documented = ENV_EXAMPLE.read_text(encoding="utf-8")
    for name in ("SLACK_WEBHOOK_URL", "SLACK_BRIEFS_WEBHOOK_URL", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN",
                 "SLACK_QUERY_CHANNEL_ID"):
        assert re.search(rf"^{name}=$", documented, re.M), f"{name} missing or not blank in .env.example"
    deploy = (PROJECT_ROOT / "docs" / "DEPLOY-SUPABASE-COOLIFY.md").read_text(encoding="utf-8")
    for name in ("SLACK_BRIEFS_WEBHOOK_URL", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_QUERY_CHANNEL_ID",
                 "pulse slack-test", "slack-app-manifest.yaml"):
        assert name in deploy, name


def test_requirements_and_pyproject_list_the_slack_deps():
    req = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for dep in ("slack-bolt>=1.21,<2", "slack-sdk>=3.33,<4", "aiohttp>=3.10,<4"):
        assert dep in req and f'"{dep}"' in pyproject, dep


def test_slack_app_manifest_is_socket_mode_and_minimal():
    manifest = yaml.safe_load((PROJECT_ROOT / "docs" / "slack-app-manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["display_information"]["name"] == "WellPeps Pulse"
    assert manifest["features"]["bot_user"]["display_name"] == "Pulse"
    assert manifest["features"]["app_home"]["messages_tab_enabled"] is False
    settings = manifest["settings"]
    assert settings["socket_mode_enabled"] is True
    assert settings["interactivity"]["is_enabled"] is False
    assert settings["event_subscriptions"]["bot_events"] == ["app_mention"]
    assert "request_url" not in json.dumps(manifest)
    assert sorted(manifest["oauth_config"]["scopes"]["bot"]) == sorted(
        ["app_mentions:read", "chat:write", "reactions:write", "incoming-webhook"])
    assert "user" not in manifest["oauth_config"]["scopes"]
    # No channel IDs or secrets in the manifest.
    text = (PROJECT_ROOT / "docs" / "slack-app-manifest.yaml").read_text(encoding="utf-8")
    assert not re.search(r"\bC0[A-Z0-9]{8,}\b|xox[bp]-|xapp-\d|hooks\.slack\.com", text)


def test_no_slack_channel_ids_hardcoded_in_code():
    for path in (PROJECT_ROOT / "harvey").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for channel_id in ("C0C72G5JWJ1", "C0C6SDT56LF", "C0C6SDTCRJB"):
            assert channel_id not in text, f"{channel_id} hardcoded in {path.name}"
