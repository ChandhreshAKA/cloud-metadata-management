"""GET /files (list) and GET /files/{id}/placement (stored explanation), through the real app.

Fake database for the logic; the SQL behind them is proved against Oracle in
tests/integration/test_oracle_file_views.py.
"""

from tests.unit.test_files_api import upload


def test_both_endpoints_need_a_token(client):
    assert client.get("/files").status_code == 401
    assert client.get("/files/1/placement").status_code == 401


def test_admin_and_auditor_have_no_own_files_so_they_are_refused(client, as_role):
    for role in ("ADMIN", "AUDITOR"):
        headers = as_role(role)
        assert client.get("/files", headers=headers).status_code == 403
        assert client.get("/files/1/placement", headers=headers).status_code == 403


def test_list_shows_only_my_files_newest_first_and_hides_deleted(client, as_role):
    mine, other = as_role("USER"), as_role("USER")
    first = upload(client, mine, b"one", name="a.pdf").json()
    second = upload(client, mine, b"two", name="b.pdf").json()
    third = upload(client, mine, b"three", name="c.pdf").json()
    upload(client, other, b"theirs", name="theirs.pdf")
    assert client.delete(f"/files/{second['file_id']}?confirm=true", headers=mine).status_code == 200

    body = client.get("/files", headers=mine).json()
    assert body["count"] == 2 and "SIMULATED" in body["storage"]
    assert [f["file_name"] for f in body["files"]] == ["c.pdf", "a.pdf"]  # newest first, b deleted
    row = body["files"][0]
    assert row["file_id"] == third["file_id"]
    assert row["provider_code"] == third["provider_code"] and row["region_code"] == third["region"]
    assert row["tier_code"] == third["tier"] and row["status"] == "ACTIVE"
    assert row["content_sha256"] == third["content_sha256"]
    assert first["file_id"] in {f["file_id"] for f in body["files"]}


def test_list_is_empty_for_a_new_user(client, as_role):
    assert client.get("/files", headers=as_role("USER")).json() == {
        "count": 0, "storage": "SIMULATED local folders", "files": [],
    }  # fmt: skip


def test_placement_returns_the_explanation_saved_at_upload(client, as_role):
    headers = as_role("USER")
    up = upload(client, headers, b"hello", name="r.pdf", level="HIGH").json()
    body = client.get(f"/files/{up['file_id']}/placement", headers=headers).json()
    assert body["explanation"] == up["selection"]["explanation"]
    assert body["preset"] == up["selection"]["preset"]
    assert body["selected"]["provider_code"] == up["provider_code"]
    assert body["selected"]["region_code"] == up["region"]
    assert body["selected"]["composite"] == body["ranked"][0]["composite"]  # the winner is rank 1
    scores = [float(r["composite"]) for r in body["ranked"]]
    assert scores == sorted(scores, reverse=True) and body["ranked"][0]["rank"] == 1
    assert sum(float(v) for v in body["weights"].values()) > 0.99 and "SIMULATED" in body["storage"]


def test_placement_lists_every_rejected_region_with_its_reason(client, as_role):
    headers = as_role("USER")
    up = upload(client, headers, b"hello", level="HIGH").json()
    body = client.get(f"/files/{up['file_id']}/placement", headers=headers).json()
    assert body["rejected"], "HIGH security rejects at least one illustrative region"
    assert all(r["reason"] for r in body["rejected"])
    ranked = {(r["provider_code"], r["region_code"]) for r in body["ranked"]}
    rejected = {(r["provider_code"], r["region_code"]) for r in body["rejected"]}
    assert not ranked & rejected  # each region appears exactly once


def test_placement_of_someone_elses_file_is_403_and_logged(client, db, as_role):
    owner, other = as_role("USER"), as_role("USER")
    up = upload(client, owner, b"secret").json()
    response = client.get(f"/files/{up['file_id']}/placement", headers=other)
    assert response.status_code == 403
    assert [e["details"]["reason"] for e in db.audit_of("ACCESS_DENIED")] == ["not_owner"]
    assert db.state["access_logs"][-1]["outcome"] == "DENIED"


def test_placement_of_missing_and_deleted_files(client, as_role):
    headers = as_role("USER")
    assert client.get("/files/999999/placement", headers=headers).status_code == 404
    up = upload(client, headers, b"x").json()
    client.delete(f"/files/{up['file_id']}?confirm=true", headers=headers)
    assert client.get(f"/files/{up['file_id']}/placement", headers=headers).status_code == 410


def test_the_views_are_read_only(client, db, as_role):
    headers = as_role("USER")
    up = upload(client, headers, b"x").json()
    before = {k: repr(v) for k, v in db.state.items() if k not in ("audit", "last_login")}
    for _ in range(3):
        assert client.get("/files", headers=headers).status_code == 200
        assert client.get(f"/files/{up['file_id']}/placement", headers=headers).status_code == 200
    assert {k: repr(v) for k, v in db.state.items() if k not in ("audit", "last_login")} == before
    for method in (client.post, client.put, client.patch):
        assert method(f"/files/{up['file_id']}/placement", headers=headers).status_code == 405
