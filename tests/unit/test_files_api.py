"""Upload, download and delete through the real FastAPI app.

Database: FakeDatabase (logic only). Storage: the REAL simulated adapter writing to a temporary
folder, so "stored bytes", "flip a byte" and "object removed" are checked on actual files.
Nothing here proves Oracle behaviour; tests/integration/test_oracle_files.py does that.
"""

import hashlib
import logging
import os
from dataclasses import replace
from decimal import Decimal as D

import pytest

from app.db.errors import DatabaseError
from app.selection import engine
from app.selection.models import FileRequest
from app.selection.presets import PRESETS
from tests.fakes import _FakeProviders

OCTET = {"Content-Type": "application/octet-stream"}
MARKER = b"TOP-SECRET-MARKER-"


def upload(client, headers, data=b"hello world", name="report.pdf", level="MEDIUM", **params):
    query = {"file_name": name, "security_level": level, **params}
    return client.post("/files", params=query, content=data, headers={**headers, **OCTET})


def download(client, headers, file_id):
    return client.get(f"/files/{file_id}/download", headers=headers)


def stored_objects(storage):
    """Every object file currently on the simulated disk."""
    return [p for p in storage.real.root.rglob("*") if p.is_file()]


def object_path(storage, uploaded):
    matches = [
        p for p in stored_objects(storage) if p.name == uploaded["object_key"].split("/")[-1]
    ]
    assert len(matches) == 1, matches
    return matches[0]


def user_id(client, headers):
    return client.get("/auth/me", headers=headers).json()["user_id"]


# ---- round trip and encryption at rest -------------------------------------------------------
@pytest.mark.parametrize("size", [1, 15, 16, 17, 1000, 100_000, 1024 * 1024])
def test_round_trip_returns_identical_bytes(client, as_role, size):
    headers, data = as_role("USER"), os.urandom(size)
    response = upload(client, headers, data)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["file_size_bytes"] == size
    assert body["content_sha256"] == hashlib.sha256(data).hexdigest()
    got = download(client, headers, body["file_id"])
    assert got.status_code == 200 and got.content == data


def test_download_keeps_the_original_file_name_even_with_spaces_and_accents(client, as_role):
    headers = as_role("USER")
    body = upload(client, headers, b"abc", name="résumé final.pdf").json()
    got = download(client, headers, body["file_id"])
    assert "filename*=UTF-8''r%C3%A9sum%C3%A9%20final.pdf" in got.headers["content-disposition"]


def test_stored_bytes_do_not_contain_the_plaintext(client, as_role, storage, db):
    headers, plaintext = as_role("USER"), MARKER * 200
    body = upload(client, headers, plaintext, name="notes.txt").json()
    on_disk = object_path(storage, body).read_bytes()
    assert MARKER not in on_disk and plaintext[:32] not in on_disk
    assert len(on_disk) == len(plaintext) + 16  # AES-GCM adds a 16-byte tag, nothing else
    assert hashlib.sha256(on_disk).hexdigest() == body["ciphertext_sha256"]
    # and nothing in the database holds the plaintext or the master key either
    dump = repr(db.state).encode()
    assert MARKER not in dump and bytes(range(32)) not in dump


def test_database_holds_only_the_wrapped_key_and_nonce(client, as_role, db):
    body = upload(client, as_role("USER"), b"x" * 64).json()
    row = db.state["encryption"][body["file_id"]]
    assert set(row) == {
        "algorithm",
        "wrapped_dek",
        "nonce",
        "master_key_version",
        "ciphertext_sha256",
    }
    assert row["algorithm"] == "AES-256-GCM" and len(row["nonce"]) == 12
    assert len(row["wrapped_dek"]) == 60 and row["master_key_version"] == 1
    assert "wrapped_dek" not in body and "nonce" not in body  # not echoed to the client


