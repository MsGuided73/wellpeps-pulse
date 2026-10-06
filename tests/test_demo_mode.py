"""Pulse <-> DEMO sandbox integration: demo flags, the Review desk demo labels,
mark-posted with a sandbox link (demo mode only), the DEMO config banner, and
the full approve -> post loop with a TestClient."""

from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from harvey import dashboard, knowledge
from harvey.models import AuditEventType, MentionStatus
from harvey.sandbox import demo_config, urls as sandbox_urls
from harvey.sandbox.store import SandboxStore

from tests.dashboard_helpers import (
    CLEAN_IDS,
    CLEAN_REPLY,
    PASSWORD,
    REVIEWER,
    add_review_item,
    run,
    setup_app,
    teardown_app,
)

LOCAL = ("127.0.0.1", 50000)
REMOTE = ("203.0.113.9", 50000)
WEB = Path(dashboard.WEB_DIR)


@pytest.fixture
def app_state(tmp_path, monkeypatch):
    state, _ = setup_app(tmp_path, monkeypatch)
    monkeypatch.setenv("PULSE_SANDBOX_DB_PATH", str(tmp_path / "sandbox.db"))
    yield state
    teardown_app()


@pytest.fixture
def demo_cfg(tmp_path, monkeypatch):
    directory = demo_config.write_demo_config(tmp_path / "demo-config")
    monkeypatch.setenv("PULSE_CONFIG_DIR", str(directory))
    knowledge.reload()
    yield directory
    monkeypatch.setenv("PULSE_CONFIG_DIR", "")
    knowledge.reload()


@pytest.fixture
def sandbox_on(monkeypatch):
    monkeypatch.setenv("PULSE_DEMO_SANDBOX", "true")


