"""Output guards for #pulse-query answers (deterministic, no Claude).

- ``verify_numbers``: drop every sentence that cites a number absent from the
  aggregate payload (like ``briefs.verify_cards``). Digits glued to a word
  (``GLP-1``, ``Q3``) are names, not figures, and are not checked.
- ``scrub``: remove Slack mentions/broadcasts (``<@U..>``, ``<!channel>``),
  @handles, u/ and r/ handles, e-mail addresses, every URL except one on the
  dashboard, and any quoted span of 8+ words (a verbatim post could point at
  its author).
- ``cap_words`` / ``cap_chars``: <= 120 words of model text, <= 1500 chars
  in the final message (footer included).
"""

import json
import re

from harvey.briefs import numbers_in as payload_numbers

MAX_WORDS = 120
MAX_CHARS = 1500
MIN_QUOTE_WORDS = 8
QUOTE_REMOVED = "[quote removed]"

# A figure: not glued to a word (GLP-1, BPC-157, Q3) or another number part.
_FIGURE = re.compile(r"(?<![A-Za-z]-)(?<![\w.,])\d+(?:,\d{3})*(?:\.\d+)?")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_SLACK_SPECIAL = re.compile(r"<[!@#][^>]*>")
_SLACK_LINK = re.compile(r"<((?:https?|mailto):[^|>\s]*)(?:\|([^>]*))?>", re.I)
_URL = re.compile(r"(?:https?://|www\.)[^\s<>|)\]]+", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_HANDLE = re.compile(r"(?<![\w/@])@[\w.]+|(?<![\w/])/?\b[ur]/[\w-]+", re.I)
_QUOTES = (
    re.compile(r'"([^"\n]+)"'),
    re.compile(r"“([^”\n]+)”"),
    re.compile(r"‘([^’\n]+)’"),
    re.compile(r"(?<!\w)'([^'\n]+)'(?!\w)"),
    re.compile(r"«([^»\n]+)»"),
    re.compile(r"`([^`\n]+)`"),
)
_BLOCKQUOTE = re.compile(r"^\s*(?:>|&gt;)\s?(.*)$")


def figures(text: str) -> set[float]:
    return {float(n.replace(",", "")) for n in _FIGURE.findall(text or "")}


def verify_numbers(text: str, payload: dict) -> tuple[str, list[str]]:
    """(text without sentences citing invented numbers, the stripped sentences)."""
    allowed = payload_numbers(json.dumps(payload, ensure_ascii=False))
    kept_lines, stripped = [], []
    for line in (text or "").splitlines():
        if not line.strip():
            kept_lines.append("")
            continue
        kept = []
        for sentence in _SENTENCE_END.split(line):
            if figures(sentence) - allowed:
                stripped.append(sentence.strip())
            else:
                kept.append(sentence)
        joined = " ".join(s for s in kept if s.strip())
        if joined.strip() and joined.strip() not in ("-", "•", "*"):
            kept_lines.append(joined)
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(kept_lines)).strip()
    return out, stripped


def _is_dashboard(url: str, dashboard_url: str) -> bool:
    base = (dashboard_url or "").strip().rstrip("/")
    return bool(base) and (url == base or url.startswith(base + "/") or url.startswith(base + "#"))


def _quote_out(match: re.Match) -> str:
    return QUOTE_REMOVED if len(match.group(1).split()) >= MIN_QUOTE_WORDS else match.group(0)


def scrub(text: str, dashboard_url: str = "") -> str:
    """No handles, links (but the dashboard's), e-mails or long quotes."""
    text = _SLACK_SPECIAL.sub("", text or "")

    def slack_link(match):
        url, label = match.group(1), (match.group(2) or "").strip()
        if _is_dashboard(url, dashboard_url):
            return match.group(0)
        return label

    text = _SLACK_LINK.sub(slack_link, text)
    text = _EMAIL.sub("", text)
    text = _URL.sub(lambda m: m.group(0) if _is_dashboard(m.group(0), dashboard_url) else "", text)
    text = _HANDLE.sub("", text)
    for pattern in _QUOTES:
        text = pattern.sub(_quote_out, text)
    lines = []
    for line in text.splitlines():
        quoted = _BLOCKQUOTE.match(line)
        if quoted and len(quoted.group(1).split()) >= MIN_QUOTE_WORDS:
            line = QUOTE_REMOVED
        lines.append(re.sub(r"[ \t]{2,}", " ", line).rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def cap_words(text: str, limit: int = MAX_WORDS) -> str:
    words = 0
    out = []
    for line in (text or "").splitlines():
        parts = line.split(" ")
        kept = []
        for part in parts:
            if part.strip():
                words += 1
            if words > limit:
                break
            kept.append(part)
        out.append(" ".join(kept))
        if words > limit:
            out[-1] = out[-1].rstrip() + " …"
            break
    return "\n".join(out).strip()


def cap_chars(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    cut = text[: max(limit - 1, 0)]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip() + "…"
