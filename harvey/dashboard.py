"""WellPeps Pulse dashboard — the review desk, urgent queue, feed, usage, and
heartbeat controls.

Security (Phase 7, see harvey/auth.py):
- Every /api route except /api/login needs a session cookie (401); every
  state-changing request also needs a matching X-CSRF-Token header (403).
  Role checks (403) come from ``auth.PERMISSIONS``.
- Binds to 127.0.0.1 by default. A public bind is refused until an active
  admin exists; serve it over HTTPS with ``dashboard.secure_cookies: true``.
- Security headers on every response: a strict CSP (no inline script or
  style), nosniff, DENY framing, no referrer.
- ``GET /healthz`` is public and says only {"ok": true|false} (200/503).
- The login throttle keys on the real client IP: X-Forwarded-For is
  believed only from ``dashboard.trusted_proxies`` (harvey/netutil.py).

Nothing here posts to any platform. "Copied" and "mark posted" only record
what a human did by hand.
"""

import asyncio
import logging
import os
import signal
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from harvey import analytics, auth, briefs, health, pulse_store, review, trends
from harvey.config import ConfigFileNotFoundError, PulseConfig, load_config, load_env
from harvey.escalation import ack as ack_escalation
from harvey.models import MentionStatus
from harvey.netutil import client_ip, networks_from_config
from harvey.paths import DB_PATH, PROJECT_ROOT
from harvey.state import StateManager, usage_windows

logger = logging.getLogger("harvey.dashboard")

LOOPBACK_HOST = "127.0.0.1"
PID_FILE = PROJECT_ROOT / "data" / "pulse.pid"
LOG_FILE = PROJECT_ROOT / "data" / "pulse.log"
MENTIONS_MAX_LIMIT = review.MAX_LIMIT
MENTION_PREVIEW_CHARS = review.PREVIEW_CHARS

SESSION_COOKIE = "pulse_session"
CSRF_HEADER = "X-CSRF-Token"
PUBLIC_API = frozenset({"/api/login"})
HEALTH_TIMEOUT_SECONDS = 5.0
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}

app = FastAPI(title="WellPeps Pulse", docs_url=None, redoc_url=None, openapi_url=None)
LOGIN_LIMITER = auth.LoginRateLimiter()

# Heartbeat process tracking
_harvey_process: subprocess.Popen | None = None
_harvey_started_at: datetime | None = None
_initialized: set[str] = set()
_config_cache: PulseConfig | None = None


# ── Dependencies (overridable in tests) ──


def get_config() -> PulseConfig:
    global _config_cache
    if _config_cache is None:
        try:
            _config_cache = load_config()
        except ConfigFileNotFoundError:
            _config_cache = PulseConfig()
    return _config_cache


def _config() -> PulseConfig:
    """Config outside FastAPI's DI (middleware), honoring test overrides."""
    return app.dependency_overrides.get(get_config, get_config)()


async def get_state() -> StateManager:
    """SQLite at DB_PATH, or Postgres when PULSE_DATABASE_URL is set."""
    state = StateManager.from_env(DB_PATH)
    if state.location not in _initialized:
        await state.init_db()
        _initialized.add(state.location)
    return state


def get_notifier(config: PulseConfig = Depends(get_config)):
    from harvey.notify import SlackNotifier

    return SlackNotifier.from_config(config)


def get_reviewer(config: PulseConfig = Depends(get_config), state: StateManager = Depends(get_state)):
    """The adversarial reviewer, only when edits should be re-reviewed."""
    if not config.review.rerun_reviewer_on_edit:
        return None
    from harvey.agents.reviewer import Reviewer
    from harvey.brain import Brain

    return Reviewer(Brain(state, models=config.usage.models))


def get_brief_brain(config: PulseConfig = Depends(get_config), state: StateManager = Depends(get_state)):
    """The brain the Pulse brief is written with (overridden in tests)."""
    from harvey.brain import Brain

    return Brain(state, models=config.usage.models)


