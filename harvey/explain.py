"""Plain-language "why is this urgent" for the dashboard (authenticated only).

``urgency_explanation`` turns a triage ``urgency_reason`` (a code the
triager, safety screen, or a human wrote) into one short line a reviewer can
read. It never shows a keyword regex: for a keyword override it re-runs the
rules and quotes the words in the post that matched.

This text contains post content, so it is for the dashboard only. Slack
pages are built by ``harvey.escalation.build_page`` and stay link + category.
Returned strings are raw text; escaping is the UI's job.
"""

import re

from harvey import knowledge

EXCERPT_CHARS = 220
CONTEXT_CHARS = 60
EVIDENCE_CHARS = 120
REASON_CHARS = 160

_OVERRIDE = "override:"
_SCREEN = "safety_screen:"
_SEVERE = "severe_category"
_MANUAL = "manual:"
_TRIAGE_FAILED = "triage_failed"  # harvey.agents.triager.FALLBACK_REASON

_SCREEN_FLAGS = {"adverse_event": "adverse event", "self_harm": "self-harm", "minor": "minor"}
# "Classified as <label> by triage" for the severe categories.
_SEVERE_LABELS = {
    "adverse_event": "a possible adverse event",
    "legal_regulatory": "a legal / regulatory threat",
    "privacy": "a privacy complaint",
    "billing_fraud": "a billing fraud accusation",
}
_WS = re.compile(r"\s+")


def _collapse(text) -> str:
    return _WS.sub(" ", str(text or "")).strip()


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def excerpt(text, limit: int = EXCERPT_CHARS) -> str:
    """First ``limit`` characters, whitespace collapsed, with an ellipsis."""
    return _truncate(_collapse(text), limit)


def _field(triage, name: str) -> str:
    value = triage.get(name) if isinstance(triage, dict) else getattr(triage, name, "")
    return str(getattr(value, "value", value) or "")


def _keyword(mention_text: str) -> dict:
    hit = knowledge.urgent_override_match(mention_text)
    if hit is None:  # the rule was changed or removed since triage ran
        return {"source": "keyword", "text": "Matched an urgent keyword rule"}
    match = hit[2]
    text = match.string
    start, end = match.span()
    width = max(CONTEXT_CHARS, end - start)
    lo = max(0, start - (width - (end - start)) // 2)
    hi = min(len(text), lo + width)
    lo = max(0, hi - width)
    # Don't cut a word in half at a trimmed edge (the match itself is kept).
    if lo > 0 and not text[lo - 1].isspace():
        space = text.find(" ", lo, start)
        lo = space + 1 if space != -1 else lo
    if hi < len(text) and not text[hi].isspace():
        space = text.rfind(" ", end, hi)
        hi = space if space != -1 else hi
    snippet = _truncate(_collapse(text[lo:hi]), CONTEXT_CHARS)
    lead = "…" if lo > 0 else ""
    tail = "…" if hi < len(text) and not snippet.endswith("…") else ""
    return {"source": "keyword", "text": f'Matched: "{lead}{snippet}{tail}"'}


def _screen(reason: str, mention_text: str, evidence: str) -> dict:
    flag = reason[len(_SCREEN):].split(";", 1)[0].strip()
    label = _SCREEN_FLAGS.get(flag)
    if label is None:
        return {"source": "safety_screen",
                "text": "Independent safety check could not run — held for human review"}
    line = f"Independent safety check flagged: {label}"
    evidence = (evidence or "").strip()
    if evidence and evidence in (mention_text or ""):
        line += f' — "{_truncate(_collapse(evidence), EVIDENCE_CHARS)}"'
    return {"source": "safety_screen", "text": line}


def _severe(reason: str, category: str) -> dict:
    label = _SEVERE_LABELS.get(category, "a severe category")
    model_reason = _collapse(reason[len(_SEVERE):].lstrip(":"))
    line = f"Classified as {label} by triage"
    if model_reason:
        line += f": {_truncate(model_reason, REASON_CHARS)}"
    return {"source": "severe_category", "text": line}


def urgency_explanation(mention_text: str, triage, *, evidence: str = "",
                        manual_by: str | None = None) -> dict | None:
    """``{"source": ..., "text": ...}`` for a triage (a Triage or a row dict).

    ``evidence`` is the safety screen's quote (shown only if verbatim in the
    post); ``manual_by`` is the human who escalated by hand, when known.
    """
    if triage is None and not manual_by:
        return None
    reason = _field(triage, "urgency_reason").strip() if triage is not None else ""
    if manual_by or reason.startswith(_MANUAL):
        actor = manual_by or reason[len(_MANUAL):].strip().removeprefix("escalated by").strip()
        return {"source": "manual", "text": f"Escalated manually by {actor or 'a reviewer'}"}
    if reason.startswith(_SCREEN):
        return _screen(reason, mention_text, evidence)
    if reason.startswith(_OVERRIDE):
        return _keyword(mention_text)
    if reason.startswith(_SEVERE):
        return _severe(reason, _field(triage, "category"))
    if reason.startswith(_TRIAGE_FAILED):
        return {"source": "triage_failed", "text": "Triage failed — flagged for human review"}
    text = _truncate(_collapse(reason), REASON_CHARS)
    urgency = _field(triage, "urgency") or "unknown"
    return {"source": "model", "text": text or f"Rated {urgency} urgency by triage"}


def screen_evidence(audit_events) -> str:
    """The latest safety-screen evidence quote recorded on a triage audit event."""
    for event in reversed(list(audit_events or [])):
        kind = getattr(event, "event", None)
        if getattr(kind, "value", kind) != "triaged":
            continue
        screen = (getattr(event, "verdict", None) or {}).get("safety_screen") or {}
        if screen.get("evidence"):
            return str(screen["evidence"])
    return ""
