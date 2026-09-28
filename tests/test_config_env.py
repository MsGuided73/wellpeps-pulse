"""Deployment env overrides applied by load_config (env wins over harvey.yaml)."""

import pytest
import yaml
from pydantic import ValidationError

from harvey.config import ConfigError, PulseConfig, load_config, parse_bool


def _write(tmp_path, data: dict) -> str:
    path = tmp_path / "harvey.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return str(path)


@pytest.fixture
def yaml_path(tmp_path):
    return _write(tmp_path, {
        "notify": {"dashboard_url": "https://yaml.example.test"},
        "dashboard": {"secure_cookies": False, "trusted_proxies": ["192.0.2.1"]},
    })


def test_trusted_proxies_default_empty():
    assert PulseConfig().dashboard.trusted_proxies == []


def test_trusted_proxies_in_yaml_are_validated(tmp_path):
    with pytest.raises(ValidationError):
        load_config(_write(tmp_path, {"dashboard": {"trusted_proxies": ["nope"]}}))


def test_no_env_keeps_yaml_values(yaml_path):
    config = load_config(yaml_path)

    assert config.dashboard.secure_cookies is False
    assert config.notify.dashboard_url == "https://yaml.example.test"
    assert config.dashboard.trusted_proxies == ["192.0.2.1"]


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("TRUE", True), ("1", True), ("yes", True), (" Yes ", True),
    ("false", False), ("False", False), ("0", False), ("no", False), ("NO", False),
])
def test_secure_cookies_env_overrides_yaml(yaml_path, monkeypatch, raw, expected):
    monkeypatch.setenv("PULSE_SECURE_COOKIES", raw)

    assert load_config(yaml_path).dashboard.secure_cookies is expected


@pytest.mark.parametrize("raw", ["maybe", "on", "2", "tru"])
def test_invalid_secure_cookies_env_is_a_config_error(yaml_path, monkeypatch, raw):
    monkeypatch.setenv("PULSE_SECURE_COOKIES", raw)

    with pytest.raises(ConfigError, match="PULSE_SECURE_COOKIES"):
        load_config(yaml_path)


def test_parse_bool_helper():
    assert parse_bool("Yes", "X") is True
    assert parse_bool("0", "X") is False
    with pytest.raises(ConfigError, match="X"):
        parse_bool("perhaps", "X")


@pytest.mark.parametrize("url", [
    "https://pulse.example.com", "https://pulse.example.com/", "http://localhost:5555",
    "http://127.0.0.1:5555", "http://[::1]:5555",
])
def test_dashboard_url_env_overrides_yaml(yaml_path, monkeypatch, url):
    monkeypatch.setenv("PULSE_DASHBOARD_URL", url)

    assert load_config(yaml_path).notify.dashboard_url == url


@pytest.mark.parametrize("url", [
    "http://pulse.example.com", "ftp://pulse.example.com", "pulse.example.com", "https://",
    "javascript:alert(1)", "https://pulse example.com", "http://localhost.evil.com",
])
def test_invalid_dashboard_url_env_is_a_config_error(yaml_path, monkeypatch, url):
    monkeypatch.setenv("PULSE_DASHBOARD_URL", url)

    with pytest.raises(ConfigError, match="PULSE_DASHBOARD_URL"):
        load_config(yaml_path)


def test_dashboard_url_in_yaml_is_validated(tmp_path):
    with pytest.raises(ValidationError):
        load_config(_write(tmp_path, {"notify": {"dashboard_url": "http://pulse.example.com"}}))


def test_trusted_proxies_env_overrides_yaml(yaml_path, monkeypatch):
    monkeypatch.setenv("PULSE_TRUSTED_PROXIES", "10.0.0.0/8, 172.16.0.0/12 ,127.0.0.1/32")

    assert load_config(yaml_path).dashboard.trusted_proxies == [
        "10.0.0.0/8", "172.16.0.0/12", "127.0.0.1/32"]


def test_invalid_trusted_proxies_env_is_a_config_error(yaml_path, monkeypatch):
    monkeypatch.setenv("PULSE_TRUSTED_PROXIES", "10.0.0.0/8,bogus")

    with pytest.raises(ConfigError, match="PULSE_TRUSTED_PROXIES"):
        load_config(yaml_path)


def test_blank_env_values_do_not_override(yaml_path, monkeypatch):
    for name in ("PULSE_SECURE_COOKIES", "PULSE_DASHBOARD_URL", "PULSE_TRUSTED_PROXIES"):
        monkeypatch.setenv(name, "  ")

    config = load_config(yaml_path)

    assert config.dashboard.secure_cookies is False
    assert config.notify.dashboard_url == "https://yaml.example.test"
    assert config.dashboard.trusted_proxies == ["192.0.2.1"]


def test_env_overrides_apply_without_those_yaml_sections(tmp_path, monkeypatch):
    monkeypatch.setenv("PULSE_SECURE_COOKIES", "true")
    monkeypatch.setenv("PULSE_DASHBOARD_URL", "https://pulse.example.com")

    config = load_config(_write(tmp_path, {"organization": {"name": "WellPeps"}}))

    assert config.dashboard.secure_cookies is True
    assert config.notify.dashboard_url == "https://pulse.example.com"


def test_env_example_documents_the_overrides():
    from harvey.paths import PROJECT_ROOT

    text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    for name in ("PULSE_SECURE_COOKIES", "PULSE_DASHBOARD_URL", "PULSE_TRUSTED_PROXIES",
                 "PULSE_REQUIRE_POSTGRES"):
        assert name in text
