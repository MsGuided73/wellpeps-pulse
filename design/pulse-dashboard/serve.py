"""Local dev server for the WellPeps Pulse site.

Serves the static files in this folder and one JSON endpoint:

    POST /api/rewrite  {"passage": str, "instruction": str, "context": str}
                    -> {"ok": true, "text": str} | {"ok": false, "error": str}

Rewrites run through the local Claude Code CLI (`claude -p`), so they bill the
signed-in Claude subscription. ANTHROPIC_API_KEY is stripped from the child
environment so billing can't silently switch to the API. Localhost only.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"
PORT = int(os.environ.get("PULSE_SITE_PORT", "5180"))
ROOT = Path(__file__).resolve().parent

MAX_PASSAGE_CHARS = 1500
MAX_INSTRUCTION_CHARS = 200
MAX_CONTEXT_CHARS = 3000
MAX_BODY_BYTES = 16_000
CLAUDE_TIMEOUT_SECONDS = 90
CLAUDE_MODEL = os.environ.get("PULSE_REWRITE_MODEL", "haiku")

SYSTEM_PROMPT = """You rewrite ONE passage of a public community reply drafted for WellPeps, a U.S. telehealth brand. A person will review your rewrite before anything is posted.

Return ONLY the rewritten passage: no quotes, no preamble, no explanation, no alternatives.

Follow the requested change (tone, length, clarity) while keeping these rules:
- Keep the meaning. Do not add facts, numbers, prices, timelines, outcomes, guarantees or medical advice that are not in the passage.
- If the passage says the writer works with or is part of WellPeps, keep that disclosure clearly.
- Keep every link, guide title and quoted chapter title exactly as written.
- Never name, compare with or criticize other providers or pharmacies.
- Never suggest DMs, private messages, phone calls or sharing order or health details.
- Never claim to be a customer, patient or clinician.
- Sound like a friendly, knowledgeable person: plain words, contractions, no corporate phrases, no emoji, no hashtags, no em dashes.
- Keep roughly the same length unless asked to shorten or expand."""


def live_db_path() -> Path | None:
    """Pulse's live database: PULSE_LIVE_DB, else the repo's data/live.db when this
    file sits in design/pulse-dashboard/, else the default checkout location."""
    candidates = [os.environ.get("PULSE_LIVE_DB"), ROOT.parents[1] / "data" / "live.db",
                  Path("C:/dev/wellpeps-pulse/data/live.db")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def month_spend(now: datetime | None = None) -> dict:
    """Month-to-date spend from Pulse's own records, read-only: Claude calls
    (usage_events.cost_usd, by agent) and Apify runs (runs.cost_usd)."""
    path = live_db_path()
    if path is None:
        raise RuntimeError("Pulse's live database wasn't found. Set PULSE_LIVE_DB.")
    now = now or datetime.now()
    start = now.strftime("%Y-%m-01")
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as db:
        by_agent = {agent or "other": round(total or 0.0, 4) for agent, total in db.execute(
            "SELECT agent, SUM(cost_usd) FROM usage_events WHERE created_at >= ? GROUP BY agent", (start,))}
        apify_total, apify_runs = db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM runs WHERE started_at >= ?", (start,)).fetchone()
    claude_total = round(sum(by_agent.values()), 4)
    return {"ok": True, "month": now.strftime("%Y-%m"), "month_label": now.strftime("%B"),
            "claude": {"total": claude_total, "by_agent": by_agent, "billing": "subscription (estimate)"},
            "apify": {"total": round(apify_total, 4), "runs": apify_runs},
            "total": round(claude_total + apify_total, 4)}


def run_rewrite(passage: str, instruction: str, context: str) -> str:
    claude = shutil.which("claude")
    if not claude:
        raise RuntimeError("Claude Code isn't installed or isn't on PATH.")
    prompt = (
        f"Full reply, for context only (do not rewrite it):\n<reply>\n{context}\n</reply>\n\n"
        f"Passage to rewrite:\n<passage>\n{passage}\n</passage>\n\n"
        f"Requested change: {instruction}"
    )
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    result = subprocess.run(
        [claude, "-p", "--model", CLAUDE_MODEL, "--tools", "", "--strict-mcp-config",
         "--setting-sources", "", "--no-session-persistence", "--system-prompt", SYSTEM_PROMPT],
        input=prompt, capture_output=True, text=True, encoding="utf-8",
        timeout=CLAUDE_TIMEOUT_SECONDS, env=env, cwd=ROOT,
    )
    text = (result.stdout or "").strip().strip('"').strip()
    if result.returncode != 0 or not text:
        detail = (result.stderr or result.stdout or "").strip().splitlines()[-1:] or ["no output"]
        raise RuntimeError(f"Claude didn't return a rewrite ({detail[0][:160]}).")
    return text


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      ".html": "text/html; charset=utf-8", ".md": "text/markdown; charset=utf-8"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 (http.server naming)
        if self.path.split("?")[0] != "/api/spend":
            return super().do_GET()
        try:
            return self._json(200, month_spend())
        except (RuntimeError, sqlite3.Error) as exc:
            print(f"[spend] failed: {exc}", file=sys.stderr)
            return self._json(503, {"ok": False, "error": "Spend isn't available right now."})

    def do_POST(self):  # noqa: N802 (http.server naming)
        if self.path != "/api/rewrite":
            return self._json(404, {"ok": False, "error": "Not found."})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            return self._json(413, {"ok": False, "error": "That request is too large."})
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._json(400, {"ok": False, "error": "The request wasn't valid JSON."})

        passage = str(data.get("passage") or "").strip()
        instruction = str(data.get("instruction") or "").strip()
        context = str(data.get("context") or "").strip()
        if not passage or not instruction:
            return self._json(400, {"ok": False, "error": "Select some text and choose how to rewrite it."})
        if len(passage) > MAX_PASSAGE_CHARS:
            return self._json(400, {"ok": False, "error": f"Select less text (up to {MAX_PASSAGE_CHARS} characters)."})
        if len(instruction) > MAX_INSTRUCTION_CHARS:
            return self._json(400, {"ok": False, "error": f"Keep the request under {MAX_INSTRUCTION_CHARS} characters."})
        context = context[:MAX_CONTEXT_CHARS]

        try:
            text = run_rewrite(passage, instruction, context)
        except subprocess.TimeoutExpired:
            return self._json(504, {"ok": False, "error": "The rewrite took too long. Try again."})
        except (RuntimeError, OSError) as exc:
            print(f"[rewrite] failed: {exc}", file=sys.stderr)
            return self._json(502, {"ok": False, "error": str(exc)})
        return self._json(200, {"ok": True, "text": text})


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"WellPeps Pulse site on http://{HOST}:{PORT}  (AI rewrite: claude -p --model {CLAUDE_MODEL})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
