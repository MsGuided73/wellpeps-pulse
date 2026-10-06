"""Local DEMO sandbox: a fictional community site for end-to-end reply demos.

For client demos only. Every demo mention's permalink points at a thread in
this sandbox ("Demo Forum", "Demo Photos", "Demo Reviews"), so the presenter
can copy an approved reply from the Review desk, post it here as the brand
account, and paste the comment link back into "Mark posted".

Guard (same style as ``dashboard.dev_no_auth``): the sandbox exists only when
``PULSE_DEMO_SANDBOX`` is true, never in containers (``PULSE_REQUIRE_POSTGRES``),
and only for loopback peers; otherwise every /sandbox route is a 404 and the
dashboard refuses a non-loopback bind. Content lives in its own SQLite file
(``PULSE_SANDBOX_DB_PATH``, default data/sandbox.db), never in the Pulse
database, so production needs no migration for it.

No real platform names, logos or trade dress; fictional handles only.
"""

from pathlib import Path

from harvey.paths import PROJECT_ROOT

SANDBOX_ENV = "PULSE_DEMO_SANDBOX"
SANDBOX_DB_ENV = "PULSE_SANDBOX_DB_PATH"
BRAND_ENV = "PULSE_SANDBOX_BRAND_HANDLE"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "sandbox.db"
DEFAULT_BRAND_HANDLE = "WellPeps_Team"
BRAND_FLAIR = "Brand · WellPeps"
BANNER = "DEMO SANDBOX — fictional content, not a real platform."
MAX_BODY_CHARS = 10000


def sandbox_enabled() -> bool:
    """PULSE_DEMO_SANDBOX is on and we are not in a container."""
    from harvey.config import env_setting, parse_bool
    from harvey.db import require_postgres

    raw = env_setting(SANDBOX_ENV)
    if not raw or not parse_bool(raw, SANDBOX_ENV):
        return False
    return not require_postgres()


def request_allowed(request) -> bool:
    """The sandbox is on and this request comes from this machine."""
    from harvey.auth import is_loopback

    peer = request.client.host if request.client else ""
    return sandbox_enabled() and is_loopback(peer)


def db_path() -> Path:
    from harvey.config import env_setting

    raw = env_setting(SANDBOX_DB_ENV)
    return Path(raw) if raw else DEFAULT_DB_PATH


def brand_handle() -> str:
    from harvey.config import env_setting

    return (env_setting(BRAND_ENV) or DEFAULT_BRAND_HANDLE)[:40]
