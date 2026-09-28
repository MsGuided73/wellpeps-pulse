"""SQLite -> Postgres SQL translation, kept deliberately small and explicit.

The app writes SQLite-flavoured but *portable* SQL: ``?`` placeholders,
``RETURNING id`` instead of ``lastrowid``, ``TRUE``/``FALSE`` for flags,
``ON CONFLICT (...) DO UPDATE`` upserts with table-qualified columns, and
time cutoffs computed in Python (``sql_utc``) instead of ``datetime('now')``.
``to_postgres`` then only has to do what can't be written portably:

- ``?``                  -> ``$1, $2, ...``
- ``INSERT OR IGNORE``   -> ``INSERT ... ON CONFLICT DO NOTHING``
- ``CURRENT_TIMESTAMP``  -> ``(now() AT TIME ZONE 'utc')`` (naive UTC, like SQLite)
- ``instr(a, b)``        -> ``strpos(a, b)``

Anything SQLite-only that has no safe rewrite (PRAGMA, INSERT OR REPLACE,
datetime()/date('now')/strftime()/julianday()) raises ``NotPortableError``.
The SQLite connection runs the same check, so the SQLite test suite fails
the moment a non-portable query is added. Rewrites never touch string
literals, quoted identifiers or ``--`` comments.
"""

import re
from datetime import datetime, timezone
from functools import lru_cache


class NotPortableError(ValueError):
    """SQL that only SQLite understands and that has no explicit rewrite."""


# Constructs rejected outright; rewrite the query portably instead. The flag
# says whether the pattern spans a string literal (date('now')).
_FORBIDDEN = (
    (re.compile(r"\bPRAGMA\b", re.I), "PRAGMA", False),
    (re.compile(r"\bINSERT\s+OR\s+REPLACE\b", re.I),
     "INSERT OR REPLACE (use ON CONFLICT ... DO UPDATE)", False),
    (re.compile(r"\bdatetime\s*\(", re.I), "datetime() (compute the cutoff with sql_utc)", False),
    (re.compile(r"\bstrftime\s*\(", re.I), "strftime()", False),
    (re.compile(r"\bjulianday\s*\(", re.I), "julianday()", False),
    (re.compile(r"\bdate\s*\(\s*'now'", re.I), "date('now') (compute the cutoff with sql_utc)", True),
)

# The explicit rewrite table, applied to code (never to literals).
_REWRITES = (
    (re.compile(r"\bCURRENT_TIMESTAMP\b", re.I), "(now() AT TIME ZONE 'utc')"),
    (re.compile(r"\binstr\s*\(", re.I), "strpos("),
)

_INSERT_OR_IGNORE = re.compile(r"^(\s*)INSERT\s+OR\s+IGNORE\s+INTO\b", re.I)
_RETURNING = re.compile(r"\bRETURNING\b", re.I)
_ON_CONFLICT = re.compile(r"\bON\s+CONFLICT\b", re.I)
_ROW_STATEMENT = re.compile(r"^\s*(SELECT|WITH|VALUES)\b", re.I)


def _segments(sql: str) -> list[tuple[bool, str]]:
    """Split ``sql`` into (is_code, text) chunks.

    String literals ('...'), quoted identifiers ("...") and ``--`` comments are
    not code. Doubled quotes inside a literal are escapes.
    """
    chunks: list[tuple[bool, str]] = []
    i = start = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch in "'\"":
            if start < i:
                chunks.append((True, sql[start:i]))
            j = i + 1
            while True:
                if j >= n:
                    raise ValueError(f"unterminated quoted literal in SQL: {sql[i:i + 40]!r}")
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:
                        j += 2
                        continue
                    break
                j += 1
            chunks.append((False, sql[i:j + 1]))
            i = start = j + 1
            continue
        if sql.startswith("--", i):
            if start < i:
                chunks.append((True, sql[start:i]))
            end = sql.find("\n", i)
            end = n if end < 0 else end
            chunks.append((False, sql[i:end]))
            i = start = end
            continue
        i += 1
    if start < n:
        chunks.append((True, sql[start:]))
    return chunks


