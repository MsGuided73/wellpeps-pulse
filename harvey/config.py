"""Configuration loader for WellPeps Pulse. Reads harvey.yaml + .env."""

import ipaddress
import logging
import os
from datetime import time as _time
from pathlib import Path

from urllib.parse import urlsplit

import yaml
from dotenv import dotenv_values, load_dotenv
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


# Triage is structured classification, where a small model is plenty.
# Drafting and the adversarial review need a stronger model; the reviewer
# may never run on haiku (enforced below).
# The safety screen asks one narrow yes/no question, so haiku is enough.
DEFAULT_MODELS = {
    "triager": "haiku",
    "safety": "haiku",
    "drafter": "sonnet",
    "reviewer": "sonnet",
    "pulse": "sonnet",   # daily/weekly market brief from aggregates
    # #pulse-query bot: haiku turns a question into a strict QuerySpec,
    # sonnet words the answer from the aggregate result only.
    "slackbot.plan": "haiku",
    "slackbot.answer": "sonnet",
}


def _default_models() -> dict[str, str]:
    return dict(DEFAULT_MODELS)


class UsageConfig(BaseModel):
    max_daily_claude_percent: float = 80.0
    heartbeat_interval_minutes: int = 15
    quiet_hours: QuietHoursConfig = Field(default_factory=QuietHoursConfig)
    # Per-agent model routing, passed to `claude --model`. Keys are an agent
    # ("triager") or an agent.task ("triager.classify"); the more specific key
    # wins. Unlisted calls use the CLI's default model. Set `models: {}` to
    # run everything on the default model.
    models: dict[str, str] = Field(default_factory=_default_models)

    @field_validator("models")
    @classmethod
    def _valid_models(cls, v: dict[str, str]) -> dict[str, str]:
        blank = [key for key, model in v.items() if not model.strip()]
        if blank:
            raise ValueError(f"models has an empty model name for: {', '.join(blank)}")
        weak_review = [
            key for key, model in v.items()
            if (key == "reviewer" or key.startswith("reviewer.")) and "haiku" in model.lower()
        ]
        if weak_review:
            raise ValueError(
                f"the adversarial reviewer must not run on haiku ({', '.join(weak_review)}); "
                "use sonnet or stronger"
            )
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

    # While any escalation is open the heartbeat wakes at least this often,
    # so SLA breaches are re-paged promptly (docs/PLAN.md decisions).
    urgent_tick_minutes: int = Field(default=5, ge=1)


# Escalation kinds (harvey/escalation.py derives one per urgent mention).
ESCALATION_KINDS = ("adverse_event", "legal", "privacy", "billing_fraud", "viral_negative")


def _default_owners() -> dict[str, str]:
    # adverse_event always goes to clinical_owner; the rest are named here.
    return {"legal": "", "privacy": "", "billing_fraud": "", "viral_negative": ""}


class EscalationConfig(BaseModel):
    # Named humans who get paged for adverse events / legal-regulatory
    # mentions. Blank until WellPeps names them (docs/PLAN.md §5).
    clinical_owner: str = ""
    backup_owner: str = ""
    sla_minutes: int = Field(default=15, ge=1)
    # kind -> owner for everything that isn't an adverse event. A blank owner
    # is allowed, but the Slack page then says UNASSIGNED.
    owners: dict[str, str] = Field(default_factory=_default_owners)

    @field_validator("owners")
    @classmethod
    def _known_kinds(cls, v: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(v) - set(ESCALATION_KINDS))
        if unknown:
            raise ValueError(
                f"unknown escalation kind(s) in owners: {', '.join(unknown)}; "
                f"use {', '.join(ESCALATION_KINDS)}"
            )
        return {kind: (owner or "").strip() for kind, owner in v.items()}


class TriageConfig(BaseModel):
    # Independent safety-screen pass on health-related mentions (a second,
    # narrow model call; see harvey/agents/safety_screen.py). Defense against
    # a prompt-injected triage answer suppressing an escalation. Keep on.
    safety_screen: bool = True


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_dashboard_url(url: str) -> str:
    """"" or an https:// URL, or http:// on a loopback host. Raises ValueError."""
    url = (url or "").strip()
    if not url:
        return ""
    if any(c.isspace() for c in url):
        raise ValueError("dashboard_url must not contain spaces")
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        parts.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError as exc:
        raise ValueError(f"dashboard_url '{url}' is not a valid URL") from exc
    if not host:
        raise ValueError(f"dashboard_url '{url}' has no host")
    if parts.scheme == "https":
        return url
    if parts.scheme == "http" and _is_loopback_host(host):
        return url
    raise ValueError(
        f"dashboard_url '{url}' must start with https:// (http:// is only allowed for localhost)"
    )


