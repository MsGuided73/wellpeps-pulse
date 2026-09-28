"""Dashboard authentication and authorization (Phase 7).

- Passwords: argon2id via argon2-cffi (``PasswordHasher`` defaults: 64 MiB,
  3 passes). Minimum length 12. Hashes are upgraded on login when the
  parameters change.
- Sessions: a random 32-byte token lives only in the browser's HttpOnly,
  SameSite=Strict cookie. The DB keeps sha256(token) as the row id, a
  per-session CSRF token, and a sliding ``expires_at``.
- CSRF: double-submit. ``/api/me`` hands the session's CSRF token to the
  page; every state-changing request must echo it in ``X-CSRF-Token``.
- Login throttling: in-memory, per email and per client IP, 5 failures in
  15 minutes -> 429. Process-local by design (one dashboard process). Behind
  a proxy the client IP comes from harvey/netutil.py (trusted proxies only).
- Roles: viewer < reviewer; clinical and admin see ``PERMISSIONS``.
- Passwords change in three ways: a user changes their own (current password
  required; failures count toward the login throttle; every other session
  ends and the current one gets a new CSRF token), an admin resets someone
  else's (all their sessions end), or ``pulse user reset-password``. A reset,
  and a user an admin creates, set ``must_change_password``: until the
  holder picks their own password the dashboard serves only /api/me,
  /api/me/password and /api/logout, so only the account holder knows the
  password they use. The env-var bootstrap admin (PULSE_ADMIN_EMAIL /
  PULSE_ADMIN_PASSWORD) is *not* flagged: the operator chose that password
  and keeps it in their own secret store (nobody handed it to them), and a
  headless first deploy should not open on a forced screen. Password events
  go to the ``actions`` table (``password_changed`` / ``password_reset``),
  never with password material.
"""

import hashlib
import hmac
import ipaddress
import logging
import secrets
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Callable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from harvey.db import integrity_errors

logger = logging.getLogger("harvey.auth")

ROLES = ("viewer", "reviewer", "clinical", "admin")
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024
DEFAULT_SESSION_HOURS = 12
TOKEN_BYTES = 32
MAX_LOGIN_FAILURES = 5
LOGIN_WINDOW_SECONDS = 15 * 60
MAX_TRACKED_KEYS = 10000   # prune expired limiter keys past this many

# What each role may do. "ack_adverse" is the clinical gate: adverse-event
# escalations belong to the clinical owner (or an admin).
PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset({"view"}),
    "reviewer": frozenset({"view", "review", "ack"}),
    "clinical": frozenset({"view", "ack_adverse"}),
    "admin": frozenset({"view", "review", "ack", "ack_adverse", "admin"}),
}
ADVERSE_KIND = "adverse_event"

HASHER = PasswordHasher()
_DUMMY_HASH: str | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- Roles ----------------------------------------------------------------------------


def can(role: str, permission: str) -> bool:
    return permission in PERMISSIONS.get(role, frozenset())


def can_ack(role: str, kind: str) -> bool:
    """Clinical acks adverse events only; reviewers ack the rest; admin acks any."""
    return can(role, "ack_adverse") if kind == ADVERSE_KIND else can(role, "ack")


# --- Passwords --------------------------------------------------------------------------


def check_password_policy(password: str) -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"password must be at most {MAX_PASSWORD_LENGTH} characters")


class PasswordPolicyError(ValueError):
    """A rejected new password; ``code`` is stable for the UI (labels.js)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class WrongPasswordError(Exception):
    """The current password did not verify. Deliberately carries no detail."""


def check_new_password(password: str, *, email: str = "", current: str | None = None) -> None:
    """Policy for a password being set: length, not the email, not the current one."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError("password_too_short",
                                  f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError("password_too_long",
                                  f"password must be at most {MAX_PASSWORD_LENGTH} characters")
    if email and password.strip().lower() == email.strip().lower():
        raise PasswordPolicyError("password_is_email", "password must not be the account's email address")
    if current is not None and hmac.compare_digest(password.encode("utf-8"), current.encode("utf-8")):
        raise PasswordPolicyError("password_reused", "new password must differ from the current one")


