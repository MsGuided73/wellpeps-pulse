"""Database backends for WellPeps Pulse.

SQLite (``data/pulse.db`` / ``PULSE_DB_PATH``) is the default and what the
test suite runs on. Setting ``PULSE_DATABASE_URL`` to a ``postgresql://`` URL
(in the environment or the repo's ``.env``) switches ``StateManager()`` to
Postgres, the Supabase ``pulse`` schema. See db/postgres/README.md.
"""

import os
import sqlite3
from urllib.parse import urlsplit, urlunsplit

from dotenv import dotenv_values

from harvey.config import ConfigError, env_setting, parse_bool
from harvey.paths import PROJECT_ROOT

DATABASE_URL_ENV = "PULSE_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "PULSE_REQUIRE_POSTGRES"
ENV_FILE = PROJECT_ROOT / ".env"
_SCHEMES = ("postgres://", "postgresql://")


def is_postgres_url(url: str) -> bool:
    return (url or "").strip().lower().startswith(_SCHEMES)


def redact_url(url: str) -> str:
    """``url`` without its password, safe for logs and error messages."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        user = f"{parts.username}@" if parts.username else ""
        return urlunsplit((parts.scheme, f"{user}{host}", parts.path, "", ""))
    except ValueError:
        return "<unparseable database url>"


def dotenv_database_url() -> str:
    """PULSE_DATABASE_URL from the repo's .env file only (no environment)."""
    if not ENV_FILE.is_file():
        return ""
    return (dotenv_values(ENV_FILE).get(DATABASE_URL_ENV) or "").strip()


def env_database_url() -> str:
    """The configured Postgres URL, or "" for SQLite.

    The environment wins, even when set to "" (that is how tests pin SQLite);
    otherwise the repo's .env is read without modifying os.environ. Anything
    that isn't a postgres URL is an error rather than a silent SQLite fallback.
    """
    if DATABASE_URL_ENV in os.environ:
        url = os.environ[DATABASE_URL_ENV].strip()
    else:
        url = dotenv_database_url()
    if url and not is_postgres_url(url):
        raise ValueError(
            f"{DATABASE_URL_ENV} must be a postgresql:// URL (got scheme "
            f"'{url.split(':', 1)[0]}'); leave it blank to use SQLite."
        )
    return url


class PostgresRequiredError(ConfigError):
    """PULSE_REQUIRE_POSTGRES is on but no PULSE_DATABASE_URL is set."""


def require_postgres() -> bool:
    """True when PULSE_REQUIRE_POSTGRES (env or .env) says SQLite is not allowed."""
    raw = env_setting(REQUIRE_POSTGRES_ENV)
    return parse_bool(raw, REQUIRE_POSTGRES_ENV) if raw else False


def check_sqlite_allowed() -> None:
    """Refuse the env-selected SQLite fallback when Postgres is required.

    In a container the SQLite file would live in a throwaway layer and vanish
    on the next deploy, so fail loudly instead.
    """
    if require_postgres():
        raise PostgresRequiredError(
            f"{REQUIRE_POSTGRES_ENV} is on but {DATABASE_URL_ENV} is not set: refusing to "
            "write SQLite inside the container, where it would be lost on redeploy. Set "
            f"{DATABASE_URL_ENV} to the Supabase session-pooler URL (or set "
            f"{REQUIRE_POSTGRES_ENV}=false for local use)."
        )


def integrity_errors() -> tuple[type[BaseException], ...]:
    """Unique/foreign-key violations on either backend."""
    errors: list[type[BaseException]] = [sqlite3.IntegrityError]
    try:
        import asyncpg
    except ImportError:  # pragma: no cover
        return tuple(errors)
    errors.append(asyncpg.IntegrityConstraintViolationError)
    return tuple(errors)
