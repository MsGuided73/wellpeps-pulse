"""Configuration: WellPeps Pulse config shape, YAML loading, env parsing."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from harvey.config import (
    DEFAULT_MODELS,
    ConfigError,
    EnvConfig,
    PulseConfig,
    QuietHoursConfig,
    load_config,
    load_env,
)
from harvey.paths import PROJECT_ROOT


MINIMAL = {"organization": {"name": "WellPeps", "market": "US"}}


def test_defaults():
    config = PulseConfig()

    assert config.organization.name == "WellPeps"
    assert config.organization.market == "US"
    assert config.usage.heartbeat_interval_minutes == 15
    assert config.usage.models == DEFAULT_MODELS == {"triager": "haiku"}
    assert config.escalation.sla_minutes == 15
    assert config.escalation.clinical_owner == ""
    assert config.notify.slack_webhook_env == "SLACK_WEBHOOK_URL"
    assert config.retention_days == 180


def test_repo_harvey_yaml_loads():
    config = load_config(str(PROJECT_ROOT / "harvey.yaml"))

    assert config.organization.name == "WellPeps"


def test_load_config_from_yaml_file(tmp_path):
    path = tmp_path / "harvey.yaml"
    path.write_text(yaml.safe_dump({**MINIMAL, "retention_days": 90}))

    config = load_config(str(path))

    assert config.retention_days == 90


def test_legacy_sales_sections_are_rejected(tmp_path):
    path = tmp_path / "harvey.yaml"
    path.write_text(yaml.safe_dump({**MINIMAL, "persona": {"name": "H"}}))

    with pytest.raises((ConfigError, ValidationError)):
        load_config(str(path))


def test_load_config_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "nope.yaml"))


def test_load_config_malformed_yaml_raises(tmp_path):
    path = tmp_path / "harvey.yaml"
    path.write_text("organization: [unclosed\n  - :bad")
    with pytest.raises(ConfigError):
        load_config(str(path))


def test_load_config_empty_file_raises(tmp_path):
    path = tmp_path / "harvey.yaml"
    path.write_text("")
    with pytest.raises(ConfigError):
        load_config(str(path))


def test_load_config_non_mapping_yaml_raises(tmp_path):
    path = tmp_path / "harvey.yaml"
    path.write_text("- just\n- a\n- list\n")
    with pytest.raises(ConfigError):
        load_config(str(path))


def test_usage_overrides():
    config = PulseConfig(
        usage={
            "max_daily_claude_percent": 50,
            "heartbeat_interval_minutes": 5,
            "quiet_hours": {"start": "23:00", "end": "06:00", "timezone": "UTC"},
        }
    )
    assert config.usage.max_daily_claude_percent == 50.0
    assert config.usage.heartbeat_interval_minutes == 5
    assert config.usage.quiet_hours.timezone == "UTC"


def test_quiet_hours_defaults():
    q = QuietHoursConfig()
    assert q.start == "22:00"
    assert q.end == "07:00"


def test_quiet_hours_rejects_bad_time():
    with pytest.raises(ValidationError):
        QuietHoursConfig(start="25:99")


def test_quiet_hours_rejects_bad_timezone():
    with pytest.raises(ValidationError):
        QuietHoursConfig(timezone="Mars/Olympus_Mons")


@pytest.mark.parametrize("percent", [0, 150])
def test_usage_rejects_out_of_range_percent(percent):
    with pytest.raises(ValidationError):
        PulseConfig(usage={"max_daily_claude_percent": percent})


def test_usage_rejects_zero_heartbeat():
    with pytest.raises(ValidationError):
        PulseConfig(usage={"heartbeat_interval_minutes": 0})


@pytest.mark.parametrize("field, value", [("sla_minutes", 0), ("sla_minutes", -5)])
def test_escalation_rejects_non_positive_sla(field, value):
    with pytest.raises(ValidationError):
        PulseConfig(escalation={field: value})


def test_retention_days_must_be_positive():
    with pytest.raises(ValidationError):
        PulseConfig(retention_days=0)


def test_env_config_defaults_are_blank():
    env = EnvConfig()
    assert env.anthropic_api_key == ""
    assert env.slack_webhook_url == ""
    assert env.apify_token == ""


def test_env_config_hides_secrets_from_repr():
    env = EnvConfig(pulse_admin_password="hunter2", slack_webhook_url="https://hooks.example/x")
    assert "hunter2" not in repr(env)
    assert "hooks.example" not in repr(env)


def test_load_env_reads_environment(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "  https://hooks.example/abc  ")
    monkeypatch.setenv("PULSE_ADMIN_EMAIL", "admin@wellpeps.example")
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)
    # Keep a developer's real .env out of the test.
    monkeypatch.setattr("harvey.config.load_dotenv", lambda *a, **k: False)

    env = load_env()

    assert env.slack_webhook_url == "https://hooks.example/abc"
    assert env.pulse_admin_email == "admin@wellpeps.example"
    assert env.apify_token == ""
    assert env.meta_access_token == ""


def test_env_example_has_no_values():
    example = Path(PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assignments = [
        line for line in example.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert assignments, ".env.example should list the supported keys"
    assert all(line.rstrip().endswith("=") for line in assignments)
