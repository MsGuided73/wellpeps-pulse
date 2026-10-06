"""Sandbox content store: its own small SQLite file, created on demand.

Deliberately separate from the Pulse database (SQLite or Postgres): demo
content never touches the production schema, and production needs no
migration for a demo-only feature. Plain ``sqlite3`` (the routes using it are
sync, so FastAPI runs them in its thread pool).
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sandbox_communities (
    name TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('forum', 'photo', 'review')),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sandbox_threads (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('forum', 'photo', 'review')),
    community TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL,
    created_at TEXT NOT NULL,
    points INTEGER NOT NULL DEFAULT 1,
    image_seed INTEGER NOT NULL DEFAULT 0,
    flair TEXT NOT NULL DEFAULT '',
    rating INTEGER
);
CREATE INDEX IF NOT EXISTS ix_sandbox_threads_community ON sandbox_threads(community, created_at);
CREATE TABLE IF NOT EXISTS sandbox_comments (
    id INTEGER PRIMARY KEY,
    thread_id INTEGER NOT NULL REFERENCES sandbox_threads(id),
    parent_id INTEGER REFERENCES sandbox_comments(id),
    author TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    points INTEGER NOT NULL DEFAULT 1,
    is_brand INTEGER NOT NULL DEFAULT 0,
    flair TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_sandbox_comments_thread ON sandbox_comments(thread_id, id);
"""

THREAD_COLUMNS = ("id", "kind", "community", "title", "body", "author", "created_at", "points",
                  "image_seed", "flair", "rating")


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


class SandboxStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            db.executescript(SCHEMA)
            yield db
            db.commit()
        finally:
            db.close()

    # --- writes ------------------------------------------------------------------

    def reset(self) -> None:
        """Delete every row (seeding rebuilds the sandbox from scratch)."""
        with self._db() as db:
            db.executescript("DELETE FROM sandbox_comments; DELETE FROM sandbox_threads; "
                             "DELETE FROM sandbox_communities;")

    def add_community(self, name: str, kind: str, title: str, description: str = "") -> None:
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO sandbox_communities (name, kind, title, description) "
                       "VALUES (?, ?, ?, ?)", (name, kind, title, description))

    def add_thread(self, *, kind: str, community: str, title: str, body: str, author: str,
                   created_at: str, points: int = 1, image_seed: int = 0, flair: str = "",
                   rating: int | None = None) -> int:
        with self._db() as db:
            cursor = db.execute(
                "INSERT INTO sandbox_threads (kind, community, title, body, author, created_at, points, "
                "image_seed, flair, rating) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, community, title, body, author, created_at, int(points), int(image_seed), flair,
                 rating))
            return int(cursor.lastrowid)

    def add_comment(self, thread_id: int, parent_id: int | None, author: str, body: str,
                    created_at: str | None = None, points: int = 1, *, is_brand: bool = False,
                    flair: str = "") -> int:
        with self._db() as db:
            cursor = db.execute(
                "INSERT INTO sandbox_comments (thread_id, parent_id, author, body, created_at, points, "
                "is_brand, flair) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (int(thread_id), parent_id, author, body, created_at or _now(), int(points),
                 int(bool(is_brand)), flair))
            return int(cursor.lastrowid)

    # --- reads -------------------------------------------------------------------

    @staticmethod
    def _comment_dict(row) -> dict:
        data = dict(row)
        data["is_brand"] = bool(data["is_brand"])
        return data

    def thread(self, thread_id: int) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM sandbox_threads WHERE id = ?", (int(thread_id),)).fetchone()
        return dict(row) if row else None

    def comments(self, thread_id: int) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM sandbox_comments WHERE thread_id = ? ORDER BY id",
                              (int(thread_id),)).fetchall()
        return [self._comment_dict(r) for r in rows]

    def comment(self, comment_id: int) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM sandbox_comments WHERE id = ?", (int(comment_id),)).fetchone()
        return self._comment_dict(row) if row else None

    def community(self, name: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM sandbox_communities WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

    def communities(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT c.*, COUNT(t.id) AS threads FROM sandbox_communities c "
                "LEFT JOIN sandbox_threads t ON t.community = c.name GROUP BY c.name "
                "ORDER BY c.kind, c.name").fetchall()
        return [dict(r) for r in rows]

    def threads(self, community: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
        where, params = ("WHERE t.community = ?", (community,)) if community else ("", ())
        with self._db() as db:
            rows = db.execute(
                "SELECT t.*, (SELECT COUNT(*) FROM sandbox_comments c WHERE c.thread_id = t.id) AS comments "
                f"FROM sandbox_threads t {where} ORDER BY t.created_at DESC, t.id DESC LIMIT ? OFFSET ?",
                (*params, int(limit), int(offset))).fetchall()
        return [dict(r) for r in rows]

    def count_threads(self, community: str | None = None) -> int:
        where, params = ("WHERE community = ?", (community,)) if community else ("", ())
        with self._db() as db:
            return db.execute(f"SELECT COUNT(*) FROM sandbox_threads {where}", params).fetchone()[0]