class NotifyConfig(BaseModel):
    # Name of the env var holding the Slack webhook (never the URL itself).
    # Escalation pages (#pulse-alerts) use this one.
    slack_webhook_env: str = "SLACK_WEBHOOK_URL"
    # Env var holding the briefs webhook (#pulse-briefs). When that variable
    # is unset, briefs fall back to slack_webhook_env.
    slack_briefs_webhook_env: str = "SLACK_BRIEFS_WEBHOOK_URL"
    # Optional base URL of the dashboard, linked from Slack pages.
    # Env PULSE_DASHBOARD_URL overrides it.
    dashboard_url: str = ""

    @field_validator("dashboard_url")
    @classmethod
    def _valid_dashboard_url(cls, v: str) -> str:
        return check_dashboard_url(v)


class DashboardConfig(BaseModel):
    # Set true whenever the dashboard is served over HTTPS (any non-loopback
    # deployment): the session cookie then carries the Secure flag.
    # Env PULSE_SECURE_COOKIES overrides it.
    secure_cookies: bool = False
    # Idle session lifetime; every authenticated request slides it forward.
    session_hours: int = Field(default=12, ge=1, le=24 * 30)
    # Reverse proxies (IPs/CIDRs) whose X-Forwarded-For is believed when
    # finding the client IP for the login throttle (harvey/netutil.py).
    # Empty = never read X-Forwarded-For. Env PULSE_TRUSTED_PROXIES
    # (comma-separated) overrides it.
    trusted_proxies: list[str] = Field(default_factory=list)

    @field_validator("trusted_proxies")
    @classmethod
    def _valid_proxies(cls, v: list[str]) -> list[str]:
        from harvey.netutil import parse_networks

        cleaned = [str(item).strip() for item in v]
        parse_networks(cleaned)  # raises ValueError on garbage
        return cleaned


class ReviewConfig(BaseModel):
    # Re-run the adversarial reviewer (a Claude call) on every human edit.
    # Off by default: the deterministic compliance filter always re-runs.
    rerun_reviewer_on_edit: bool = False
    # Approval needs every cited claim to be publishable (signed off, not
    # expired) in config/claims.yaml. Keep true in production.
    require_publishable_claims: bool = True


WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class PulseBriefConfig(BaseModel):
    """Pulse trends and briefs (Phase 8). Times are in the quiet-hours timezone."""

    # The daily brief (previous local day) runs once, after this local hour.
    daily_brief_hour: int = Field(default=7, ge=0, le=23)
    # The weekly brief (previous Mon-Sun) runs from this day on, after the daily.
    weekly_day: str = "monday"
    top_terms: int = Field(default=25, ge=1, le=100)
    baseline_days: int = Field(default=28, ge=1, le=365)
    # >= 2: a term from a single post is an anecdote and could point at its author.
    min_count: int = Field(default=3, ge=2)

    @field_validator("weekly_day")
    @classmethod
    def _valid_weekday(cls, v: str) -> str:
        day = (v or "").strip().lower()
        if day not in WEEKDAYS:
            raise ValueError(f"weekly_day must be one of {', '.join(WEEKDAYS)}")
        return day


class SlackQueryConfig(BaseModel):
    """Limits for the read-only #pulse-query bot (harvey/slackbot/). Only
    answered questions count; refusals don't."""

    # Answered questions per local day (usage.quiet_hours.timezone), all users.
    daily_limit: int = Field(default=100, ge=1, le=10000)
    # Answered questions per Slack user in any rolling hour.
    per_user_per_hour: int = Field(default=20, ge=1, le=1000)


