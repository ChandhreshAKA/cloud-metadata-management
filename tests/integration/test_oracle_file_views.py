"""GET /files and GET /files/{id}/placement against REAL Oracle (the SQL behind the dashboard)."""

from __future__ import annotations

import os

from tests.integration.helpers import register_and_login, scalar

OCTET = {"Content-Type": "application/octet-stream"}


def upload(client, headers, data, name, level="HIGH"):
    response = client.post(
        "/files", params={"file_name": name, "security_level": level}, content=data,
        headers={**headers, **OCTET},
    )  # fmt: skip
    assert response.status_code == 201, response.text
    return response.json()


def test_list_and_placement_match_what_upload_stored(client, database):
    _, headers = register_and_login(client)
    first = upload(client, headers, os.urandom(64), "view_a.pdf")
    second = upload(client, headers, os.urandom(64), "view_b.pdf")

    listing = client.get("/files", headers=headers).json()
    assert listing["count"] == 2
    assert [f["file_id"] for f in listing["files"]] == [second["file_id"], first["file_id"]]
    row = listing["files"][1]
    assert (row["file_name"], row["status"], row["tier_code"]) == ("view_a.pdf", "ACTIVE", "HOT")
    assert (row["provider_code"], row["region_code"]) == (first["provider_code"], first["region"])
    assert row["content_sha256"] == first["content_sha256"] and row["uploaded_at"]

    body = client.get(f"/files/{first['file_id']}/placement", headers=headers).json()
    assert body["explanation"] == first["selection"]["explanation"]
    assert body["selected"]["provider_code"] == first["provider_code"]
    assert body["ranked"][0]["rank"] == 1
    scores = [float(r["composite"]) for r in body["ranked"]]
    assert scores == sorted(scores, reverse=True)
    stored = scalar(
        database,
        "SELECT COUNT(*) FROM selection_candidates c JOIN selection_decisions d "
        "ON d.decision_id = c.decision_id WHERE d.file_id = :f",
        {"f": first["file_id"]},
    )
    assert len(body["ranked"]) + len(body["rejected"]) == stored  # every candidate is shown once
    assert all(r["reason"] for r in body["rejected"])

    for item in (first, second):
        assert client.delete(f"/files/{item['file_id']}?confirm=true", headers=headers).status_code == 200
    assert client.get("/files", headers=headers).json()["count"] == 0  # deleted files disappear


def test_another_user_cannot_read_the_placement(client):
    _, owner = register_and_login(client)
    _, other = register_and_login(client)
    up = upload(client, owner, os.urandom(32), "private_view.pdf")
    assert client.get(f"/files/{up['file_id']}/placement", headers=other).status_code == 403
    assert client.get("/files", headers=other).json()["count"] == 0
    client.delete(f"/files/{up['file_id']}?confirm=true", headers=owner)
