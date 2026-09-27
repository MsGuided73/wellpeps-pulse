"""Configuration loader for WellPeps Pulse. Reads harvey.yaml + .env."""

import logging
import os
from datetime import time as _time
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from harvey.paths import PROJECT_ROOT

logger = logging.getLogger("harvey.config")


class ConfigError(Exception):
    """Raised when the configuration is missing or invalid."""


class ConfigFileNotFoundError(ConfigError, FileNotFoundError):
    """Config file is missing. Subclasses FileNotFoundError for
    backward compatibility with existing callers."""


class OrganizationConfig(BaseModel):
    name: str = "WellPeps"
    market: str = "US"


class QuietHoursConfig(BaseModel):
    start: str = "22:00"
    end: str = "07:00"
    timezone: str = "America/New_York"

    @field_validator("start", "end")
    @classmethod
    def _valid_time(cls, v: str) -> str:
        try:
            _time.fromisoformat(v)
        except ValueError:
            raise ValueError(
                f"'{v}' is not a valid time. Use 24h HH:MM format, e.g. '22:00'."
            )
        return v

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, v: str) -> str:
        import pytz

        if v not in pytz.all_timezones_set:
            raise ValueError(
                f"'{v}' is not a valid timezone. Use an IANA name like 'America/New_York'."
            )
        return v


# Structured-output calls where a small model is plenty. Drafting (added in a
# later phase) stays on the default, stronger model.
DEFAULT_MODELS = {
    "triager": "haiku",
}


class UsageConfig(BaseModel):
    max_daily_claude_percent: float = 80.0
    heartbeat_interval_minutes: int = 15
    quiet_hours: QuietHoursConfig = QuietHoursConfig()
    # Per-agent model routing, passed to `claude --model`. Keys are an agent
    # ("triager") or an agent.task ("triager.classify"); the more specific key
    # wins. Unlisted calls use the CLI's default model. Set `models: {}` to
    # run everything on the default model.
    models: dict[str, str] = DEFAULT_MODELS

    @field_validator("models")
    @classmethod
    def _valid_models(cls, v: dict[str, str]) -> dict[str, str]:
        blank = [key for key, model in v.items() if not model.strip()]
        if blank:
            raise ValueError(f"models has an empty model name for: {', '.join(blank)}")
        return {key: model.strip() for key, model in v.items()}

    @field_validator("max_daily_claude_percent")
    @classmethod
    def _valid_percent(cls, v: float) -> float:
        if not 0 < v <= 100:
            raise ValueError("max_daily_claude_percent must be between 0 and 100")
        return v

    @field_validator("heartbeat_interval_minutes")
    @classmethod
    def _valid_interval(cls, v: int) -> int:
        if v < 1:
            raise ValueError("heartbeat_interval_minutes must be at least 1")
        return v


class EscalationConfig(BaseModel):
    # Named humans who get paged for adverse events / legal-regulatory
    # mentions. Blank until WellPeps names them (docs/PLAN.md §5).
    clinical_owner: str = ""
    backup_owner: str = ""
    sla_minutes: int = Field(default=15, ge=1)


class NotifyConfig(BaseModel):
    # Name of the env var holding the Slack webhook (never the URL itself).
    slack_webhook_env: str = "SLACK_WEBHOOK_URL"


class PulseConfig(BaseModel):
    # Unknown keys fail loudly, so a leftover sales-era harvey.yaml is caught.
    model_config = ConfigDict(extra="forbid")

    organization: OrganizationConfig = OrganizationConfig()
    usage: UsageConfig = UsageConfig()
    escalation: EscalationConfig = EscalationConfig()
    notify: NotifyConfig = NotifyConfig()
    retention_days: int = Field(default=180, ge=1)


# Backward-compatible name for the config root.
HarveyConfig = PulseConfig


class EnvConfig(BaseModel):
    """Optional secrets from the environment / .env. All default to blank.

    Secret fields are excluded from repr so they never land in logs.
    """

    anthropic_api_key: str = Field(default="", repr=False)
    slack_webhook_url: str = Field(default="", repr=False)
    apify_token: str = Field(default="", repr=False)
    meta_access_token: str = Field(default="", repr=False)
    pulse_admin_email: str = ""
    pulse_admin_password: str = Field(default="", repr=False)


# EnvConfig field -> environment variable.
_ENV_KEYS = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "slack_webhook_url": "SLACK_WEBHOOK_URL",
    "apify_token": "APIFY_TOKEN",
    "meta_access_token": "META_ACCESS_TOKEN",
    "pulse_admin_email": "PULSE_ADMIN_EMAIL",
    "pulse_admin_password": "PULSE_ADMIN_PASSWORD",
}


def _format_validation_error(e: ValidationError) -> str:
    """Turn a pydantic ValidationError into a readable, actionable message."""
    lines = []
    for err in e.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)


def load_config(config_path: str | None = None) -> PulseConfig:
    """Load configuration from YAML.

    Raises ConfigError with a clear message for file/YAML problems and
    pydantic's ValidationError for schema problems.
    """
    if config_path is None:
        config_path = _find_config_file()

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        raise ConfigFileNotFoundError(f"Config file not found: {config_path}.")
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {config_path}:\n  {e}")
    except OSError as e:
        raise ConfigError(f"Could not read {config_path}: {e}")

    if data is None:
        raise ConfigError(f"{config_path} is empty.")
    if not isinstance(data, dict):
        raise ConfigError(
            f"{config_path} must contain a YAML mapping (key: value pairs), "
            f"got {type(data).__name__}."
        )

    try:
        return PulseConfig(**data)
    except ValidationError as e:
        # Log a friendly summary, then re-raise the original ValidationError
        # so callers (and tests) keep the pydantic type.
        logger.error(
            f"Invalid configuration in {config_path}:\n{_format_validation_error(e)}"
        )
        raise


def load_env() -> EnvConfig:
    """Load optional secrets from the environment (and .env if present)."""
    load_dotenv()
    return EnvConfig(**{
        field: os.getenv(var, "").strip() for field, var in _ENV_KEYS.items()
    })


def _find_config_file() -> str:
    """Search for the config.

    ``harvey.local.yaml`` wins when present. It is gitignored, so a
    deployment can carry real owner names without committing them.
    """
    candidates = [
        Path.cwd() / "harvey.local.yaml",
        PROJECT_ROOT / "harvey.local.yaml",
        Path.cwd() / "harvey.yaml",
        PROJECT_ROOT / "harvey.yaml",
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    raise ConfigFileNotFoundError(
        "harvey.yaml not found in "
        + ", ".join(sorted({str(p.parent) for p in candidates}))
        + "."
    )