def hash_password(password: str) -> str:
    check_password_policy(password)
    return HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Constant-time (argon2) verify; any malformed input is simply False."""
    try:
        return HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError, TypeError, ValueError):
        return False


def _burn_verify(password: str) -> None:
    """Spend a verify's worth of time for an unknown email (no user oracle)."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = HASHER.hash(secrets.token_hex(16))
    verify_password(_DUMMY_HASH, password or "")


def normalize_email(email: str) -> str:
    email = (email or "").strip().lower()
    if "@" not in email or len(email) > 254 or any(c.isspace() for c in email):
        raise ValueError("a valid email address is required")
    return email


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_match(expected: str, given: str) -> bool:
    return bool(expected) and bool(given) and hmac.compare_digest(
        expected.encode("utf-8"), given.encode("utf-8"))


# --- Login rate limit ----------------------------------------------------------------------


class LoginRateLimiter:
    """Sliding-window failure counter per key ("email:x", "ip:y")."""

    def __init__(self, max_failures: int = MAX_LOGIN_FAILURES,
                 window_seconds: float = LOGIN_WINDOW_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self.max_failures = max_failures
        self.window = window_seconds
        self.clock = clock
        self._failures: dict[str, deque[float]] = {}

    def _recent(self, key: str) -> deque[float]:
        entries = self._failures.get(key)
        if entries is None:
            return deque()
        cutoff = self.clock() - self.window
        while entries and entries[0] <= cutoff:
            entries.popleft()
        if not entries:
            self._failures.pop(key, None)
        return entries

    def is_blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= self.max_failures

    def record_failure(self, key: str) -> None:
        if len(self._failures) >= MAX_TRACKED_KEYS:
            for stale in list(self._failures):
                self._recent(stale)  # drops keys whose window has passed
        self._recent(key)
        self._failures.setdefault(key, deque()).append(self.clock())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)

    def clear(self) -> None:
        self._failures.clear()


# --- Users and sessions ------------------------------------------------------------------------

_USER_FIELDS = ("id, email, display_name, role, active, created_at, last_login_at, "
                "must_change_password, password_changed_at")


def _public(row) -> dict:
    user = {k: row[k] for k in row.keys() if k != "password_hash"}
    user["active"] = bool(user.get("active"))
    user["must_change_password"] = bool(user.get("must_change_password"))
    return user


