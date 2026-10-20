"""Removing a Knowledge library document (owner request 2026-10-20: "I don't want
reference to a doc that I have chosen to remove").

Remove (admin only) deletes the document, its older versions and any edit of it
still waiting for review; no other document keeps a "replaces" link to it, and
the drafter stops using it at once. The append-only audit log keeps one
"removed" event. Temp DB; no network, no `claude` calls."""

import pytest

from harvey import knowledge_docs
from tests.dashboard_helpers import ADMIN, REVIEWER, VIEWER, client_for, post, run
from tests.test_knowledge_docs import _audit_rows, b64, upload_body
from tests.test_knowledge_docs import env  # noqa: F401  (fixture)


def _active(client, csrf, text="Keep it warm and plain.", **overrides) -> dict:
    doc = post(client, csrf, "/api/knowledge/documents", upload_body(content_base64=b64(text), **overrides)).json()
    return post(client, csrf, f"/api/knowledge/documents/{doc['id']}/approve").json()


def _edit(client, csrf, doc_id, text) -> dict:
    return post(client, csrf, f"/api/knowledge/documents/{doc_id}/edit", {"text": text}).json()


def _ids(client) -> set[int]:
    return {d["id"] for d in client.get("/api/knowledge/documents").json()["documents"]}


def _remove(client, csrf, doc_id):
    return post(client, csrf, f"/api/knowledge/documents/{doc_id}/remove")


def test_remove_deletes_the_document_and_the_drafter_stops_using_it(env):
    admin, csrf = client_for(ADMIN)
    doc = _active(admin, csrf, text="Old guidance nobody uses.")
    assert "Old guidance" in run(knowledge_docs.reference_block(env))

    resp = _remove(admin, csrf, doc["id"])

    assert resp.status_code == 200 and resp.json() == {"removed": [doc["id"]]}
    assert doc["id"] not in _ids(admin)
    assert admin.get(f"/api/knowledge/documents/{doc['id']}").status_code == 404
    assert admin.get(f"/api/knowledge/documents/{doc['id']}/text").status_code == 404
    assert "Old guidance" not in run(knowledge_docs.reference_block(env))


@pytest.mark.parametrize("decision", [None, "reject"])
def test_documents_in_review_or_rejected_can_be_removed(env, decision):
    admin, csrf = client_for(ADMIN)
    doc = post(admin, csrf, "/api/knowledge/documents", upload_body()).json()
    if decision:
        post(admin, csrf, f"/api/knowledge/documents/{doc['id']}/{decision}")

    assert _remove(admin, csrf, doc["id"]).json() == {"removed": [doc["id"]]}
    assert _ids(admin) == set()


def test_removing_a_document_removes_its_older_versions_and_pending_edit(env):
    admin, csrf = client_for(ADMIN)
    v1 = _active(admin, csrf, text="Version one.")
    v2 = _edit(admin, csrf, v1["id"], "Version two.")
    post(admin, csrf, f"/api/knowledge/documents/{v2['id']}/approve")           # v1 is now "Replaced"
    v3 = _edit(admin, csrf, v2["id"], "Version three, waiting.")
    other = _active(admin, csrf, text="An unrelated document.", filename="other.md")

    removed = _remove(admin, csrf, v2["id"]).json()["removed"]

    assert sorted(removed) == sorted([v1["id"], v2["id"], v3["id"]])
    assert _ids(admin) == {other["id"]}
    block = run(knowledge_docs.reference_block(env))
    assert "Version" not in block and "unrelated" in block


def test_removing_a_pending_edit_keeps_the_version_in_use(env):
    admin, csrf = client_for(ADMIN)
    current = _active(admin, csrf, text="The approved wording.")
    edit = _edit(admin, csrf, current["id"], "A change we no longer want.")

    assert _remove(admin, csrf, edit["id"]).json() == {"removed": [edit["id"]]}
    assert _ids(admin) == {current["id"]}
    assert admin.get(f"/api/knowledge/documents/{current['id']}").json()["status"] == "active"
    assert _edit(admin, csrf, current["id"], "A new edit is allowed again.")["status"] == "in_review"


def test_removing_an_old_version_leaves_no_link_to_it(env):
    admin, csrf = client_for(ADMIN)
    v1 = _active(admin, csrf, text="Version one.")
    v2 = _edit(admin, csrf, v1["id"], "Version two.")
    post(admin, csrf, f"/api/knowledge/documents/{v2['id']}/approve")

    assert _remove(admin, csrf, v1["id"]).json() == {"removed": [v1["id"]]}

    kept = admin.get(f"/api/knowledge/documents/{v2['id']}").json()
    assert kept["status"] == "active" and kept["supersedes_id"] is None


def test_unknown_document_is_404(env):
    admin, csrf = client_for(ADMIN)

    assert _remove(admin, csrf, 999).status_code == 404


def test_only_admins_remove(env):
    admin, acsrf = client_for(ADMIN)
    doc = _active(admin, acsrf)
    for role in (VIEWER, REVIEWER):
        client, csrf = client_for(role)
        assert _remove(client, csrf, doc["id"]).status_code == 403
    assert admin.post(f"/api/knowledge/documents/{doc['id']}/remove").status_code == 403      # no CSRF token
    assert client_for()[0].post(f"/api/knowledge/documents/{doc['id']}/remove").status_code == 401
    assert doc["id"] in _ids(admin)


def test_removal_is_audited_once_without_text(env):
    admin, csrf = client_for(ADMIN)
    doc = _active(admin, csrf, text="words that must not be logged")

    _remove(admin, csrf, doc["id"])

    rows = run(_audit_rows(env, "knowledge_doc"))
    last = rows[-1]["verdict"]
    assert last["action"] == "removed" and last["document_id"] == doc["id"]
    assert last["previous_status"] == "active" and last["removed_ids"] == [doc["id"]]
    assert all("must not be logged" not in str(r) for r in rows)
