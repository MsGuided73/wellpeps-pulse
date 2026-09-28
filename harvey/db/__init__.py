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

from harvey.paths import PROJECT_ROOT

DATABASE_URL_ENV = "PULSE_DATABASE_URL"
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


def integrity_errors() -> tuple[type[BaseException], ...]:
    """Unique/foreign-key violations on either backend."""
    errors: list[type[BaseException]] = [sqlite3.IntegrityError]
    try:
        import asyncpg
    except ImportError:  # pragma: no cover
        return tuple(errors)
    errors.append(asyncpg.IntegrityConstraintViolationError)
    return tuple(errors)