class AuthStore:
    """Users and sessions on top of a StateManager's database."""

    def __init__(self, state):
        self.state = state

    async def _fetchone(self, sql: str, params: tuple = ()):
        async with self.state.connect() as db:
            async with db.execute(sql, params) as cursor:
                return await cursor.fetchone()

    async def _write(self, sql: str, params: tuple = ()) -> int:
        async with self.state.connect() as db:
            cursor = await db.execute(sql, params)
            await db.commit()
            return cursor.rowcount

    # users

    async def create_user(self, email: str, password: str, role: str, name: str = "",
                          must_change_password: bool = False) -> int:
        """Create an active user. Admin-facing callers (the Users tab, ``pulse
        user add``) pass ``must_change_password=True`` unless opted out."""
        email = normalize_email(email)
        if role not in ROLES:
            raise ValueError(f"unknown role '{role}'; use one of {', '.join(ROLES)}")
        check_new_password(password, email=email)
        password_hash = hash_password(password)
        async with self.state.connect() as db:
            try:
                cursor = await db.execute(
                    "INSERT INTO users (email, display_name, role, password_hash, active, "
                    "must_change_password) VALUES (?, ?, ?, ?, TRUE, ?) RETURNING id",
                    (email, (name or "").strip()[:120], role, password_hash, bool(must_change_password)),
                )
            except integrity_errors() as exc:
                raise ValueError(f"a user with email {email} already exists") from exc
            (user_id,) = await cursor.fetchone()
            await db.commit()
            return user_id

    async def get_user(self, email: str) -> dict | None:
        try:
            email = normalize_email(email)
        except ValueError:
            return None
        row = await self._fetchone(f"SELECT {_USER_FIELDS} FROM users WHERE email = ?", (email,))
        return _public(row) if row else None

    async def list_users(self) -> list[dict]:
        async with self.state.connect() as db:
            async with db.execute(f"SELECT {_USER_FIELDS} FROM users ORDER BY email") as cursor:
                return [_public(r) for r in await cursor.fetchall()]

    async def count_users(self) -> int:
        row = await self._fetchone("SELECT COUNT(*) AS n FROM users")
        return row["n"]

    async def count_active_admins(self) -> int:
        row = await self._fetchone("SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND active = TRUE")
        return row["n"]

    async def disable_user(self, email: str) -> bool:
        """Deactivate a user and end every session they hold."""
        user = await self.get_user(email)
        if user is None:
            return False
        await self._write("UPDATE users SET active = FALSE WHERE id = ?", (user["id"],))
        await self._write("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
        return True

    async def verify_user_password(self, user_id: int, password: str) -> bool:
        """Whether ``password`` is this active user's current one (argon2, constant-time)."""
        row = await self._fetchone("SELECT password_hash, active FROM users WHERE id = ?", (int(user_id),))
        if row is None or not row["password_hash"]:
            _burn_verify(password)
            return False
        return verify_password(row["password_hash"], password or "") and bool(row["active"])

    async def set_password(self, user_id: int, password: str, *, must_change: bool) -> None:
        """Store a new argon2id hash (the caller has checked the policy)."""
        await self._write(
            "UPDATE users SET password_hash = ?, must_change_password = ?, password_changed_at = ? "
            "WHERE id = ?",
            (hash_password(password), bool(must_change), _utcnow().isoformat(), int(user_id)),
        )

    async def revoke_sessions(self, user_id: int, keep_token: str = "") -> int:
        """End the user's sessions, except the one behind ``keep_token``."""
        if keep_token:
            return await self._write("DELETE FROM sessions WHERE user_id = ? AND id <> ?",
                                     (int(user_id), token_digest(keep_token)))
        return await self._write("DELETE FROM sessions WHERE user_id = ?", (int(user_id),))

    async def rotate_csrf(self, token: str) -> str:
        """A fresh CSRF token for the session behind ``token``."""
        csrf = secrets.token_urlsafe(TOKEN_BYTES)
        await self._write("UPDATE sessions SET csrf_token = ? WHERE id = ?", (csrf, token_digest(token)))
        return csrf

    async def change_own_password(self, user: dict, current: str, new: str, keep_token: str) -> str:
        """Self-service change; returns the kept session's new CSRF token.

        Raises ``WrongPasswordError`` (generic) or ``PasswordPolicyError``. The
        current password is verified before the policy runs, so the policy's
        "must differ" answer never confirms a guess.
        """
        if not await self.verify_user_password(user["id"], current):
            raise WrongPasswordError()
        check_new_password(new, email=user["email"], current=current)
        # Other sessions end first: a failure after this point never leaves
        # them alive under the new password.
        ended = await self.revoke_sessions(user["id"], keep_token=keep_token)
        await self.set_password(user["id"], new, must_change=False)
        csrf = await self.rotate_csrf(keep_token)
        await self._log_event("password_changed", "dashboard", {
            "email": user["email"], "by": user["email"], "other_sessions_ended": ended})
        return csrf

    async def _log_event(self, action_type: str, agent: str, details: dict) -> None:
        """Record a password event in ``actions``; the change itself already
        happened, so a logging failure is reported, not raised."""
        try:
            await self.state.log_action(action_type, agent, details)
        except Exception as exc:
            logger.error("could not record %s for %s (%s)", action_type,
                         details.get("email", "?"), type(exc).__name__)

    async def reset_password(self, email: str, new: str, *, by: str, agent: str,
                             must_change: bool = True) -> dict:
        """Admin/CLI reset: new password, every session ended, change forced by default.

        Raises LookupError (no such user) or ``PasswordPolicyError``.
        """
        target = await self.get_user(email)
        if target is None:
            raise LookupError("no such user")
        check_new_password(new, email=target["email"])
        await self.set_password(target["id"], new, must_change=must_change)
        # Even if this delete failed, their sessions are gated by the flag
        # (must_change) until someone who knows the new password changes it.
        ended = await self.revoke_sessions(target["id"])
        await self._log_event("password_reset", agent, {
            "email": target["email"], "by": by, "must_change_password": bool(must_change),
            "sessions_ended": ended})
        return target

    async def authenticate(self, email: str, password: str) -> dict | None:
        """The active user for these credentials, else None (timing-flat)."""
        try:
            email = normalize_email(email)
        except ValueError:
            _burn_verify(password)
            return None
        row = await self._fetchone(
            f"SELECT {_USER_FIELDS}, password_hash FROM users WHERE email = ?", (email,))
        if row is None or not row["password_hash"]:
            _burn_verify(password)
            return None
        if not verify_password(row["password_hash"], password or "") or not row["active"]:
            return None
        if HASHER.check_needs_rehash(row["password_hash"]):
            await self._write("UPDATE users SET password_hash = ? WHERE id = ?",
                              (HASHER.hash(password), row["id"]))
        await self._write("UPDATE users SET last_login_at = ? WHERE id = ?",
                          (_utcnow().isoformat(), row["id"]))
        return _public(row)

    # sessions

    async def create_session(self, user_id: int, hours: int = DEFAULT_SESSION_HOURS) -> tuple[str, str]:
        """(token for the cookie, csrf token). Only sha256(token) is stored."""
        token = secrets.token_urlsafe(TOKEN_BYTES)
        csrf = secrets.token_urlsafe(TOKEN_BYTES)
        now = _utcnow()
        await self._write(
            "INSERT INTO sessions (id, user_id, created_at, expires_at, last_seen_at, csrf_token) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (token_digest(token), int(user_id), now.isoformat(),
             (now + timedelta(hours=hours)).isoformat(), now.isoformat(), csrf),
        )
        return token, csrf

    async def session_user(self, token: str, hours: int = DEFAULT_SESSION_HOURS,
                           now: datetime | None = None) -> dict | None:
        """The user behind a live session (sliding its expiry), else None.

        Expired sessions and sessions of disabled users are deleted.
        """
        if not token or len(token) > 256:
            return None
        digest = token_digest(token)
        row = await self._fetchone(
            f"SELECT s.expires_at, s.csrf_token, u.id, u.email, u.display_name, u.role, u.active, "
            f"u.must_change_password "
            f"FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.id = ?",
            (digest,),
        )
        if row is None:
            return None
        now = now or _utcnow()
        if not row["active"] or datetime.fromisoformat(str(row["expires_at"])) <= now:
            await self._write("DELETE FROM sessions WHERE id = ?", (digest,))
            return None
        await self._write(
            "UPDATE sessions SET expires_at = ?, last_seen_at = ? WHERE id = ?",
            ((now + timedelta(hours=hours)).isoformat(), now.isoformat(), digest),
        )
        return {"id": row["id"], "email": row["email"], "name": row["display_name"] or "",
                "role": row["role"], "csrf": row["csrf_token"] or "",
                "must_change_password": bool(row["must_change_password"])}

    async def delete_session(self, token: str) -> None:
        if token:
            await self._write("DELETE FROM sessions WHERE id = ?", (token_digest(token),))

    async def purge_expired_sessions(self) -> int:
        return await self._write("DELETE FROM sessions WHERE expires_at <= ?", (_utcnow().isoformat(),))