def test_every_file_gets_its_own_key_and_nonce(client, as_role, db):
    headers = as_role("USER")
    a = upload(client, headers, b"same content").json()["file_id"]
    b = upload(client, headers, b"same content").json()["file_id"]
    assert db.state["encryption"][a]["wrapped_dek"] != db.state["encryption"][b]["wrapped_dek"]
    assert db.state["encryption"][a]["nonce"] != db.state["encryption"][b]["nonce"]


def test_upload_response_labels_storage_as_simulated_and_hides_secrets(client, as_role):
    body = upload(client, as_role("USER"), b"data").json()
    assert body["storage"].startswith("SIMULATED local folder: ")
    assert body["encryption"] == {"algorithm": "AES-256-GCM", "master_key_version": 1}
    assert "ILLUSTRATIVE" in " ".join(body["selection"]["warnings"])


# ---- tamper detection ------------------------------------------------------------------------
@pytest.mark.parametrize("where", ["first", "middle", "last"])
def test_flipping_one_stored_byte_makes_download_fail(client, as_role, storage, db, where):
    headers = as_role("USER")
    data = os.urandom(300)
    body = upload(client, headers, data).json()
    path = object_path(storage, body)
    original = path.read_bytes()
    index = {"first": 0, "middle": len(original) // 2, "last": len(original) - 1}[where]
    tampered = bytearray(original)
    tampered[index] ^= 0x01
    path.write_bytes(bytes(tampered))

    response = download(client, headers, body["file_id"])
    assert response.status_code == 502
    assert "integrity check failed" in response.json()["detail"]
    assert data not in response.content
    failure = [e for e in db.state["access_logs"] if e["action"] == "DOWNLOAD"]
    assert [e["outcome"] for e in failure] == ["FAILURE"]

    path.write_bytes(original)  # restoring the byte proves the flip was the cause
    assert download(client, headers, body["file_id"]).content == data


def test_truncated_or_extended_objects_are_detected(client, as_role, storage):
    headers = as_role("USER")
    body = upload(client, headers, os.urandom(100)).json()
    path = object_path(storage, body)
    original = path.read_bytes()
    for broken in (original[:-1], original + b"\x00", b""):
        path.write_bytes(broken)
        assert download(client, headers, body["file_id"]).status_code == 502


def test_a_wrong_recorded_plaintext_hash_is_detected_after_decryption(client, as_role, db):
    headers = as_role("USER")
    body = upload(client, headers, b"important").json()
    db.state["files"][body["file_id"]]["content_sha256"] = "0" * 64
    response = download(client, headers, body["file_id"])
    assert response.status_code == 502
    assert "does not match its recorded SHA-256" in response.json()["detail"]


def test_a_missing_stored_object_is_a_clear_404(client, as_role, storage, db):
    headers = as_role("USER")
    body = upload(client, headers, b"here today").json()
    object_path(storage, body).unlink()
    response = download(client, headers, body["file_id"])
    assert response.status_code == 404
    assert response.json() == {"detail": "the stored object is missing from the storage provider"}
    assert [e["outcome"] for e in db.state["access_logs"] if e["action"] == "DOWNLOAD"] == [
        "FAILURE"
    ]


def test_unknown_file_is_404_and_a_provider_failure_is_503(client, as_role, storage):
    headers = as_role("USER")
    assert download(client, headers, 999).status_code == 404
    body = upload(client, headers, b"data").json()
    storage.get_fails = True
    assert download(client, headers, body["file_id"]).status_code == 503


# ---- size and name validation ----------------------------------------------------------------
def test_oversize_file_is_rejected_and_nothing_is_stored(client, as_role, storage, db):
    headers = as_role("USER")  # test limit is 1 MiB
    response = upload(client, headers, b"a" * (1024 * 1024 + 1))
    assert response.status_code == 413 and "limit" in response.json()["detail"]
    assert db.state["files"] == {} and db.state["decisions"] == {} and stored_objects(storage) == []
    assert storage.calls == []  # the provider was never contacted


def test_oversize_is_also_caught_when_the_client_streams_without_a_length(client, as_role, storage):
    headers = as_role("USER")
    chunks = iter([b"a" * 700_000, b"a" * 700_000])  # no Content-Length header: chunked upload
    response = client.post(
        "/files", params={"file_name": "big.bin", "security_level": "LOW"}, content=chunks,
        headers={**headers, **OCTET},
    )  # fmt: skip
    assert response.status_code == 413 and stored_objects(storage) == []


def test_the_limit_itself_is_allowed(client, as_role):
    assert upload(client, as_role("USER"), b"a" * (1024 * 1024)).status_code == 201


def test_empty_file_is_rejected(client, as_role):
    response = upload(client, as_role("USER"), b"")
    assert response.status_code == 422 and response.json()["detail"] == "file is empty"


@pytest.mark.parametrize(
    "name", ["a/b.txt", "..\\x.txt", "..", ".", "bad\x00name", "tab\tname", "   "]
)
def test_unsafe_file_names_are_rejected(client, as_role, storage, name):
    response = upload(client, as_role("USER"), b"data", name=name)
    assert response.status_code == 422 and stored_objects(storage) == []


def test_overlong_name_and_missing_parameters_are_422(client, as_role):
    headers = as_role("USER")
    assert upload(client, headers, b"x", name="n" * 256).status_code == 422
    assert client.post("/files", content=b"x", headers={**headers, **OCTET}).status_code == 422
    assert upload(client, headers, b"x", level="TOP").status_code == 422
    assert upload(client, headers, b"x", tier="FREEZER").status_code == 422


def test_file_type_is_derived_from_the_extension_with_other_as_fallback(client, as_role, db):
    headers = as_role("USER")
    ids = {n: upload(client, headers, b"x", name=n).json()["file_id"]
           for n in ("a.PDF", "b.jpeg", "c.unknownext", "noextension")}  # fmt: skip
    assert {n: db.state["files"][i]["file_type"] for n, i in ids.items()} == {
        "a.PDF": "PDF", "b.jpeg": "JPG", "c.unknownext": "OTHER", "noextension": "OTHER",
    }  # fmt: skip


# ---- authorisation ---------------------------------------------------------------------------
def test_another_user_cannot_download_or_delete_and_the_denial_is_recorded(client, as_role, db):
    owner, other = as_role("USER"), as_role("USER")
    body = upload(client, owner, b"private").json()
    other_id = user_id(client, other)

    assert download(client, other, body["file_id"]).status_code == 403
    assert (
        client.delete(
            f"/files/{body['file_id']}", params={"confirm": "true"}, headers=other
        ).status_code
        == 403
    )

    denied = [e for e in db.audit_of("ACCESS_DENIED") if e["details"].get("reason") == "not_owner"]
    assert [e["actor_user_id"] for e in denied] == [other_id, other_id]
    assert {e["details"]["action"] for e in denied} == {"DOWNLOAD", "DELETE"}
    assert [e["outcome"] for e in db.state["access_logs"] if e["user_id"] == other_id] == [
        "DENIED",
        "DENIED",
    ]
    assert download(client, owner, body["file_id"]).content == b"private"  # untouched
    assert db.state["files"][body["file_id"]]["status"] == "ACTIVE"


@pytest.mark.parametrize("role", ["ADMIN", "AUDITOR"])
def test_admin_and_auditor_cannot_upload_download_or_delete_content(client, as_role, role):
    owner, other = as_role("USER"), as_role(role)
    body = upload(client, owner, b"private").json()
    assert upload(client, other, b"x").status_code == 403
    assert download(client, other, body["file_id"]).status_code == 403
    assert (
        client.delete(
            f"/files/{body['file_id']}", params={"confirm": "true"}, headers=other
        ).status_code
        == 403
    )


def test_every_file_endpoint_needs_a_token(client, seeded):
    assert (
        client.post(
            "/files", params={"file_name": "a", "security_level": "LOW"}, content=b"x"
        ).status_code
        == 401
    )
    assert client.get("/files/1/download").status_code == 401
    assert client.delete("/files/1", params={"confirm": "true"}).status_code == 401


# ---- forced failures: no orphans either way --------------------------------------------------
EMPTY_TABLES = ["files", "encryption", "locations", "decisions", "candidates", "access_logs"]


def assert_no_metadata(db):
    assert {t: db.state[t] for t in EMPTY_TABLES} == {t: type(db.state[t])() for t in EMPTY_TABLES}
    assert db.audit_of("FILE_UPLOADED") == []


def test_forced_storage_failure_leaves_no_metadata(client, as_role, storage, db):
    storage.put_fails = True
    response = upload(client, as_role("USER"), b"will not be stored")
    assert response.status_code == 503
    assert response.json() == {"detail": "the storage provider could not store the file"}
    assert_no_metadata(db)
    assert stored_objects(storage) == []


def test_forced_database_failure_leaves_no_stored_object(client, as_role, storage, db):
    db.fail_insert_file_with = DatabaseError("injected: database write failed")
    response = upload(client, as_role("USER"), b"stored then rolled back")
    assert response.status_code == 500 and response.json() == {"detail": "Internal database error"}
    ops = [op for op, _ in storage.calls]
    assert ops == ["put", "delete"]  # the object WAS written, then removed again
    assert stored_objects(storage) == []
    assert_no_metadata(db)


def test_database_failure_late_in_the_transaction_rolls_back_everything_and_removes_the_object(
    client, as_role, storage, db
):
    """The audit insert is the LAST write: the file, key, location and decision rows had already
    been added inside the transaction and must vanish with it."""
    headers = as_role("USER")
    db.fail_audit_with = DatabaseError("injected: audit write failed")
    response = upload(client, headers, b"data")
    assert response.status_code == 500
    assert stored_objects(storage) == []
    assert db.state["files"] == {} and db.state["encryption"] == {} and db.state["locations"] == {}
    assert db.state["decisions"] == {} and db.state["candidates"] == []


def test_if_the_cleanup_delete_also_fails_the_orphan_is_logged_and_the_original_error_returned(
    client, as_role, storage, db, caplog
):
    db.fail_insert_file_with = DatabaseError("injected")
    storage.delete_fails = True
    with caplog.at_level(logging.ERROR):
        response = upload(client, as_role("USER"), b"orphan me")
    assert response.status_code == 500  # the caller sees the database error, not the cleanup error
    assert len(stored_objects(storage)) == 1  # the object really is orphaned
    assert "ORPHANED OBJECT" in caplog.text


# ---- selection is called and its decision is recorded ----------------------------------------
def expected_region(db, size, level, preset="BALANCED", **kw):
    request = FileRequest(size, level, D(kw.get("accesses", 1)), D(kw.get("months", 12)), "HOT")
    candidates = _FakeProviders(db.state).candidates("HOT")
    return engine.select(
        request, candidates, PRESETS[preset], db.state["policy"], preset_name=preset
    )


@pytest.mark.parametrize(
    ("level", "preset"), [("LOW", "BALANCED"), ("HIGH", "SECURE"), ("MEDIUM", "COST_SAVER")]
)
def test_upload_goes_where_the_selection_engine_says(client, as_role, db, level, preset):
    data = os.urandom(5000)
    body = upload(client, as_role("USER"), data, level=level, preset=preset).json()
    direct = expected_region(db, len(data), level, preset)
    chosen = direct.selected.candidate
    assert (body["provider_code"], body["region"]) == (chosen.provider_code, chosen.region_code)
    assert body["selection"]["composite"] == format(direct.selected.composite, "f")
    location = next(iter(db.state["locations"].values()))
    assert location["region_id"] == chosen.region_id and location["tier_code"] == "HOT"


def test_the_decision_and_every_candidate_are_stored_including_rejections(client, as_role, db):
    body = upload(client, as_role("USER"), b"d" * 100, level="HIGH").json()
    [decision] = db.state["decisions"].values()
    assert decision["file_id"] == body["file_id"] and decision["profile_name"] == "BALANCED"
    assert sum(D(v) for v in decision["weights"].values()) == D(1)
    assert decision["explanation"] == db.state["decisions"][decision["decision_id"]]["explanation"]
    candidates = db.state["candidates"]
    assert len(candidates) == 6  # all six seeded provider-regions are recorded
    rejected = [c for c in candidates if c["excluded_reason"]]
    assert {c["region_id"] for c in rejected} == {
        5,
        6,
    }  # Google Cloud: security 9.1 < HIGH minimum 9.2
    assert all(c["total_score"] is None and c["raw"] is None for c in rejected)
    ranked = [c for c in candidates if not c["excluded_reason"]]
    assert all(D(0) <= c["total_score"] <= D(1) for c in ranked) and len(ranked) == 4
    assert all(
        set(c["raw"])
        == {"storage_cost", "transfer_cost", "latency", "reliability", "security", "total_cost"}
        for c in ranked
    )


def test_when_no_provider_qualifies_nothing_is_stored_and_the_reasons_are_returned(
    client, as_role, storage, db
):
    for rid, region in list(db.state["regions"].items()):
        db.state["regions"][rid] = replace(region, is_active=False)
    response = upload(client, as_role("USER"), b"nowhere to go")
    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == "no provider-region can store this file" and len(body["rejected"]) == 6
    assert all("region is disabled" in r["reasons"] for r in body["rejected"])
    assert storage.calls == [] and db.state["files"] == {} and db.state["decisions"] == {}


def test_unknown_preset_is_rejected_before_anything_is_stored(client, as_role, storage):
    response = upload(client, as_role("USER"), b"x", preset="NOPE")
    assert response.status_code == 422 and storage.calls == []


# ---- access log and audit --------------------------------------------------------------------
def test_upload_and_download_write_access_log_rows_with_measured_latency(client, as_role, db):
    headers = as_role("USER")
    uid = user_id(client, headers)
    body = upload(client, headers, os.urandom(2000)).json()
    download(client, headers, body["file_id"])
    logs = [e for e in db.state["access_logs"] if e["file_id"] == body["file_id"]]
    assert [(e["action"], e["outcome"]) for e in logs] == [
        ("UPLOAD", "SUCCESS"),
        ("DOWNLOAD", "SUCCESS"),
    ]
    for entry in logs:
        assert entry["user_id"] == uid and entry["region_id"] is not None
        assert isinstance(entry["latency_ms"], D) and entry["latency_ms"] >= 0
    [event] = db.audit_of("FILE_UPLOADED")
    assert event["actor_user_id"] == uid and event["target_id"] == body["file_id"]
    assert event["details"]["provider"] == body["provider_code"]


# ---- delete ----------------------------------------------------------------------------------
def test_delete_requires_the_confirm_flag(client, as_role, storage, db):
    headers = as_role("USER")
    body = upload(client, headers, b"keep me").json()
    for params in ({}, {"confirm": "false"}):
        response = client.delete(f"/files/{body['file_id']}", params=params, headers=headers)
        assert response.status_code == 400 and "confirm=true" in response.json()["detail"]
    assert download(client, headers, body["file_id"]).content == b"keep me"
    assert db.audit_of("FILE_DELETED") == [] and len(stored_objects(storage)) == 1


def test_confirmed_delete_erases_the_key_removes_the_object_and_audits(
    client, as_role, storage, db
):
    headers = as_role("USER")
    uid = user_id(client, headers)
    body = upload(client, headers, b"delete me").json()
    response = client.delete(
        f"/files/{body['file_id']}", params={"confirm": "true"}, headers=headers
    )
    assert response.status_code == 200
    assert response.json() == {
        "file_id": body["file_id"], "status": "DELETED", "crypto_erased": True, "object_removed": True,
    }  # fmt: skip
    assert db.state["files"][body["file_id"]]["status"] == "DELETED"
    assert body["file_id"] not in db.state["encryption"]  # crypto-erase: the wrapped key is gone
    assert db.state["locations"] == {} and stored_objects(storage) == []
    [event] = db.audit_of("FILE_DELETED")
    assert event["actor_user_id"] == uid and event["target_id"] == body["file_id"]
    assert ("DELETE", "SUCCESS") in [(e["action"], e["outcome"]) for e in db.state["access_logs"]]


def test_a_deleted_file_cannot_be_downloaded_or_deleted_again(client, as_role):
    headers = as_role("USER")
    body = upload(client, headers, b"once").json()
    client.delete(f"/files/{body['file_id']}", params={"confirm": "true"}, headers=headers)
    assert download(client, headers, body["file_id"]).status_code == 410
    again = client.delete(f"/files/{body['file_id']}", params={"confirm": "true"}, headers=headers)
    assert again.status_code == 410


def test_if_the_object_cannot_be_removed_the_key_is_still_erased_and_the_location_kept(
    client, as_role, storage, db
):
    headers = as_role("USER")
    body = upload(client, headers, b"stubborn").json()
    storage.delete_fails = True
    response = client.delete(
        f"/files/{body['file_id']}", params={"confirm": "true"}, headers=headers
    )
    assert response.status_code == 200 and response.json()["object_removed"] is False
    assert body["file_id"] not in db.state["encryption"]  # unreadable even though bytes remain
    [location] = db.state["locations"].values()
    assert location["replica_status"] == "PENDING_DELETE"
    assert len(stored_objects(storage)) == 1


# ---- each defensive layer is pinned separately ----------------------------------------------
def test_flipped_byte_is_caught_by_the_stored_hash_check_first(client, as_role, storage):
    headers = as_role("USER")
    body = upload(client, headers, os.urandom(200)).json()
    path = object_path(storage, body)
    raw = bytearray(path.read_bytes())
    raw[10] ^= 0x01
    path.write_bytes(bytes(raw))
    detail = download(client, headers, body["file_id"]).json()["detail"]
    assert "ciphertext hash mismatch" in detail


def test_an_attacker_who_also_rewrites_the_stored_hash_is_still_caught_by_gcm(
    client, as_role, storage, db
):
    """Second, independent layer: even if the ciphertext hash in the database is updated to match
    the tampered object, AES-GCM authentication fails and the file is not returned."""
    headers = as_role("USER")
    body = upload(client, headers, os.urandom(200)).json()
    path = object_path(storage, body)
    raw = bytearray(path.read_bytes())
    raw[10] ^= 0x01
    path.write_bytes(bytes(raw))
    db.state["encryption"][body["file_id"]]["ciphertext_sha256"] = hashlib.sha256(raw).hexdigest()
    response = download(client, headers, body["file_id"])
    assert response.status_code == 502
    assert "could not be decrypted" in response.json()["detail"]


def test_the_service_itself_enforces_the_size_limit_without_the_router(
    db, settings, seeded, storage
):
    """FileService is also used outside HTTP, so it must not rely on the router's check."""
    from app.crypto.service import CryptoService
    from app.db.base import UserRecord
    from app.files.errors import FileTooLargeError
    from app.files.service import FileService, UploadParams

    service = FileService(db, settings, CryptoService(settings.master_key), storage)
    user = UserRecord(1, "T", "t@example.com", "USER", None, "ACTIVE")
    with pytest.raises(FileTooLargeError):
        service.upload(user, UploadParams("big.bin", "LOW"), b"a" * (settings.max_upload_bytes + 1))
    assert storage.calls == [] and db.state["files"] == {}


def test_file_name_length_is_checked_in_bytes_because_oracle_counts_bytes(client, as_role, storage):
    headers = as_role("USER")
    too_long = upload(client, headers, b"x", name="é" * 127 + ".txt")  # 254 + 4 = 258 bytes
    assert too_long.status_code == 422 and "bytes" in too_long.json()["detail"]
    assert stored_objects(storage) == []  # refused before anything was stored
    fits = upload(client, headers, b"x", name="é" * 125 + ".txt")  # 250 + 4 = 254 bytes
    assert fits.status_code == 201
    assert len(stored_objects(storage)) == 1
