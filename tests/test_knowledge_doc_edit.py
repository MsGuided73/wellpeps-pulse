"""Editing a Knowledge library document (owner request 2026-10-20).

An edit never changes a document in place: it stores a new version ``in_review``
that supersedes the original, so the drafter keeps the approved text until an
admin approves the edit. Temp DB; no network, no `claude` calls."""

import json

import pytest

from harvey import knowledge_docs
from tests.dashboard_helpers import ADMIN, REVIEWER, VIEWER, client_for, post, run
from tests.test_knowledge_docs import _audit_rows, b64, upload_body
from tests.test_knowledge_docs import env  # noqa: F401  (fixture)


def _active(client, csrf, **overrides) -> dict:
    doc = post(client, csrf, "/api/knowledge/documents", upload_body(**overrides)).json()
    return post(client, csrf, f"/api/knowledge/documents/{doc['id']}/approve").json()


def _edit(client, csrf, doc_id, **body):
    return post(client, csrf, f"/api/knowledge/documents/{doc_id}/edit", body)


def test_full_text_route_returns_every_character(env):
    admin, csrf = client_for(ADMIN)
    long_text = "Warm and plain. " * 300          # longer than the 2000-character preview
    doc = _active(admin, csrf, content_base64=b64(long_text))

    body = admin.get(f"/api/knowledge/documents/{doc['id']}/text").json()

    assert body["id"] == doc["id"] and body["text"] == long_text.strip()
    assert client_for(VIEWER)[0].get(f"/api/knowledge/documents/{doc['id']}/text").status_code == 200
    assert admin.get("/api/knowledge/documents/999/text").status_code == 404


def test_edit_makes_a_new_version_in_review_and_keeps_the_original_in_use(env):
    admin, csrf = client_for(ADMIN)
    original = _active(admin, csrf, filename="voice.md")

    resp = _edit(admin, csrf, original["id"], text="Keep it warm, plain and short.", note="shorter")

    assert resp.status_code == 200
    edited = resp.json()
    assert edited["id"] != original["id"] and edited["status"] == "in_review"
    assert edited["supersedes_id"] == original["id"] and edited["filename"] == "voice.md"
    assert edited["note"] == "shorter" and edited["kind"] == original["kind"]
    assert admin.get(f"/api/knowledge/documents/{original['id']}").json()["status"] == "active"
    block = run(knowledge_docs.reference_block(env))
    assert "Keep it warm and plain." in block and "short." not in block


def test_approving_the_edit_retires_the_original(env):
    admin, csrf = client_for(ADMIN)
    original = _active(admin, csrf)
    edited = _edit(admin, csrf, original["id"], text="New approved wording here.").json()

    post(admin, csrf, f"/api/knowledge/documents/{edited['id']}/approve")

    assert admin.get(f"/api/knowledge/documents/{original['id']}").json()["status"] == "superseded"
    block = run(knowledge_docs.reference_block(env))
    assert "New approved wording here." in block and "Keep it warm and plain." not in block


def test_details_can_change_without_the_text(env):
    admin, csrf = client_for(ADMIN)
    original = _active(admin, csrf, content_base64=b64("Same words."))

    edited = _edit(admin, csrf, original["id"], kind="approved_messaging", authority="must_follow",
                   approved_by="Derek Goldberg", programs=["Weight Loss"], effective_date="2026-11-01").json()

    assert (edited["kind"], edited["authority"], edited["approved_by"]) == ("approved_messaging", "must_follow",
                                                                            "Derek Goldberg")
    assert edited["programs"] == ["Weight Loss"] and edited["effective_date"] == "2026-11-01"
    assert admin.get(f"/api/knowledge/documents/{edited['id']}/text").json()["text"] == "Same words."


def test_pasting_text_into_a_pending_pdf_makes_it_usable(env, monkeypatch):
    monkeypatch.setattr(knowledge_docs, "_pdf_text", lambda raw: None)
    admin, csrf = client_for(ADMIN)
    pdf = post(admin, csrf, "/api/knowledge/documents",
               upload_body(filename="guide.pdf", content_base64=b64(b"%PDF-1.4 binary"))).json()
    assert pdf["extraction"] == "pending"

    edited = _edit(admin, csrf, pdf["id"], text="The text of the guide, pasted in.").json()

    assert edited["extraction"] == "done" and edited["text_length"] > 0
    assert admin.get(f"/api/knowledge/documents/{pdf['id']}").json()["status"] == "superseded"