def _code(sql: str) -> str:
    """``sql`` with literals blanked to '' and comments dropped, for keyword searches."""
    return " ".join(text if is_code else ("" if text.startswith("--") else "''")
                    for is_code, text in _segments(sql))


@lru_cache(maxsize=1024)
def check_portable(sql: str) -> None:
    """Raise NotPortableError if ``sql`` uses a construct Postgres can't run."""
    code = _code(sql)
    for pattern, label, spans_literal in _FORBIDDEN:
        if pattern.search(sql if spans_literal else code):
            raise NotPortableError(f"SQLite-only SQL ({label}): {sql.strip()[:120]!r}")
    if _INSERT_OR_IGNORE.match(code) and _ON_CONFLICT.search(code):
        raise NotPortableError(f"INSERT OR IGNORE with its own ON CONFLICT: {sql.strip()[:120]!r}")


@lru_cache(maxsize=1024)
def to_postgres(sql: str) -> str:
    """Translate one portable SQLite statement to Postgres (see module doc)."""
    check_portable(sql)
    counter = 0

    def placeholder(_match):
        nonlocal counter
        counter += 1
        return f"${counter}"

    ignore = bool(_INSERT_OR_IGNORE.match(_code(sql)))
    pending_ignore = ignore
    out = []
    for is_code, text in _segments(sql):
        if is_code:
            if pending_ignore and text.strip():
                # The statement's first code; comments may come before it.
                text = _INSERT_OR_IGNORE.sub(r"\1INSERT INTO", text, count=1)
                pending_ignore = False
            text = re.sub(r"\?", placeholder, text)
            for pattern, replacement in _REWRITES:
                text = pattern.sub(replacement, text)
        out.append(text)
    translated = "".join(out)
    return _add_on_conflict_do_nothing(translated) if ignore else translated


def _add_on_conflict_do_nothing(sql: str) -> str:
    """Add ``ON CONFLICT DO NOTHING`` before a top-level RETURNING, else after the last code."""
    chunks = _segments(sql)
    for i, (is_code, text) in enumerate(chunks):
        if is_code:
            match = _RETURNING.search(text)
            if match:
                head, tail = text[:match.start()].rstrip(), text[match.start():]
                chunks[i] = (True, f"{head} ON CONFLICT DO NOTHING {tail}")
                return "".join(t for _, t in chunks)
    last = max(i for i, (is_code, text) in enumerate(chunks) if is_code and text.strip())
    code = chunks[last][1]
    body = code.rstrip()
    trailing = code[len(body):]
    body = body.rstrip(";").rstrip()
    chunks[last] = (True, f"{body} ON CONFLICT DO NOTHING{trailing}")
    return "".join(t for _, t in chunks).rstrip()


@lru_cache(maxsize=1024)
def returns_rows(sql: str) -> bool:
    """True for statements that produce a result set (SELECT/WITH or RETURNING)."""
    code = _code(sql)
    return bool(_ROW_STATEMENT.match(code) or _RETURNING.search(code))


def rowcount_from_status(status: str | None) -> int:
    """Rows affected from an asyncpg command tag ('UPDATE 3' -> 3); -1 if none."""
    parts = (status or "").split()
    if len(parts) >= 2 and parts[-1].isdigit():
        return int(parts[-1])
    return -1


# ── Timestamps: naive UTC, the same ISO strings on both backends ──


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def encode_timestamp(value) -> str:
    """Python value -> Postgres `timestamp` text (naive UTC)."""
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip())
        except ValueError as exc:
            raise ValueError(f"not an ISO timestamp: {value!r}") from exc
    return _naive_utc(parsed).isoformat(sep=" ")


def decode_timestamp(text: str) -> str:
    """Postgres `timestamp` text -> the ISO string Python's isoformat() writes."""
    return datetime.fromisoformat(text).isoformat()


def encode_date(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def decode_date(text: str) -> str:
    return text


def sql_utc(value: datetime) -> str:
    """A naive-UTC cutoff in the exact format of SQLite's datetime('now', ...)."""
    return _naive_utc(value).strftime("%Y-%m-%d %H:%M:%S")
