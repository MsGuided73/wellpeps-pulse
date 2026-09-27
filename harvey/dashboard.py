"""WellPeps Pulse dashboard — local web UI to monitor mentions, usage, and the
heartbeat process.

Binds to 127.0.0.1 only: there is no authentication yet (Phase 7), and the
feed carries public-but-sensitive health conversations.
"""

import asyncio
import logging
import os
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import aiosqlite
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from harvey.models import MentionStatus
from harvey.paths import DB_PATH, PROJECT_ROOT

logger = logging.getLogger("harvey.dashboard")

LOOPBACK_HOST = "127.0.0.1"
PID_FILE = PROJECT_ROOT / "data" / "pulse.pid"
LOG_FILE = PROJECT_ROOT / "data" / "pulse.log"
MENTIONS_MAX_LIMIT = 500
# The list endpoint returns a preview; full text will come from a detail
# endpoint (Phase 7), so list responses stay small.
MENTION_PREVIEW_CHARS = 2000

app = FastAPI(title="WellPeps Pulse")

# Heartbeat process tracking
_harvey_process: subprocess.Popen | None = None
_harvey_started_at: datetime | None = None


# ── Helpers ──


async def query_db(sql: str, params: tuple = ()) -> list[dict]:
    """Run a query and return results as list of dicts.

    Never raises: a missing DB file, missing table, or malformed schema
    returns [] so no dashboard route can 500 on an empty install.
    """
    db_path = Path(DB_PATH)
    if not db_path.exists():
        return []
    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("query_db failed (%s): %s", sql.split(None, 4)[:4], e)
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


# ── Heartbeat process control ──


@app.get("/api/harvey/status")
async def get_harvey_status():
    """Check if the heartbeat is currently running."""
    pid = _check_harvey_pid()
    started = _harvey_started_at.isoformat() if _harvey_started_at else None
    return {"running": pid is not None, "pid": pid, "started_at": started}


@app.post("/api/harvey/start")
async def start_harvey():
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
                cwd=str(PROJECT_ROOT),
                stdout=log_handle,
                stderr=log_handle,
                start_new_session=True,
            )
        finally:
            # Child holds its own copies of the fds; don't leak ours.
            log_handle.close()
    except Exception as e:
        logger.warning("Failed to start heartbeat: %s", e)
        return {"success": False, "message": f"Failed to start: {e}"}
    _harvey_started_at = datetime.now()

    try:
        PID_FILE.write_text(str(_harvey_process.pid), encoding="utf-8")
    except OSError as e:
        logger.warning("Could not write PID file: %s", e)

    return {"success": True, "pid": _harvey_process.pid}


@app.post("/api/harvey/stop")
async def stop_harvey():
    """Stop the heartbeat subprocess."""
    global _harvey_process, _harvey_started_at

    pid = _check_harvey_pid()
    if not pid:
        return {"success": False, "message": "Pulse is not running."}

    try:
        os.kill(pid, signal.SIGTERM)
        # Wait briefly for graceful shutdown
        for _ in range(10):
            if not _pid_alive(pid):
                break
            await asyncio.sleep(0.5)
        else:
            # Force kill if still running (SIGKILL does not exist on Windows)
            try:
                os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            except (ProcessLookupError, PermissionError, OSError):
                pass
    except (ProcessLookupError, PermissionError, OSError):
        pass

    _harvey_process = None
    _harvey_started_at = None
    PID_FILE.unlink(missing_ok=True)

    return {"success": True}


@app.get("/api/harvey/logs")
async def get_harvey_logs():
    """Get recent log lines."""
    if not LOG_FILE.exists():
        return {"lines": []}
    try:
        # Tail only the last 64KB so a huge log file never blocks the UI
        with open(LOG_FILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 65536))
            text = f.read().decode("utf-8", errors="replace")
        return {"lines": text.strip().splitlines()[-100:]}
    except Exception:
        return {"lines": []}


# ── Mentions ──