def test_editing_a_document_still_in_review_replaces_it(env):
    reviewer, csrf = client_for(REVIEWER)
    draft = post(reviewer, csrf, "/api/knowledge/documents", upload_body()).json()

    edited = _edit(reviewer, csrf, draft["id"], text="Fixed a typo before review.").json()

    assert edited["status"] == "in_review" and edited["supersedes_id"] == draft["id"]
    assert reviewer.get(f"/api/knowledge/documents/{draft['id']}").json()["status"] == "superseded"


def test_a_rejected_document_can_be_fixed_and_sent_again(env):
    admin, csrf = client_for(ADMIN)
    doc = post(admin, csrf, "/api/knowledge/documents", upload_body()).json()
    post(admin, csrf, f"/api/knowledge/documents/{doc['id']}/reject")

    edited = _edit(admin, csrf, doc["id"], text="Corrected wording.")

    assert edited.status_code == 200 and edited.json()["status"] == "in_review"
    assert admin.get(f"/api/knowledge/documents/{doc['id']}").json()["status"] == "rejected"


@pytest.mark.parametrize("body,fragment", [
    ({}, "nothing changed"),
    ({"text": "Keep it warm and plain."}, "nothing changed"),
    ({"text": "   "}, "text"),
    ({"text": 5}, "text"),
    ({"kind": "poem"}, "kind"),
    ({"authority": "must_follow", "approved_by": ""}, "approved_by"),
    ({"effective_date": "soon"}, "effective_date"),
    ({"programs": "Weight Loss"}, "programs"),
])
def test_invalid_edits_are_400(env, body, fragment):
    admin, csrf = client_for(ADMIN)
    original = _active(admin, csrf)

    resp = _edit(admin, csrf, original["id"], **body)

    assert resp.status_code == 400 and fragment in resp.json()["detail"]


def test_text_over_the_limit_is_413(env, monkeypatch):
    monkeypatch.setattr(knowledge_docs, "MAX_TEXT_CHARS", 50)
    admin, csrf = client_for(ADMIN)
    original = _active(admin, csrf)

    assert _edit(admin, csrf, original["id"], text="x" * 51).status_code == 413


def test_only_one_edit_can_wait_per_document(env):
    admin, csrf = client_for(ADMIN)
    original = _active(admin, csrf)
    first = _edit(admin, csrf, original["id"], text="First edit.").json()

    second = _edit(admin, csrf, original["id"], text="Second edit.")

    assert second.status_code == 409 and str(first["id"]) in second.json()["detail"]


def test_a_replaced_document_cannot_be_edited(env):
    admin, csrf = client_for(ADMIN)
    original = _active(admin, csrf)
    edited = _edit(admin, csrf, original["id"], text="Newer.").json()
    post(admin, csrf, f"/api/knowledge/documents/{edited['id']}/approve")

    resp = _edit(admin, csrf, original["id"], text="Editing the old one.")

    assert resp.status_code == 409 and "replaced" in resp.json()["detail"]
    assert _edit(admin, csrf, 999, text="x").status_code == 404


def test_roles_and_csrf(env):
    admin, acsrf = client_for(ADMIN)
    original = _active(admin, acsrf)
    viewer, vcsrf = client_for(VIEWER)
    assert _edit(viewer, vcsrf, original["id"], text="Viewer edit.").status_code == 403
    assert admin.post(f"/api/knowledge/documents/{original['id']}/edit", json={"text": "x"}).status_code == 403
    reviewer, rcsrf = client_for(REVIEWER)
    edited = _edit(reviewer, rcsrf, original["id"], text="Reviewer edit.").json()
    assert post(reviewer, rcsrf, f"/api/knowledge/documents/{edited['id']}/approve").status_code == 403
    assert client_for()[0].post(f"/api/knowledge/documents/{original['id']}/edit", json={}).status_code == 401


def test_edits_are_audited_without_text(env):
    admin, csrf = client_for(ADMIN)
    original = _active(admin, csrf)

    _edit(admin, csrf, original["id"], text="private new wording", note="tightened")

    rows = run(_audit_rows(env, "knowledge_doc"))
    edit = rows[-1]["verdict"]
    assert edit["action"] == "edited" and edit["source_id"] == original["id"]
    assert set(edit["changed"]) == {"text", "note"}
    assert all("private new wording" not in json.dumps(r) for r in rows)