def require(permission: str):
    def dependency(request: Request) -> dict:
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(status_code=401, detail="authentication required")
        if not auth.can(user["role"], permission):
            raise HTTPException(status_code=403, detail="your role does not allow this")
        return user
    return dependency


VIEW = Depends(require("view"))
REVIEW = Depends(require("review"))
ADMIN = Depends(require("admin"))


@app.exception_handler(review.ReviewError)
async def _review_error(request: Request, exc: review.ReviewError):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status)


# ── Session + security middleware ──


async def _session_user(request: Request) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    store = auth.AuthStore(await get_state())
    return await store.session_user(token, hours=_config().dashboard.session_hours)


def _deny(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in PUBLIC_API:
        user = await _session_user(request)
        if user is None:
            response = _deny(401, "authentication required")
        elif request.method not in SAFE_METHODS and not auth.tokens_match(
                user["csrf"], request.headers.get(CSRF_HEADER, "")):
            response = _deny(403, "missing or invalid CSRF token")
        else:
            request.state.user = user
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
    else:
        response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    return response


def request_ip(request: Request) -> str:
    """The client's IP for security decisions (login throttle).

    X-Forwarded-For is believed only from ``dashboard.trusted_proxies``
    (harvey/netutil.py); uvicorn's own proxy-header rewriting is off.
    """
    peer = request.client.host if request.client else ""
    xff = ",".join(request.headers.getlist("x-forwarded-for"))
    return client_ip(peer, xff, networks_from_config(_config().dashboard.trusted_proxies))


# ── Health (public, for the container healthcheck) ──


@app.get("/healthz")
async def healthz():
    """{"ok": true} when the database answers; 503 {"ok": false} otherwise.

    Unauthenticated, so it says nothing else: no error text, no counts.
    """
    async def _probe():
        await health.check_database(await get_state())

    try:
        await asyncio.wait_for(_probe(), timeout=HEALTH_TIMEOUT_SECONDS)
        ok = True
    except Exception as exc:
        logger.warning("healthz: database check failed (%s)", type(exc).__name__)
        ok = False
    return JSONResponse({"ok": ok}, status_code=200 if ok else 503,
                        headers={"Cache-Control": "no-store"})


# ── Auth routes ──


class LoginBody(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=auth.MAX_PASSWORD_LENGTH)


@app.post("/api/login")
async def login(body: LoginBody, request: Request):
    ip = request_ip(request)
    keys = (f"email:{body.email.strip().lower()}", f"ip:{ip}")
    if any(LOGIN_LIMITER.is_blocked(k) for k in keys):
        return _deny(429, "too many failed sign-in attempts; try again in 15 minutes")
    store = auth.AuthStore(await get_state())
    user = await store.authenticate(body.email, body.password)
    if user is None:
        for key in keys:
            LOGIN_LIMITER.record_failure(key)
        return _deny(401, "invalid credentials")
    LOGIN_LIMITER.reset(keys[0])
    config = _config()
    token, csrf = await store.create_session(user["id"], hours=config.dashboard.session_hours)
    response = JSONResponse({"email": user["email"], "name": user["display_name"] or "",
                             "role": user["role"], "csrf": csrf})
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict",
                        secure=config.dashboard.secure_cookies, path="/")
    return response