@app.get("/api/mentions")
async def get_mentions(status: str | None = None, limit: int = 100):
    """Mentions, newest first, optionally filtered by status."""
    params: list = []
    where = ""
    if status:
        try:
            params.append(MentionStatus(status).value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"unknown status '{status}'")
        where = "WHERE status = ?"
    params.append(max(1, min(int(limit), MENTIONS_MAX_LIMIT)))
    rows = await query_db(
        "SELECT id, platform, external_id, url, author_handle, title, "
        f"substr(text, 1, {MENTION_PREVIEW_CHARS}) AS text, "
        f"length(text) > {MENTION_PREVIEW_CHARS} AS text_truncated, "
        "lang, posted_at, collected_at, owned_channel, status "
        f"FROM mentions {where} ORDER BY collected_at DESC, id DESC LIMIT ?",
        tuple(params),
    )
    return [{**row, "text_truncated": bool(row["text_truncated"])} for row in rows]


@app.get("/api/summary")
async def get_summary():
    """Mention counts per status (zero-filled) and open escalations."""
    counts = {s.value: 0 for s in MentionStatus}
    for row in await query_db("SELECT status, COUNT(*) AS n FROM mentions GROUP BY status"):
        counts[row["status"]] = row["n"]
    open_rows = await query_db(
        "SELECT COUNT(*) AS n FROM escalations WHERE acked_at IS NULL"
    )
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
    "ROUND(COALESCE(SUM(cost_usd), 0), 4) AS cost_usd"
)

_quota_client = None


@app.get("/api/usage")
async def get_usage():
    """Token/cost accounting + live subscription quota for the Usage tab."""
    global _quota_client

    totals = {}
    for label, where in (
        ("today", "date(created_at) = date('now')"),
        ("week", "created_at >= datetime('now', '-7 days')"),
        ("month", "created_at >= datetime('now', '-30 days')"),
    ):
        rows = await query_db(f"SELECT {_USAGE_SUM} FROM usage_events WHERE {where}")
        totals[label] = rows[0] if rows else {}

    def grouped(expr, alias):
        return (
            f"SELECT {expr} AS {alias}, {_USAGE_SUM} FROM usage_events "
            f"WHERE created_at >= datetime('now', '-30 days') "
            f"GROUP BY {alias} ORDER BY output_tokens DESC LIMIT 25"
        )

    by_agent = await query_db(grouped("CASE WHEN agent = '' THEN 'other' ELSE agent END", "agent"))
    by_task = await query_db(grouped("CASE WHEN task = '' THEN 'other' ELSE task END", "task"))
    by_model = await query_db(grouped("CASE WHEN model = '' THEN 'unknown' ELSE model END", "model"))
    by_day = await query_db(
        f"SELECT date(created_at) AS day, {_USAGE_SUM} FROM usage_events "
        f"WHERE created_at >= datetime('now', '-30 days') "
        f"GROUP BY day ORDER BY day ASC"
    )

    quota = None
    try:
        from harvey.integrations.quota import QuotaClient
        if _quota_client is None:
            _quota_client = QuotaClient()
        quota = await _quota_client.get_utilization()
    except Exception as e:
        logger.debug("Quota lookup failed: %s", e)

    return {
        "quota": quota,
        "totals": totals,
        "by_day": by_day,
        "by_agent": by_agent,
        "by_task": by_task,
        "by_model": by_model,
    }


# ── Static assets ──

WEB_DIR = (Path(__file__).resolve().parent / "web")

TEXT_TYPES = {".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml"}
BINARY_TYPES = {".woff2": "font/woff2", ".woff": "font/woff", ".png": "image/png"}


@app.get("/static/{path:path}")
async def static_file(path: str):
    """Serve the dashboard's own assets from disk.

    Read per-request rather than cached at import so editing app.css and
    reloading just works. Fonts are vendored so the page renders offline.
    """
    target = (WEB_DIR / path).resolve()
    root = WEB_DIR.resolve()
    if not target.is_file() or not target.is_relative_to(root):
        return PlainTextResponse("not found", status_code=404)

    if target.suffix in BINARY_TYPES:
        return Response(
            target.read_bytes(),
            media_type=BINARY_TYPES[target.suffix],
            headers={"Cache-Control": "public, max-age=604800"},
        )
    return PlainTextResponse(
        target.read_text(encoding="utf-8"),
        media_type=TEXT_TYPES.get(target.suffix, "text/plain"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


def start_dashboard(port: int = 5555):
    """Start the dashboard server on the loopback interface only."""
    import uvicorn

    print(f"\n  WellPeps Pulse dashboard running at http://{LOOPBACK_HOST}:{port}")
    print("  Press Ctrl+C to stop.\n")
    uvicorn.run(app, host=LOOPBACK_HOST, port=port, log_level="warning")
