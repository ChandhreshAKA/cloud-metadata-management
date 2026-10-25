"""INTEGRATION TESTS (chunk 4): encrypted upload, download and delete against a real Oracle database.

Storage is the SIMULATED adapter in a temporary folder. Needs MASTER_KEY (and the other settings)
in the environment or .env; see tests/integration/conftest.py. Test files are owned by itest users.
"""

from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.db.errors import DatabaseError
from app.main import create_app
from tests.integration.helpers import register_and_login, scalar

pytestmark = pytest.mark.integration

OCTET = {"Content-Type": "application/octet-stream"}
MARKER = b"TOP-SECRET-MARKER-"


def up(client, headers, data, name="report.pdf", level="MEDIUM", **params):
    query = {"file_name": name, "security_level": level, **params}
    return client.post("/files", params=query, content=data, headers={**headers, **OCTET})


def down(client, headers, file_id):
    return client.get(f"/files/{file_id}/download", headers=headers)


def objects(storage):
    return sorted(p for p in storage.real.root.rglob("*") if p.is_file())


def object_file(storage, uploaded):
    name = uploaded["object_key"].split("/")[-1]
    [match] = [p for p in objects(storage) if p.name == name]
    return match


def uid(client, headers):
    return client.get("/auth/me", headers=headers).json()["user_id"]


@pytest.fixture(scope="module")
def owner(client, database):
    return register_and_login(client, database=database)[1]


@pytest.fixture(scope="module")
def other(client, database):
    return register_and_login(client, database=database)[1]