@app.post("/api/logout")
async def logout(request: Request, user: dict = VIEW):
    await auth.AuthStore(await get_state()).delete_session(request.cookies.get(SESSION_COOKIE, ""))
    response = JSONResponse({"success": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/api/me")
async def me(user: dict = VIEW):
    return {"email": user["email"], "name": user["name"], "role": user["role"], "csrf": user["csrf"]}


# ── Users (admin) ──


class NewUserBody(BaseModel):
    email: str = Field(max_length=254)
    name: str = Field(default="", max_length=120)
    role: str
    password: str = Field(max_length=auth.MAX_PASSWORD_LENGTH)


class EmailBody(BaseModel):
    email: str = Field(max_length=254)


@app.get("/api/users")
async def list_users(user: dict = ADMIN):
    return await auth.AuthStore(await get_state()).list_users()


@app.post("/api/users")
async def create_user(body: NewUserBody, user: dict = ADMIN):
    store = auth.AuthStore(await get_state())
    try:
        user_id = await store.create_user(body.email, body.password, body.role, name=body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    logger.info("user %s created by %s", body.email.strip().lower(), user["email"])
    return {"success": True, "id": user_id}


@app.post("/api/users/disable")
async def disable_user(body: EmailBody, user: dict = ADMIN):
    store = auth.AuthStore(await get_state())
    target = await store.get_user(body.email)
    if target is None:
        raise HTTPException(status_code=404, detail="no such user")
    if target["role"] == "admin" and target["active"] and await store.count_active_admins() <= 1:
        raise HTTPException(status_code=409, detail="cannot disable the last active admin")
    await store.disable_user(body.email)
    logger.info("user %s disabled by %s", target["email"], user["email"])
    return {"success": True}


# ── Review desk ──


class EditBody(BaseModel):
    text: str = Field(max_length=review.MAX_EDIT_CHARS * 2)
    claim_ids: list[str] = Field(default_factory=list, max_length=review.MAX_CLAIMS)


class ApproveBody(BaseModel):
    draft_id: int | None = None


class RejectBody(BaseModel):
    reason: str = Field(default="", max_length=review.MAX_REASON_CHARS)


class PostedBody(BaseModel):
    posted_url: str = Field(default="", max_length=2000)


class EscalateBody(BaseModel):
    kind: str = Field(max_length=40)


@app.get("/api/urgent")
async def get_urgent(user: dict = VIEW):
    return await review.urgent(await get_state())


@app.get("/api/mentions")
async def get_mentions(status: str | None = None, platform: str | None = None,
                       competitor: str | None = None, product: str | None = None,
                       drug: str | None = None, category: str | None = None,
                       urgency: str | None = None, q: str | None = None,
                       limit: int = 100, offset: int = 0, sort: str = "newest", user: dict = VIEW):
    """Mentions, newest first (or ``sort=oldest``), filtered and paginated (text is a preview)."""
    return await review.feed(
        await get_state(), status=status, platform=platform, competitor=competitor,
        product=product, drug=drug, category=category, urgency=urgency, q=q,
        limit=limit, offset=offset, sort=sort,
    )


@app.get("/api/mentions/{mention_id}")
async def get_mention_detail(mention_id: int, user: dict = VIEW,
                             config: PulseConfig = Depends(get_config)):
    return await review.detail(await get_state(), mention_id, config)


@app.get("/api/claims")
async def get_claims(user: dict = VIEW):
    return review.all_claims()


@app.post("/api/mentions/{mention_id}/edit")
async def edit_mention(mention_id: int, body: EditBody, user: dict = REVIEW,
                       config: PulseConfig = Depends(get_config), reviewer=Depends(get_reviewer)):
    outcome = await review.edit(await get_state(), mention_id, body.text, body.claim_ids,
                                user["email"], config, reviewer=reviewer)
    return {"success": True, **outcome.__dict__}


@app.post("/api/mentions/{mention_id}/approve")
async def approve_mention(mention_id: int, body: ApproveBody | None = None, user: dict = REVIEW,
                          config: PulseConfig = Depends(get_config)):
    await review.approve(await get_state(), mention_id, user["email"], config,
                         draft_id=body.draft_id if body else None)
    return {"success": True}


@app.post("/api/mentions/{mention_id}/reject")
async def reject_mention(mention_id: int, body: RejectBody, user: dict = REVIEW):
    await review.reject(await get_state(), mention_id, body.reason, user["email"])
    return {"success": True}


@app.post("/api/mentions/{mention_id}/copied")
async def copied_mention(mention_id: int, user: dict = REVIEW):
    await review.copied(await get_state(), mention_id, user["email"])
    return {"success": True}


@app.post("/api/mentions/{mention_id}/mark-posted")
async def mark_posted(mention_id: int, body: PostedBody | None = None, user: dict = REVIEW):
    await review.mark_posted(await get_state(), mention_id, user["email"],
                             body.posted_url if body else "")
    return {"success": True}


@app.post("/api/mentions/{mention_id}/escalate")
async def escalate_mention(mention_id: int, body: EscalateBody, user: dict = REVIEW,
                           config: PulseConfig = Depends(get_config), notifier=Depends(get_notifier)):
    escalation = await review.manual_escalate(await get_state(), notifier, mention_id, body.kind,
                                              user["email"], config)
    return {"success": True, "escalation_id": escalation.id,
            "paged": escalation.notified_at is not None}


@app.post("/api/escalations/{escalation_id}/ack")
async def ack(escalation_id: int, user: dict = VIEW):
    state = await get_state()
    escalation = await state.get_escalation(escalation_id)
    if escalation is None:
        raise HTTPException(status_code=404, detail="escalation not found")
    if not auth.can_ack(user["role"], escalation.kind):
        who = "clinical or admin" if escalation.kind == auth.ADVERSE_KIND else "reviewer or above"
        raise HTTPException(status_code=403, detail=f"only {who} can acknowledge this escalation")
    if not await ack_escalation(state, escalation_id, user["email"]):
        raise HTTPException(status_code=409, detail="already acknowledged")
    return {"success": True}


# ── Pulse: briefs, live trends, language bank ──

Period = Literal["daily", "weekly"]


class GenerateBriefBody(BaseModel):
    period: Period
    force: bool = False


@app.get("/api/briefs")
async def get_briefs(period: Period | None = None, limit: int = Query(30, ge=1, le=200),
                     user: dict = VIEW):
    """Brief history, newest window first (headline and status only)."""
    return {"items": await pulse_store.list_briefs(await get_state(), period=period, limit=limit)}


@app.get("/api/briefs/{brief_id}")
async def get_brief_detail(brief_id: int, user: dict = VIEW):
    state = await get_state()
    brief = await pulse_store.get_brief(state, brief_id)
    if brief is None:
        raise HTTPException(status_code=404, detail="brief not found")
    return {**brief, "trend_terms": await pulse_store.list_trend_terms(state, brief_id)}


@app.get("/api/trends")
async def get_trends(days: int = Query(7, ge=1, le=90), user: dict = VIEW,
                     config: PulseConfig = Depends(get_config)):
    """Live trends for the last ``days`` days: deterministic, no Claude call."""
    end = datetime.now(timezone.utc).replace(tzinfo=None)
    report = await trends.compute_trends(
        await get_state(), end - timedelta(days=days), end, baseline_days=config.pulse.baseline_days,
        min_count=config.pulse.min_count, top_n=config.pulse.top_terms,
    )
    return report.to_dict()


@app.get("/api/language-bank")
async def get_language_bank(q: str | None = Query(None, max_length=200),
                            category: str | None = Query(None, max_length=40),
                            drug: str | None = Query(None, max_length=120),
                            sort: Literal["count", "recent"] = "count",
                            limit: int = Query(100, ge=1, le=pulse_store.MAX_PAGE),
                            offset: int = Query(0, ge=0), user: dict = VIEW):
    """Verbatim consumer phrases with counts (for copywriters)."""
    return await pulse_store.search_language_bank(await get_state(), q=q, category=category, drug=drug,
                                                  sort=sort, limit=limit, offset=offset)


@app.post("/api/briefs/generate")
async def generate_brief(body: GenerateBriefBody, user: dict = ADMIN,
                         config: PulseConfig = Depends(get_config),
                         brain=Depends(get_brief_brain), notifier=Depends(get_notifier)):
    """Build the brief for the latest window now (one Claude call unless it exists)."""
    brief = await briefs.build_brief(await get_state(), brain, body.period, config=config,
                                     force=body.force, notifier=notifier)
    logger.info("%s brief #%s requested by %s (created=%s)", body.period, brief["id"],
                user["email"], brief["created"])
    return brief


# ── Analytics: aggregate market charts (harvey/analytics.py) ──


@app.exception_handler(analytics.AnalyticsError)
async def _analytics_error(request: Request, exc: analytics.AnalyticsError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/api/analytics/options")
async def get_analytics_options(user: dict = VIEW):
    """Filter choices for the Analytics tab (anchored competitors first)."""
    return await analytics.options(await get_state())


@app.get("/api/analytics/{chart}")
async def get_analytics_chart(chart: str, days: str | None = Query(None, max_length=8),
                              start: str | None = Query(None, alias="from", max_length=20),
                              end: str | None = Query(None, alias="to", max_length=20),
                              bucket: str = Query("auto", max_length=8),
                              platform: list[str] = Query(default=[]),
                              competitor: list[str] = Query(default=[]),
                              drug: str | None = Query(None, max_length=200),
                              category: str | None = Query(None, max_length=200),
                              by: str = Query("category", max_length=20),
                              user: dict = VIEW, config: PulseConfig = Depends(get_config)):
    """One Analytics chart: aggregates only, bucketed in the org timezone."""
    if chart not in analytics.CHARTS:
        raise HTTPException(status_code=404, detail="unknown chart")
    if len(platform) > 20 or len(competitor) > 20:
        raise analytics.AnalyticsError("too many filter values")
    params = analytics.parse_params(
        days=days, start=start, end=end, bucket=bucket, platforms=platform, competitors=competitor,
        drug=drug, category=category, tz=config.usage.quiet_hours.timezone,
    )
    return await analytics.chart(await get_state(), chart, params, baseline_days=config.pulse.baseline_days,
                                 min_count=config.pulse.min_count, by=by)


# ── Helpers ──


async def query_db(sql: str, params: tuple = ()) -> list[dict]:
    """Run a read query; never raises (an empty install returns [])."""
    try:
        state = StateManager.from_env(DB_PATH)
        if state.db_path is not None and not Path(state.db_path).exists():
            return []
        async with state.connect() as db:
            async with db.execute(sql, params) as cursor:
                return [dict(r) for r in await cursor.fetchall()]
    except Exception as e:
        logger.warning("query_db failed (%s): %s", sql.split(None, 4)[:4], type(e).__name__)
        return []


def _pid_alive(pid: int) -> bool:
    """True if a process with this PID exists.

    POSIX: signal 0 is a no-op existence probe. Windows has no such probe —
    os.kill(pid, 0) there sends CTRL_C_EVENT — so ask the kernel instead.
    """
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def _check_harvey_pid() -> int | None:
    """Return the PID of a running heartbeat process, if any."""
    if _harvey_process and _harvey_process.poll() is None:
        return _harvey_process.pid
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            pid = None
        if pid and _pid_alive(pid):
            return pid
        PID_FILE.unlink(missing_ok=True)
    return None


# ── Heartbeat process control (start/stop: admin only) ──


@app.get("/api/harvey/status")
async def get_harvey_status(user: dict = VIEW):
    pid = _check_harvey_pid()
    started = _harvey_started_at.isoformat() if _harvey_started_at else None
    return {"running": pid is not None, "pid": pid, "started_at": started}


@app.post("/api/harvey/start")
async def start_harvey(user: dict = ADMIN):
    """Start the heartbeat loop as a subprocess."""
    global _harvey_process, _harvey_started_at

    if _check_harvey_pid():
        return {"success": False, "message": "Pulse is already running."}

    (PROJECT_ROOT / "data").mkdir(parents=True, exist_ok=True)
    try:
        log_handle = open(LOG_FILE, "a", encoding="utf-8")
        try:
            _harvey_process = subprocess.Popen(
                [sys.executable, "-m", "harvey", "run"],
                cwd=str(PROJECT_ROOT), stdout=log_handle, stderr=log_handle,
                start_new_session=True,
            )
        finally:
            log_handle.close()  # the child holds its own copies of the fds
    except Exception as e:
        logger.warning("Failed to start heartbeat: %s", e)
        return {"success": False, "message": "Failed to start; see the dashboard log."}
    _harvey_started_at = datetime.now()
    try:
        PID_FILE.write_text(str(_harvey_process.pid), encoding="utf-8")
    except OSError as e:
        logger.warning("Could not write PID file: %s", e)
    logger.info("heartbeat started by %s", user["email"])
    return {"success": True, "pid": _harvey_process.pid}


@app.post("/api/harvey/stop")
async def stop_harvey(user: dict = ADMIN):
    """Stop the heartbeat subprocess."""
    global _harvey_process, _harvey_started_at

    pid = _check_harvey_pid()
    if not pid:
        return {"success": False, "message": "Pulse is not running."}
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(10):
            if not _pid_alive(pid):
                break
            await asyncio.sleep(0.5)
        else:
            try:
                os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            except (ProcessLookupError, PermissionError, OSError):
                pass
    except (ProcessLookupError, PermissionError, OSError):
        pass
    _harvey_process = None
    _harvey_started_at = None
    PID_FILE.unlink(missing_ok=True)
    logger.info("heartbeat stopped by %s", user["email"])
    return {"success": True}


@app.get("/api/harvey/logs")
async def get_harvey_logs(user: dict = VIEW):
    """Recent heartbeat log lines (last 64KB, at most 100 lines)."""
    if not LOG_FILE.exists():
        return {"lines": []}
    try:
        with open(LOG_FILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 65536))
            text = f.read().decode("utf-8", errors="replace")
        return {"lines": text.strip().splitlines()[-100:]}
    except Exception:
        return {"lines": []}


@app.get("/api/summary")
async def get_summary(user: dict = VIEW):
    """Mention counts per status (zero-filled) and open escalations."""
    counts = {s.value: 0 for s in MentionStatus}
    for row in await query_db("SELECT status, COUNT(*) AS n FROM mentions GROUP BY status"):
        counts[row["status"]] = row["n"]
    open_rows = await query_db("SELECT COUNT(*) AS n FROM escalations WHERE acked_at IS NULL")
    return {
        "mentions": counts,
        "total": sum(counts.values()),
        "open_escalations": open_rows[0]["n"] if open_rows else 0,
    }


# ── Usage ──

_USAGE_SUM = (
    "COUNT(DISTINCT CASE WHEN session_id != '' THEN session_id ELSE id END) AS calls, "
    "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
    "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
    "COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens, "
    "COALESCE(SUM(cache_creation_tokens), 0) AS cache_creation_tokens, "
    "COALESCE(SUM(cost_usd), 0) AS cost_usd"
)


def _round_cost(rows: list[dict]) -> list[dict]:
    """cost_usd to 4 places in Python (Postgres has no ROUND(double, int))."""
    return [{**r, "cost_usd": round(float(r.get("cost_usd") or 0.0), 4)} for r in rows]

_quota_client = None


@app.get("/api/usage")
async def get_usage(user: dict = VIEW):
    """Token/cost accounting + live subscription quota for the Usage tab."""
    global _quota_client

    totals = {}
    windows = usage_windows()
    for label, where, params in windows:
        rows = _round_cost(await query_db(f"SELECT {_USAGE_SUM} FROM usage_events WHERE {where}",
                                          params))
        totals[label] = rows[0] if rows else {}
    month_start = next(params for label, _, params in windows if label == "month")

    async def grouped(expr, alias):
        # GROUP BY 1: the alias shadows a real column on Postgres.
        return _round_cost(await query_db(
            f"SELECT {expr} AS {alias}, {_USAGE_SUM} FROM usage_events "
            f"WHERE created_at >= ? GROUP BY 1 ORDER BY output_tokens DESC LIMIT 25",
            month_start,
        ))

    by_agent = await grouped("CASE WHEN agent = '' THEN 'other' ELSE agent END", "agent")
    by_task = await grouped("CASE WHEN task = '' THEN 'other' ELSE task END", "task")
    by_model = await grouped("CASE WHEN model = '' THEN 'unknown' ELSE model END", "model")
    by_day = _round_cost(await query_db(
        f"SELECT date(created_at) AS day, {_USAGE_SUM} FROM usage_events "
        f"WHERE created_at >= ? GROUP BY 1 ORDER BY day ASC",
        month_start,
    ))

    quota = None
    try:
        from harvey.integrations.quota import QuotaClient
        if _quota_client is None:
            _quota_client = QuotaClient()
        quota = await _quota_client.get_utilization()
    except Exception as e:
        logger.debug("Quota lookup failed: %s", e)

    return {"quota": quota, "totals": totals, "by_day": by_day, "by_agent": by_agent,
            "by_task": by_task, "by_model": by_model}


# ── Pages and static assets ──

WEB_DIR = (Path(__file__).resolve().parent / "web")

TEXT_TYPES = {".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml"}
BINARY_TYPES = {".woff2": "font/woff2", ".woff": "font/woff", ".png": "image/png"}


@app.get("/static/{path:path}")
async def static_file(path: str):
    """Serve the dashboard's own assets from disk (public: no data inside)."""
    target = (WEB_DIR / path).resolve()
    root = WEB_DIR.resolve()
    if not target.is_file() or not target.is_relative_to(root):
        return PlainTextResponse("not found", status_code=404)
    if target.suffix in BINARY_TYPES:
        return Response(target.read_bytes(), media_type=BINARY_TYPES[target.suffix],
                        headers={"Cache-Control": "public, max-age=604800"})
    return PlainTextResponse(target.read_text(encoding="utf-8"),
                             media_type=TEXT_TYPES.get(target.suffix, "text/plain"),
                             headers={"Cache-Control": "no-store"})


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return (WEB_DIR / "login.html").read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if await _session_user(request) is None:
        return RedirectResponse("/login", status_code=303)
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


# ── Startup ──


async def _prepare(state: StateManager) -> tuple[int, str]:
    """Init the DB, bootstrap the first admin from env, purge dead sessions."""
    await state.init_db()
    store = auth.AuthStore(state)
    env = load_env()
    message = await auth.bootstrap_admin(store, env.pulse_admin_email, env.pulse_admin_password)
    await store.purge_expired_sessions()
    admins = await store.count_active_admins()
    await state.close()  # the pool belongs to this event loop; uvicorn runs its own
    return admins, message


def _serve(host: str, port: int) -> None:
    import uvicorn

    # proxy_headers off: request.client stays the direct peer, and request_ip()
    # alone decides whether X-Forwarded-For is believed.
    uvicorn.run(app, host=host, port=port, log_level="warning", proxy_headers=False)


def start_dashboard(port: int = 5555, host: str = LOOPBACK_HOST):
    """Start the dashboard. A non-loopback host needs an active admin."""
    state = StateManager.from_env(DB_PATH)
    admins, message = asyncio.run(_prepare(state))
    if message:
        print(f"\n  {message}")
    error = auth.bind_error(host, admins, _config().dashboard.secure_cookies)
    if error:
        print(f"\n  {error}\n")
        raise SystemExit(2)
    if admins == 0:
        print("\n  No admin user yet: create one with `pulse user add EMAIL --role admin --name NAME`.")
    print(f"\n  WellPeps Pulse dashboard running at http://{host}:{port}")
    print("  Press Ctrl+C to stop.\n")
    _serve(host, port)