# --- Bootstrap and bind guard ---------------------------------------------------------------------


async def bootstrap_admin(store: AuthStore, email: str, password: str) -> str:
    """Create the first admin from PULSE_ADMIN_EMAIL/PASSWORD when no users exist."""
    if not (email or "").strip() or not password:
        return ""
    if await store.count_users() > 0:
        return ""
    try:
        await store.create_user(email, password, "admin", name="Admin")
    except ValueError as exc:
        return f"admin bootstrap refused: {exc}"
    return f"admin bootstrap: created admin user {normalize_email(email)}"


def is_loopback(host: str) -> bool:
    host = (host or "").strip().strip("[]")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def bind_error(host: str, active_admins: int, secure_cookies: bool = False) -> str | None:
    """Why the dashboard may not bind to ``host``, or None if it may."""
    if is_loopback(host):
        return None
    if not secure_cookies:
        return (
            f"Refusing to bind to {host} without dashboard.secure_cookies: true: the "
            "session cookie would travel without TLS. Put the dashboard behind HTTPS "
            "and set dashboard.secure_cookies: true in harvey.yaml (or env "
            "PULSE_SECURE_COOKIES=true)."
        )
    if active_admins > 0:
        return None
    return (
        f"Refusing to bind to {host}: no active admin user exists, so nobody could "
        "manage access. Create one first with `pulse user add EMAIL --role admin "
        "--name NAME` (or set PULSE_ADMIN_EMAIL/PULSE_ADMIN_PASSWORD), and serve it "
        "over HTTPS with dashboard.secure_cookies: true."
    )
