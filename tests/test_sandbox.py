"""Local DEMO sandbox (harvey/sandbox): guards, store, pages, posting.

The sandbox is a fictional community site served by the dashboard only when
PULSE_DEMO_SANDBOX is on, outside containers, and for loopback peers.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harvey import dashboard, sandbox
from harvey.sandbox import urls as sandbox_urls
from harvey.sandbox.store import SandboxStore

LOCAL = ("127.0.0.1", 50000)
REMOTE = ("203.0.113.9", 50000)
SANDBOX_WEB = Path(dashboard.WEB_DIR) / "sandbox"


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "sandbox.db"
    monkeypatch.setenv("PULSE_SANDBOX_DB_PATH", str(path))
    monkeypatch.setattr(dashboard, "DB_PATH", tmp_path / "pulse.db")
    s = SandboxStore(path)
    s.add_community("tirzepatide_talk", "forum", "Tirzepatide talk", "Fictional demo community.")
    return s


@pytest.fixture
def thread(store):
    tid = store.add_thread(kind="forum", community="tirzepatide_talk", title="Follow-up visits?",
                           body="Which providers include follow-ups?", author="demo_op",
                           created_at="2026-09-20T10:00:00", points=12)
    cid = store.add_comment(tid, None, "demo_commenter", "Mine charged extra.", "2026-09-20T11:00:00", 4)
    return tid, cid


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("PULSE_DEMO_SANDBOX", "true")


def _client(peer=LOCAL):
    return TestClient(dashboard.app, client=peer)


# --- Guards ---------------------------------------------------------------------


def test_off_by_default_every_route_404s(store, thread):
    tid, _ = thread
    client = _client()
    for path in ("/sandbox", "/sandbox/api/index", f"/sandbox/api/threads/{tid}",
                 f"/sandbox/f/c/tirzepatide_talk/{tid}", "/sandbox/assets/sandbox.js"):
        assert client.get(path).status_code == 404, path
    assert client.post("/sandbox/api/comments", json={"thread_id": tid, "body": "x"}).status_code == 404


def test_on_serves_loopback(store, thread, on):
    tid, _ = thread
    client = _client()
    assert client.get("/sandbox").status_code == 200
    assert client.get(f"/sandbox/f/c/tirzepatide_talk/{tid}").status_code == 200
    assert client.get(f"/sandbox/api/threads/{tid}").json()["thread"]["title"] == "Follow-up visits?"


def test_on_but_non_loopback_peer_404s(store, thread, on):
    tid, _ = thread
    client = _client(REMOTE)
    assert client.get("/sandbox").status_code == 404
    assert client.get(f"/sandbox/api/threads/{tid}").status_code == 404


def test_ignored_inside_containers(store, on, monkeypatch):
    monkeypatch.setenv("PULSE_REQUIRE_POSTGRES", "true")
    assert sandbox.sandbox_enabled() is False


def test_static_route_never_serves_sandbox_assets(store, on):
    # Sandbox assets only come through the guarded /sandbox/assets route.
    assert _client().get("/static/sandbox/sandbox.js").status_code == 404


def test_pages_carry_the_app_csp_and_the_banner(store, thread, on):
    tid, _ = thread
    resp = _client().get(f"/sandbox/f/c/tirzepatide_talk/{tid}")
    assert resp.headers["Content-Security-Policy"] == dashboard.SECURITY_HEADERS["Content-Security-Policy"]
    assert sandbox.BANNER in resp.text
    assert "<script>" not in resp.text and " style=" not in resp.text and "onclick" not in resp.text.lower()


def test_unknown_thread_page_404s(store, on):
    assert _client().get("/sandbox/f/c/tirzepatide_talk/999").status_code == 404
    assert _client().get("/sandbox/api/threads/999").status_code == 404


def test_startup_refuses_sandbox_off_loopback(store, on, monkeypatch):
    started = []
    monkeypatch.setattr(dashboard, "_serve", lambda h, p: started.append((h, p)))
    monkeypatch.setattr(dashboard.auth, "bind_error", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        dashboard.start_dashboard(port=5555, host="0.0.0.0")
    assert started == []


# --- Posting --------------------------------------------------------------------


def _csrf(client):
    return client.get("/sandbox/api/session").json()["csrf"]


def test_posting_needs_the_sandbox_csrf_token(store, thread, on):
    tid, _ = thread
    client = _client()
    assert client.post("/sandbox/api/comments", json={"thread_id": tid, "body": "hi"}).status_code == 403
    assert client.post("/sandbox/api/comments", json={"thread_id": tid, "body": "hi"},
                       headers={"X-Sandbox-CSRF": "wrong"}).status_code == 403


def test_posting_creates_a_brand_comment_with_a_permalink(store, thread, on):
    tid, cid = thread
    client = _client()
    resp = client.post("/sandbox/api/comments", json={"thread_id": tid, "parent_id": cid,
                                                      "body": "Disclosure: I work with WellPeps."},
                       headers={"X-Sandbox-CSRF": _csrf(client)})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    comment = store.comment(data["id"])
    assert comment["is_brand"] is True
    assert comment["author"] == sandbox.DEFAULT_BRAND_HANDLE
    assert comment["flair"] == sandbox.BRAND_FLAIR
    assert comment["parent_id"] == cid
    assert data["permalink"] == (f"http://testserver/sandbox/f/c/tirzepatide_talk/{tid}/comment/"
                                 f"{data['id']}#c{data['id']}")
    ref = sandbox_urls.parse(data["permalink"].replace("testserver", "127.0.0.1:5555"))
    assert (ref.thread_id, ref.comment_id) == (tid, data["id"])


def test_brand_handle_is_configurable(store, thread, on, monkeypatch):
    monkeypatch.setenv("PULSE_SANDBOX_BRAND_HANDLE", "Demo_Brand")
    tid, _ = thread
    client = _client()
    resp = client.post("/sandbox/api/comments", json={"thread_id": tid, "body": "hello"},
                       headers={"X-Sandbox-CSRF": _csrf(client)})
    assert store.comment(resp.json()["id"])["author"] == "Demo_Brand"


def test_posted_markup_is_stored_raw_and_returned_as_data(store, thread, on):
    tid, _ = thread
    client = _client()
    body = '<script>alert(1)</script> <img src=x onerror=alert(2)>'
    resp = client.post("/sandbox/api/comments", json={"thread_id": tid, "body": body},
                       headers={"X-Sandbox-CSRF": _csrf(client)})
    thread_json = client.get(f"/sandbox/api/threads/{tid}")
    assert thread_json.headers["content-type"].startswith("application/json")
    assert any(c["body"] == body for c in thread_json.json()["comments"])
    assert resp.status_code == 200


@pytest.mark.parametrize("body", ["", "   ", "x" * 10001])
def test_posting_rejects_empty_or_huge_bodies(store, thread, on, body):
    tid, _ = thread
    client = _client()
    resp = client.post("/sandbox/api/comments", json={"thread_id": tid, "body": body},
                       headers={"X-Sandbox-CSRF": _csrf(client)})
    assert resp.status_code in (400, 422)


def test_posting_rejects_a_parent_from_another_thread(store, thread, on):
    tid, cid = thread
    other = store.add_thread(kind="forum", community="tirzepatide_talk", title="Other", body="b",
                             author="a", created_at="2026-09-21T10:00:00", points=1)
    client = _client()
    resp = client.post("/sandbox/api/comments", json={"thread_id": other, "parent_id": cid, "body": "x"},
                       headers={"X-Sandbox-CSRF": _csrf(client)})
    assert resp.status_code == 400


def test_posting_is_never_a_get(store, thread, on):
    tid, _ = thread
    assert _client().get("/sandbox/api/comments").status_code in (404, 405)


# --- URLs -----------------------------------------------------------------------


def test_url_helpers_round_trip():
    url = "http://127.0.0.1:5555" + sandbox_urls.comment_path("forum", "glp1_journey", 7, 42)
    ref = sandbox_urls.parse(url)
    assert (ref.kind, ref.community, ref.thread_id, ref.comment_id) == ("forum", "glp1_journey", 7, 42)
    assert url.endswith("#c42")
    assert sandbox_urls.parse("http://localhost:5566/sandbox/p/3").kind == "photo"
    assert sandbox_urls.parse("http://127.0.0.1:5555/sandbox/r/9/comment/2").comment_id == 2
    assert sandbox_urls.parse("https://example.com/sandbox/p/3") is None
    assert sandbox_urls.parse("http://127.0.0.1:5555/api/p/3") is None
    assert sandbox_urls.is_sandbox_url("http://[::1]:5555/sandbox/f/c/x/1")


# --- No real platform names or trade dress in the sandbox assets -----------------


def test_sandbox_assets_name_no_real_platform():
    files = sorted(SANDBOX_WEB.glob("*"))
    assert {f.name for f in files} >= {"sandbox.html", "sandbox.css", "sandbox.js"}
    for path in files:
        text = path.read_text(encoding="utf-8").lower()
        for name in ("reddit", "instagram", "facebook", "tiktok", "subreddit", "snoo"):
            assert name not in text, f"{name!r} in {path.name}"


def test_sandbox_js_renders_through_escaping():
    js = (SANDBOX_WEB / "sandbox.js").read_text(encoding="utf-8")
    assert "function escHtml" in js
    assert "eval(" not in js and "document.write" not in js