class PulseConfig(BaseModel):
    # Unknown keys fail loudly, so a leftover sales-era harvey.yaml is caught.
    model_config = ConfigDict(extra="forbid")

    organization: OrganizationConfig = Field(default_factory=OrganizationConfig)
    usage: UsageConfig = Field(default_factory=UsageConfig)
    triage: TriageConfig = Field(default_factory=TriageConfig)
    escalation: EscalationConfig = Field(default_factory=EscalationConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    pulse: PulseBriefConfig = Field(default_factory=PulseBriefConfig)
    slack_query: SlackQueryConfig = Field(default_factory=SlackQueryConfig)
    retention_days: int = Field(default=180, ge=1)


# Backward-compatible name for the config root.
HarveyConfig = PulseConfig


class EnvConfig(BaseModel):
    """Optional secrets from the environment / .env. All default to blank.

    Secret fields are excluded from repr so they never land in logs.
    """

    anthropic_api_key: str = Field(default="", repr=False)
    slack_webhook_url: str = Field(default="", repr=False)
    slack_briefs_webhook_url: str = Field(default="", repr=False)
    slack_bot_token: str = Field(default="", repr=False)
    slack_app_token: str = Field(default="", repr=False)
    slack_query_channel_id: str = ""
    apify_token: str = Field(default="", repr=False)
    meta_access_token: str = Field(default="", repr=False)
    pulse_admin_email: str = ""
    pulse_admin_password: str = Field(default="", repr=False)


# EnvConfig field -> environment variable.
_ENV_KEYS = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "slack_webhook_url": "SLACK_WEBHOOK_URL",
    "slack_briefs_webhook_url": "SLACK_BRIEFS_WEBHOOK_URL",
    "slack_bot_token": "SLACK_BOT_TOKEN",
    "slack_app_token": "SLACK_APP_TOKEN",
    "slack_query_channel_id": "SLACK_QUERY_CHANNEL_ID",
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


_TRUE = frozenset({"true", "1", "yes"})
_FALSE = frozenset({"false", "0", "no"})

# Deployment overrides (env wins over harvey.yaml). Blank means "not set".
SECURE_COOKIES_ENV = "PULSE_SECURE_COOKIES"
DASHBOARD_URL_ENV = "PULSE_DASHBOARD_URL"
TRUSTED_PROXIES_ENV = "PULSE_TRUSTED_PROXIES"


def parse_bool(raw: str, name: str) -> bool:
    """true/false/1/0/yes/no, any case. Anything else is a ConfigError naming ``name``."""
    value = (raw or "").strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{name} must be true/false (or 1/0, yes/no), got '{raw.strip()}'.")


def env_setting(name: str) -> str:
    """An env var, stripped. The environment wins, even when set to "" (tests
    pin blanks that way); otherwise the repo's .env is read without touching
    os.environ."""
    if name in os.environ:
        return os.environ[name].strip()
    env_file = PROJECT_ROOT / ".env"
    if not env_file.is_file():
        return ""
    return (dotenv_values(env_file).get(name) or "").strip()


def _env_overrides(data: dict) -> dict:
    """A copy of the YAML mapping with the deployment env overrides applied."""
    from harvey.netutil import parse_networks

    notify = dict(data.get("notify") or {})
    dashboard = dict(data.get("dashboard") or {})

    raw = env_setting(SECURE_COOKIES_ENV)
    if raw:
        dashboard["secure_cookies"] = parse_bool(raw, SECURE_COOKIES_ENV)

    raw = env_setting(DASHBOARD_URL_ENV)
    if raw:
        try:
            notify["dashboard_url"] = check_dashboard_url(raw)
        except ValueError as exc:
            raise ConfigError(f"{DASHBOARD_URL_ENV}: {exc}")

    raw = env_setting(TRUSTED_PROXIES_ENV)
    if raw:
        try:
            parse_networks(raw)
        except ValueError as exc:
            raise ConfigError(f"{TRUSTED_PROXIES_ENV}: {exc}")
        dashboard["trusted_proxies"] = [item.strip() for item in raw.split(",")]

    merged = dict(data)
    if notify or "notify" in data:
        merged["notify"] = notify
    if dashboard or "dashboard" in data:
        merged["dashboard"] = dashboard
    return merged


def load_config(config_path: str | None = None) -> PulseConfig:
    """Load configuration from YAML, then apply the deployment env overrides
    (PULSE_SECURE_COOKIES, PULSE_DASHBOARD_URL, PULSE_TRUSTED_PROXIES).

    Raises ConfigError with a clear message for file/YAML problems and bad
    env overrides, and pydantic's ValidationError for schema problems.
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

    data = _env_overrides(data)
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
