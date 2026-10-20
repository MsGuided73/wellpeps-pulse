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
