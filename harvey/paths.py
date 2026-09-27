"""Canonical filesystem locations for the WellPeps Pulse checkout.

Pulse is repo-centric: config (harvey.yaml, .env), knowledge (prompts/,
skills/), and state (data/) all live next to the package. ``resolve()``
matters — when the package is reached through a symlink (some editable
installs), a bare ``Path(__file__).parent`` would point into
site-packages and Pulse would silently read/write the wrong files.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# SQLite database. PULSE_DB_PATH overrides the default (containers, tests).
DB_PATH = Path(
    os.environ.get("PULSE_DB_PATH", "").strip() or PROJECT_ROOT / "data" / "pulse.db"
)
