"""The three main views (Pulse, Operator HQ, Knowledge): static-file rules and page structure.

The CSP forbids inline script and style, so the new scripts must build markup without
style attributes or inline handlers, never fetch off-site, and load in dependency order.
"""

import re

import pytest

from harvey import dashboard

WEB = dashboard.WEB_DIR
NEW_SCRIPTS = ["rewrite.js", "home.js", "hq.js", "knowledge.js"]


@pytest.mark.parametrize("name", NEW_SCRIPTS)
def test_view_scripts_follow_the_csp_rules(name):
    js = (WEB / name).read_text(encoding="utf-8")
    assert "style=" not in js
    assert not re.search(r"\son[a-z]+=", js)
    assert "eval(" not in js and "new Function" not in js
    assert "escHtml" in js
    assert not re.search(r"(?:fetch|request)\(['\"]https?://", js)


def test_view_scripts_load_after_app_js_in_dependency_order():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    order = ["/static/labels.js", "/static/app.js", "/static/rewrite.js", "/static/home.js",
             "/static/hq.js", "/static/knowledge.js"]
    positions = [html.index(src) for src in order]
    assert positions == sorted(positions)


def test_app_starts_only_after_every_view_script_has_registered():
    # A cold load once routed before home.js set TAB_LOAD.pulse, leaving the Pulse tab
    # on "Checking what needs you..." (2026-10-20). init must wait for DOMContentLoaded.
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert re.search(r"^whenScriptsLoaded\(init\);\s*$", js, re.M)
    assert not re.search(r"^init\(\);", js, re.M)
    assert "addEventListener('DOMContentLoaded', start" in js


def _selection_codes() -> set[str]:
    from pathlib import Path

    from harvey import engagement

    src = Path(engagement.__file__).read_text(encoding="utf-8")
    block = src.split("# --- Conversation selection record", 1)[1].split("# --- Drafter guidance", 1)[0]
    codes = set(re.findall(r'"([A-Z][A-Z_]{3,})"', block)) - {"RESPOND", "SKIP", "HOLD", "ROUTE"}
    codes |= {getattr(engagement, n).upper() for n in dir(engagement) if n.startswith("SKIP_")}
    return codes


def test_every_selection_reason_has_plain_wording_on_the_review_card():
    labels = (WEB / "labels.js").read_text(encoding="utf-8")
    pick_reason = labels.split("pickReason: {", 1)[1].split("},", 1)[0]
    missing = sorted(c for c in _selection_codes() if not re.search(rf"\b{c}:", pick_reason))
    assert not missing, f"add plain wording to LABELS.pickReason in labels.js: {missing}"
    for decision in ("RESPOND", "HOLD", "SKIP", "ROUTE"):
        assert re.search(rf"\b{decision}: '", labels.split("pick: {", 1)[1].split("},", 1)[0])


def test_review_card_shows_why_this_post_and_no_false_community_claim():
    js = (WEB / "home.js").read_text(encoding="utf-8")
    assert "function pickHtml(card)" in js and "Why this post:" in js
    assert "card.phase === 'review' ? pickHtml(card)" in js
    assert "doesn't allow agents" not in js and "doesn\\'t allow agents" not in js
    assert 'href="#communities"' in js


def test_page_has_landmarks_three_views_and_a_live_region():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert 'class="skip-link"' in html and '<main id="main"' in html and "<header" in html and "<footer" in html
    assert 'id="toasts" role="status" aria-live="polite"' in html
    for label in ("Pulse", "Operator HQ", "Knowledge"):
        assert re.search(rf'data-nav="[a-z]+">{label}', html)
    for view in ("pulse", "hq", "knowledge"):
        assert len(re.findall(rf'id="{view}" class="section view"', html)) == 1
    # One h1 per view: the three views and each older tab carry exactly one.
    for section in re.findall(r'<div id="(\w+)" class="section(?: view)?">(.*?)(?=<div id="\w+" class="section|</main>)', html, re.S):
        assert section[1].count("<h1") == 1, section[0]


def test_stylesheets_and_brand_assets_are_served():
    from fastapi.testclient import TestClient

    client = TestClient(dashboard.app)  # static files are public and need no database
    assert client.get("/static/views.css").status_code == 200
    assert "Segoe UI" in client.get("/static/app.css").text
    assert client.get("/static/brand/pulse-icon.png").headers["content-type"].startswith("image/png")
    assert client.get("/static/brand/pulse-favicon-32.png").status_code == 200
    assert "Inter" not in client.get("/static/app.css").text


def test_ui_copy_has_no_em_dashes_in_the_new_views():
    for name in ("home.js", "hq.js", "knowledge.js", "rewrite.js"):
        js = (WEB / name).read_text(encoding="utf-8")
        code = "\n".join(line for line in js.splitlines() if not line.lstrip().startswith("//"))
        assert "—" not in code, name
    html = (WEB / "index.html").read_text(encoding="utf-8")
    start = html.index('<main id="main"')
    assert "—" not in html[start:html.index('<div id="feed"')]


def test_review_card_shows_what_the_draft_relied_on():
    # Context harness (R-27): one calm gray line under "Why this post" naming the library
    # sections the draft relied on, from latest_draft.context (names only, escaped).
    js = (WEB / "home.js").read_text(encoding="utf-8")
    assert "function reliedHtml(card)" in js and "Relied on: " in js
    assert "card.phase === 'review' ? pickHtml(card) + reliedHtml(card)" in js
    body = js.split("function reliedHtml(card)", 1)[1].split("\n  }\n", 1)[0]
    assert "latest_draft" in body and ".context" in body
    assert "escHtml(" in body and "style=" not in body and "innerHTML" not in body
    css = (WEB / "views.css").read_text(encoding="utf-8")
    rule = re.search(r"\.relied\s*\{([^}]*)\}", css)
    assert rule and "var(--muted)" in rule.group(1) and "#" not in rule.group(1)