def _login(peer=LOCAL) -> tuple[TestClient, str]:
    client = TestClient(dashboard.app, client=peer)
    resp = client.post("/api/login", json={"email": REVIEWER, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return client, resp.json()["csrf"]


def _post(client, csrf, path, body=None):
    return client.post(path, json=body or {}, headers={"X-CSRF-Token": csrf})


def _sandbox_thread(tmp_path) -> tuple[SandboxStore, int, int]:
    store = SandboxStore(tmp_path / "sandbox.db")
    store.add_community("tirzepatide_talk", "forum", "Tirzepatide talk", "Fictional.")
    tid = store.add_thread(kind="forum", community="tirzepatide_talk", title="Follow-ups?",
                           body="Which providers include follow-ups?", author="demo_op",
                           created_at="2026-09-20T10:00:00", points=9)
    cid = store.add_comment(tid, None, "demo_member", "Asking the same thing.", "2026-09-20T11:00:00", 3)
    return store, tid, cid


def _sandbox_mention(state, tmp_path, key="demo1") -> tuple[int, SandboxStore, int, int]:
    store, tid, cid = _sandbox_thread(tmp_path)
    mid = run(add_review_item(state, key))
    url = "http://127.0.0.1:5555" + sandbox_urls.comment_path("forum", "tirzepatide_talk", tid, cid)
    run(_set_url(state, mid, url))
    return mid, store, tid, cid


async def _set_url(state, mention_id: int, url: str) -> None:
    from harvey.urls import normalize_url

    async with state.connect() as db:
        await db.execute("UPDATE mentions SET url = ?, url_norm = ? WHERE id = ?", (url, normalize_url(url), mention_id))
        await db.commit()


def _events(state, mid):
    return run(state.list_audit(mid))


# --- Flags --------------------------------------------------------------------------


def test_demo_flags_off_by_default(app_state):
    client, _ = _login()
    assert client.get("/api/demo").json() == {"sandbox": False, "demo_config": False, "brand_handle": ""}


def test_demo_flags_on_for_loopback_only(app_state, sandbox_on):
    local, _ = _login(LOCAL)
    assert local.get("/api/demo").json()["sandbox"] is True
    remote, _ = _login(REMOTE)
    assert remote.get("/api/demo").json()["sandbox"] is False


def test_demo_config_banner_flag_and_markup(app_state, demo_cfg):
    client, _ = _login()
    assert client.get("/api/demo").json()["demo_config"] is True
    html = (WEB / "index.html").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'id="demo-config-banner"' in html
    assert "DEMO CONFIG — claims marked approved for demonstration only" in html + js


def test_real_config_is_not_a_demo_config():
    assert knowledge.is_demo_config() is False


# --- Review desk labels ------------------------------------------------------------


def test_review_desk_has_the_demo_labels():
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert "Copy reply &amp; open demo post" in js
    assert "Copy reply &amp; open post" in js  # unchanged outside demo mode
    assert "function isSandboxUrl" in js and "DEMO.sandbox" in js
    assert "/demo-post" in js and "DEMO:" in js


# --- Mark posted with a sandbox link ---------------------------------------------


def _approve(client, csrf, mid):
    resp = _post(client, csrf, f"/api/mentions/{mid}/approve")
    assert resp.status_code == 200, resp.text


def test_mark_posted_refuses_a_sandbox_link_when_demo_is_off(app_state, tmp_path, demo_cfg):
    mid, _, tid, cid = _sandbox_mention(app_state, tmp_path)
    client, csrf = _login()
    _approve(client, csrf, mid)
    link = f"http://127.0.0.1:5555/sandbox/f/c/tirzepatide_talk/{tid}/comment/{cid}#c{cid}"

    resp = _post(client, csrf, f"/api/mentions/{mid}/mark-posted", {"posted_url": link})

    assert resp.status_code == 400
    assert "sandbox" in resp.json()["detail"]
    assert run(app_state.get_mention(mid)).status is MentionStatus.APPROVED


def test_mark_posted_accepts_a_sandbox_link_in_demo_mode(app_state, tmp_path, demo_cfg, sandbox_on):
    mid, _, tid, cid = _sandbox_mention(app_state, tmp_path)
    client, csrf = _login()
    _approve(client, csrf, mid)
    link = f"http://127.0.0.1:5566/sandbox/f/c/tirzepatide_talk/{tid}/comment/{cid}#c{cid}"

    resp = _post(client, csrf, f"/api/mentions/{mid}/mark-posted", {"posted_url": link})

    assert resp.status_code == 200, resp.text
    posted = _events(app_state, mid)[-1]
    assert posted.event is AuditEventType.POSTED and posted.verdict["posted_url"] == link


def test_mark_posted_still_accepts_real_links_when_demo_is_off(app_state, demo_cfg):
    mid = run(add_review_item(app_state, "real1"))
    client, csrf = _login()
    _approve(client, csrf, mid)
    resp = _post(client, csrf, f"/api/mentions/{mid}/mark-posted",
                 {"posted_url": "https://www.example.com/thread/1#reply"})
    assert resp.status_code == 200


# --- The full demo loop ------------------------------------------------------------


def test_demo_approve_then_post_in_sandbox_then_mark_posted(app_state, tmp_path, demo_cfg, sandbox_on):
    mid, store, tid, cid = _sandbox_mention(app_state, tmp_path)
    client, csrf = _login()

    # Review desk: approve (DEMO config: claims marked approved), copy & open the post.
    _approve(client, csrf, mid)
    assert _post(client, csrf, f"/api/mentions/{mid}/copied").status_code == 200
    detail = client.get(f"/api/mentions/{mid}").json()
    ref = sandbox_urls.parse(detail["mention"]["url"])
    assert client.get(sandbox_urls.thread_path("forum", ref.community, ref.thread_id)).status_code == 200

    # Sandbox: paste the approved reply under the mention's comment.
    token = client.get("/sandbox/api/session").json()["csrf"]
    posted = client.post("/sandbox/api/comments", json={"thread_id": ref.thread_id, "parent_id": ref.comment_id,
                                                        "body": detail["latest_draft"]["text"]},
                         headers={"X-Sandbox-CSRF": token}).json()
    comment = store.comment(posted["id"])
    assert comment["is_brand"] and comment["author"] == "WellPeps_Team" and comment["body"] == CLEAN_REPLY

    # Back in Pulse: mark posted with the sandbox comment link.
    resp = _post(client, csrf, f"/api/mentions/{mid}/mark-posted", {"posted_url": posted["permalink"]})
    assert resp.status_code == 200, resp.text
    assert run(app_state.get_mention(mid)).status is MentionStatus.POSTED
    events = [e.event for e in _events(app_state, mid)]
    assert events[-3:] == [AuditEventType.APPROVED, AuditEventType.COPIED, AuditEventType.POSTED]


def test_demo_post_shortcut_posts_and_marks_posted(app_state, tmp_path, demo_cfg, sandbox_on):
    mid, store, tid, cid = _sandbox_mention(app_state, tmp_path)
    client, csrf = _login()
    _approve(client, csrf, mid)

    resp = _post(client, csrf, f"/api/mentions/{mid}/demo-post")

    assert resp.status_code == 200, resp.text
    posted_url = resp.json()["posted_url"]
    assert posted_url.startswith("http://testserver/sandbox/")  # host/port from the request
    ref = sandbox_urls.parse_path(urlsplit(posted_url).path)
    comment = store.comment(ref.comment_id)
    assert comment["parent_id"] == cid and comment["is_brand"] and comment["body"] == CLEAN_REPLY
    assert run(app_state.get_mention(mid)).status is MentionStatus.POSTED
    last = _events(app_state, mid)[-1]
    assert last.actor == REVIEWER and last.verdict["posted_url"] == posted_url


def test_demo_post_shortcut_needs_an_approved_reply(app_state, tmp_path, demo_cfg, sandbox_on):
    mid, _, _, _ = _sandbox_mention(app_state, tmp_path)
    client, csrf = _login()
    assert _post(client, csrf, f"/api/mentions/{mid}/demo-post").status_code == 409


def test_demo_post_shortcut_is_404_when_sandbox_is_off(app_state, tmp_path, demo_cfg):
    mid, _, _, _ = _sandbox_mention(app_state, tmp_path)
    client, csrf = _login()
    _approve(client, csrf, mid)
    assert _post(client, csrf, f"/api/mentions/{mid}/demo-post").status_code == 404
    assert run(app_state.get_mention(mid)).status is MentionStatus.APPROVED


def test_demo_post_shortcut_needs_csrf(app_state, tmp_path, demo_cfg, sandbox_on):
    mid, _, _, _ = _sandbox_mention(app_state, tmp_path)
    client, csrf = _login()
    _approve(client, csrf, mid)
    assert client.post(f"/api/mentions/{mid}/demo-post", json={}).status_code == 403


def test_approval_rules_unchanged_without_the_demo_config(app_state, tmp_path, sandbox_on):
    mid, _, _, _ = _sandbox_mention(app_state, tmp_path)
    client, csrf = _login()
    resp = _post(client, csrf, f"/api/mentions/{mid}/approve")
    assert resp.status_code == 409 and "not approved for publishing" in resp.json()["detail"]
    for cid in CLEAN_IDS:
        assert cid in resp.json()["detail"]