class FailingFiles:
    """Delegates to the real file repository but makes insert_file fail (a forced DB failure)."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def insert_file(self, *args, **kwargs):
        raise DatabaseError("injected: database write failed")


class FailingDatabase:
    """The real database, except that metadata inserts fail AFTER the object has been stored."""

    def __init__(self, real):
        self._real = real

    @contextmanager
    def transaction(self):
        with self._real.transaction() as session:
            session.files = FailingFiles(session.files)
            yield session

    def ping(self):
        self._real.ping()

    def close(self):
        pass


# ---- the happy path, checked row by row in Oracle --------------------------------------------
def test_round_trip_and_every_metadata_row(client, database, storage, owner):
    data = MARKER * 300 + os.urandom(1000)
    response = up(client, owner, data, name="report.pdf", level="MEDIUM", preset="BALANCED")
    assert response.status_code == 201, response.text
    body, file_id, owner_id = response.json(), response.json()["file_id"], uid(client, owner)

    got = down(client, owner, file_id)
    assert got.status_code == 200 and got.content == data

    f = {"f": file_id}
    assert (
        scalar(database, "SELECT content_sha256 FROM files WHERE file_id = :f", f)
        == hashlib.sha256(data).hexdigest()
    )
    assert scalar(database, "SELECT file_type FROM files WHERE file_id = :f", f) == "PDF"
    assert scalar(database, "SELECT status FROM files WHERE file_id = :f", f) == "ACTIVE"
    assert scalar(database, "SELECT owner_id FROM files WHERE file_id = :f", f) == owner_id
    assert scalar(database, "SELECT file_size_bytes FROM files WHERE file_id = :f", f) == len(data)

    wrapped = scalar(database, "SELECT wrapped_dek FROM file_encryption WHERE file_id = :f", f)
    nonce = scalar(database, "SELECT nonce FROM file_encryption WHERE file_id = :f", f)
    assert len(wrapped) == 60 and len(nonce) == 12
    assert (
        scalar(database, "SELECT algorithm FROM file_encryption WHERE file_id = :f", f)
        == "AES-256-GCM"
    )
    assert (
        scalar(database, "SELECT master_key_version FROM file_encryption WHERE file_id = :f", f)
        == 1
    )

    stored = object_file(storage, body).read_bytes()
    assert MARKER not in stored and len(stored) == len(data) + 16
    assert (
        scalar(database, "SELECT ciphertext_sha256 FROM file_encryption WHERE file_id = :f", f)
        == hashlib.sha256(stored).hexdigest()
    )

    assert (
        scalar(
            database,
            "SELECT COUNT(*) FROM storage_locations WHERE file_id = :f AND is_primary = 'Y' AND replica_status = 'OK' AND tier_code = 'HOT'",
            f,
        )
        == 1
    )
    assert (
        scalar(database, "SELECT object_key FROM storage_locations WHERE file_id = :f", f)
        == body["object_key"]
    )
    region_code = scalar(
        database,
        "SELECT r.region_code FROM storage_locations sl JOIN provider_regions r ON r.region_id = sl.region_id WHERE sl.file_id = :f",
        f,
    )
    assert region_code == body["region"]

    assert (
        scalar(
            database,
            "SELECT COUNT(*) FROM selection_decisions WHERE file_id = :f AND profile_name = 'BALANCED'",
            f,
        )
        == 1
    )
    candidates = scalar(
        database,
        "SELECT COUNT(*) FROM selection_candidates c JOIN selection_decisions d ON d.decision_id = c.decision_id WHERE d.file_id = :f",
        f,
    )
    assert candidates == scalar(database, "SELECT COUNT(*) FROM provider_regions")
    assert (
        scalar(
            database,
            "SELECT COUNT(*) FROM selection_candidates c JOIN selection_decisions d ON d.decision_id = c.decision_id WHERE d.file_id = :f AND c.excluded_reason IS NULL AND c.total_score BETWEEN 0 AND 1",
            f,
        )
        >= 1
    )

    for action in ("UPLOAD", "DOWNLOAD"):
        latency = scalar(
            database,
            "SELECT latency_ms FROM access_logs WHERE file_id = :f AND action = :a AND outcome = 'SUCCESS'",
            {**f, "a": action},
        )
        assert isinstance(latency, D) and latency >= 0
    assert (
        scalar(
            database,
            "SELECT COUNT(*) FROM audit_events WHERE event_type = 'FILE_UPLOADED' AND target_type = 'FILE' AND target_id = :f AND actor_user_id = :u",
            {**f, "u": owner_id},
        )
        == 1
    )


def test_unicode_name_and_the_255_byte_limit(client, database, storage, owner):
    name = "résumé 日本語.pdf"
    body = up(client, owner, b"unicode name", name=name).json()
    assert (
        scalar(database, "SELECT file_name FROM files WHERE file_id = :f", {"f": body["file_id"]})
        == name
    )
    assert down(client, owner, body["file_id"]).content == b"unicode name"
    before = len(objects(storage))
    too_long = up(client, owner, b"x", name="é" * 128)  # 256 bytes
    assert too_long.status_code == 422 and len(objects(storage)) == before
    assert up(client, owner, b"x", name="n" * 251 + ".pdf").status_code == 201  # exactly 255 bytes


# ---- tamper detection against Oracle-held hashes ---------------------------------------------
def test_flipping_one_stored_byte_fails_and_is_logged(client, database, storage, owner):
    data = os.urandom(400)
    body = up(client, owner, data).json()
    path = object_file(storage, body)
    original = path.read_bytes()
    tampered = bytearray(original)
    tampered[len(original) // 2] ^= 0x01
    path.write_bytes(bytes(tampered))

    response = down(client, owner, body["file_id"])
    assert response.status_code == 502 and "integrity check failed" in response.json()["detail"]
    assert (
        scalar(
            database,
            "SELECT COUNT(*) FROM access_logs WHERE file_id = :f AND action = 'DOWNLOAD' AND outcome = 'FAILURE'",
            {"f": body["file_id"]},
        )
        == 1
    )
    path.write_bytes(original)
    assert down(client, owner, body["file_id"]).content == data


def test_a_missing_object_is_a_clear_404(client, database, storage, owner):
    body = up(client, owner, b"will vanish").json()
    object_file(storage, body).unlink()
    response = down(client, owner, body["file_id"])
    assert (
        response.status_code == 404
        and "missing from the storage provider" in response.json()["detail"]
    )


# ---- authorisation ---------------------------------------------------------------------------
def test_wrong_user_is_denied_and_the_denial_is_recorded_in_oracle(client, database, owner, other):
    body = up(client, owner, b"mine").json()
    other_id = uid(client, other)
    assert down(client, other, body["file_id"]).status_code == 403
    assert (
        client.delete(
            f"/files/{body['file_id']}", params={"confirm": "true"}, headers=other
        ).status_code
        == 403
    )
    args = {"u": other_id, "f": body["file_id"]}
    assert (
        scalar(
            database,
            "SELECT COUNT(*) FROM audit_events WHERE actor_user_id = :u AND event_type = 'ACCESS_DENIED' AND target_type = 'FILE' AND target_id = :f",
            args,
        )
        == 2
    )
    assert (
        scalar(
            database,
            "SELECT COUNT(*) FROM access_logs WHERE user_id = :u AND file_id = :f AND outcome = 'DENIED'",
            args,
        )
        == 2
    )
    assert (
        scalar(database, "SELECT status FROM files WHERE file_id = :f", {"f": body["file_id"]})
        == "ACTIVE"
    )


# ---- forced failures against the real database -----------------------------------------------
def test_forced_storage_failure_leaves_no_rows_in_oracle(client, database, storage, owner):
    owner_id = uid(client, owner)
    files_before = scalar(
        database, "SELECT COUNT(*) FROM files WHERE owner_id = :o", {"o": owner_id}
    )
    logs_before = scalar(
        database, "SELECT COUNT(*) FROM access_logs WHERE user_id = :o", {"o": owner_id}
    )
    objects_before = objects(storage)
    storage.put_fails = True
    try:
        response = up(client, owner, b"cannot be stored")
    finally:
        storage.put_fails = False
    assert response.status_code == 503
    assert (
        scalar(database, "SELECT COUNT(*) FROM files WHERE owner_id = :o", {"o": owner_id})
        == files_before
    )
    assert (
        scalar(database, "SELECT COUNT(*) FROM access_logs WHERE user_id = :o", {"o": owner_id})
        == logs_before
    )
    assert objects(storage) == objects_before


def test_forced_database_failure_after_storing_removes_the_object(client, database, storage, owner):
    owner_id = uid(client, owner)
    files_before = scalar(
        database, "SELECT COUNT(*) FROM files WHERE owner_id = :o", {"o": owner_id}
    )
    objects_before = objects(storage)
    app = create_app(database=FailingDatabase(database), settings=load_settings(), storage=storage)
    storage.calls.clear()
    with TestClient(app, raise_server_exceptions=False) as failing_client:
        response = up(failing_client, owner, b"stored, then the database write fails")
    assert response.status_code == 500
    assert [op for op, _ in storage.calls] == ["put", "delete"]  # written, then removed again
    assert objects(storage) == objects_before
    assert (
        scalar(database, "SELECT COUNT(*) FROM files WHERE owner_id = :o", {"o": owner_id})
        == files_before
    )


def test_oversize_file_is_rejected_before_anything_is_stored(client, database, storage, owner):
    app = create_app(
        database=database, settings=replace(load_settings(), max_upload_bytes=1000), storage=storage
    )
    objects_before = objects(storage)
    with TestClient(app) as small:
        response = up(small, owner, b"a" * 1001)
        assert response.status_code == 413
        assert up(small, owner, b"a" * 1000).status_code == 201  # exactly the limit
    assert len(objects(storage)) == len(objects_before) + 1


# ---- the selection engine's choice, as recorded -----------------------------------------------
def test_high_security_upload_avoids_google_cloud_and_records_why(client, database, owner):
    body = up(client, owner, b"confidential", level="HIGH", preset="SECURE").json()
    assert body["provider_code"] != "GCP"
    reasons = scalar(
        database,
        "SELECT COUNT(*) FROM selection_candidates c JOIN selection_decisions d ON d.decision_id = c.decision_id "
        "JOIN provider_regions r ON r.region_id = c.region_id JOIN cloud_providers p ON p.provider_id = r.provider_id "
        "WHERE d.file_id = :f AND p.provider_code = 'GCP' AND c.excluded_reason LIKE '%HIGH minimum%'",
        {"f": body["file_id"]},
    )
    assert reasons == 2


# ---- delete ----------------------------------------------------------------------------------
def test_delete_needs_confirm_then_erases_key_object_and_location(client, database, storage, owner):
    body = up(client, owner, b"delete me").json()
    fid = {"f": body["file_id"]}
    assert client.delete(f"/files/{body['file_id']}", headers=owner).status_code == 400
    assert down(client, owner, body["file_id"]).content == b"delete me"

    response = client.delete(f"/files/{body['file_id']}", params={"confirm": "true"}, headers=owner)
    assert response.status_code == 200 and response.json()["object_removed"] is True
    assert scalar(database, "SELECT status FROM files WHERE file_id = :f", fid) == "DELETED"
    assert scalar(database, "SELECT deleted_at FROM files WHERE file_id = :f", fid) is not None
    assert scalar(database, "SELECT COUNT(*) FROM file_encryption WHERE file_id = :f", fid) == 0
    assert scalar(database, "SELECT COUNT(*) FROM storage_locations WHERE file_id = :f", fid) == 0
    assert (
        scalar(
            database,
            "SELECT COUNT(*) FROM audit_events WHERE event_type = 'FILE_DELETED' AND target_id = :f",
            fid,
        )
        == 1
    )
    assert (
        scalar(
            database,
            "SELECT COUNT(*) FROM access_logs WHERE file_id = :f AND action = 'DELETE' AND outcome = 'SUCCESS'",
            fid,
        )
        == 1
    )
    assert body["object_key"].split("/")[-1] not in [p.name for p in objects(storage)]
    assert down(client, owner, body["file_id"]).status_code == 410
    assert (
        client.delete(
            f"/files/{body['file_id']}", params={"confirm": "true"}, headers=owner
        ).status_code
        == 410
    )


def test_unicode_and_binary_content_survive_the_round_trip(client, owner):
    for data in (bytes(range(256)) * 40, "café 日本語".encode(), b"\x00" * 5000):
        body = up(client, owner, data, name="blob.bin").json()
        assert down(client, owner, body["file_id"]).content == data
