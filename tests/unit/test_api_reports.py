"""The report endpoints over HTTP, with canned rows from the fake database.

Proves: who may call them, whose files each role sees, that thresholds reach the query, that bad
thresholds are refused, and that the endpoints are read-only. The SQL itself is checked against
Oracle in tests/integration/test_oracle_reports.py.
"""

import copy
from datetime import datetime
from decimal import Decimal

from app.reports.models import ColdRow, DuplicateRow

UP = datetime(2026, 1, 1, 9, 0, 0)


def dup_row(owner, file_id, rank, sha="a" * 64, price="0.025"):
    return DuplicateRow(
        owner, sha, file_id, f"f{file_id}.bin", 2**30, UP, rank, "AWS", "us-east-1", "HOT",
        Decimal(price), True,
    )  # fmt: skip


def cold_row(owner, file_id, idle, downloads):
    return ColdRow(
        file_id, owner, f"c{file_id}.pdf", 1000, UP, None, idle, 0, downloads, "AWS", "ap-south-1",
        "COOL", "STANDARD_IA",
    )  # fmt: skip


def load(db, owner_a, owner_b):
    db.state["report_duplicate_rows"] = [
        dup_row(owner_a, 1, 1), dup_row(owner_a, 2, 2),
        dup_row(owner_b, 3, 1, sha="b" * 64), dup_row(owner_b, 4, 2, sha="b" * 64),
    ]  # fmt: skip
    db.state["report_cold_rows"] = [
        cold_row(owner_a, 10, idle=200, downloads=0),
        cold_row(owner_a, 11, idle=100, downloads=2),
        cold_row(owner_b, 12, idle=300, downloads=0),
    ]  # fmt: skip


def test_reports_need_a_token(client):
    assert client.get("/reports/duplicates").status_code == 401
    assert client.get("/reports/cold-files").status_code == 401


def test_a_user_sees_only_their_own_files(client, db, make_user, login):
    a, a_email = make_user()
    b, _ = make_user()
    load(db, a, b)
    headers = login(a_email)

    dups = client.get("/reports/duplicates", headers=headers).json()
    assert {g["owner_id"] for g in dups["groups"]} == {a}
    assert dups["scope"] == f"owner {a} only"
    cold = client.get("/reports/cold-files", headers=headers).json()
    assert {f["owner_id"] for f in cold["files"]} == {a}


def test_admin_and_auditor_see_every_owner(client, db, make_user, login):
    a, _ = make_user()
    b, _ = make_user()
    load(db, a, b)
    for role in ("ADMIN", "AUDITOR"):
        _, email = make_user(role=role)
        headers = login(email)
        dups = client.get("/reports/duplicates", headers=headers).json()
        assert {g["owner_id"] for g in dups["groups"]} == {a, b}, role
        assert dups["scope"] == "all owners"
        cold = client.get("/reports/cold-files", headers=headers).json()
        assert {f["owner_id"] for f in cold["files"]} == {a, b}, role


def test_duplicate_report_numbers_over_http(client, db, make_user, login):
    a, email = make_user()
    load(db, a, 999)
    body = client.get("/reports/duplicates", headers=login(email)).json()
    group = body["groups"][0]
    assert (group["copies"], group["extra_copies"]) == (2, 1)
    assert group["wasted_bytes"] == 2**30
    assert group["estimated_monthly_waste_usd"] == "0.025000"  # 1 GiB * $0.025
    assert body["prices_are_illustrative"] is True and body["read_only"] is True


def test_default_thresholds_apply_when_none_are_given(client, db, make_user, login):
    a, email = make_user()
    load(db, a, 999)
    body = client.get("/reports/cold-files", headers=login(email)).json()
    assert body["thresholds"] == {
        "idle_days_more_than": 90, "downloads_fewer_than": 3, "download_window_days": 365,
    }  # fmt: skip
    # idle 200/0 downloads and idle 100/2 downloads both pass; owner b's row is out of scope
    assert [f["file_id"] for f in body["files"]] == [10, 11]
    assert all(f["label"] == "candidate for archival review" for f in body["files"])


def test_changing_a_threshold_changes_the_result(client, db, make_user, login):
    a, email = make_user()
    load(db, a, 999)
    headers = login(email)

    def ids(query):
        return [f["file_id"] for f in client.get("/reports/cold-files" + query, headers=headers).json()["files"]]

    assert ids("") == [10, 11]
    assert ids("?idle_days=150") == [10]  # stricter idle limit drops the 100-day file
    assert ids("?idle_days=250") == []
    assert ids("?max_accesses=2") == [10]  # 2 downloads is not "fewer than 2"
    assert ids("?max_accesses=1") == [10]
    assert ids("?idle_days=50&max_accesses=10") == [10, 11]


def test_thresholds_come_from_system_config_then_the_request_overrides(client, db, make_user, login):
    a, email = make_user()
    load(db, a, 999)
    db.state["report_config"] = {"cold.idle_days": "150", "cold.max_accesses": "9", "cold.window_days": "60"}
    headers = login(email)

    body = client.get("/reports/cold-files", headers=headers).json()
    assert body["thresholds"] == {
        "idle_days_more_than": 150, "downloads_fewer_than": 9, "download_window_days": 60,
    }  # fmt: skip
    assert [f["file_id"] for f in body["files"]] == [10]
    body = client.get("/reports/cold-files?idle_days=50", headers=headers).json()
    assert body["thresholds"]["idle_days_more_than"] == 50  # request wins
    assert body["thresholds"]["downloads_fewer_than"] == 9  # config still used for the rest


def test_bad_thresholds_are_422(client, db, make_user, login):
    _, email = make_user()
    headers = login(email)
    for query in ("?idle_days=-1", "?max_accesses=0", "?window_days=0", "?idle_days=abc", "?idle_days=99999"):
        assert client.get("/reports/cold-files" + query, headers=headers).status_code == 422, query


def test_the_reports_are_read_only(client, db, make_user, login):
    a, email = make_user()
    load(db, a, 999)
    headers = login(email)
    before = copy.deepcopy({k: v for k, v in db.state.items() if k not in ("audit", "report_cold_calls")})

    for _ in range(3):
        assert client.get("/reports/duplicates", headers=headers).status_code == 200
        assert client.get("/reports/cold-files", headers=headers).status_code == 200
    after = {k: v for k, v in db.state.items() if k not in ("audit", "report_cold_calls")}
    assert after == before  # no file, location, log or config row changed

    for method in (client.post, client.put, client.patch, client.delete):
        for path in ("/reports/duplicates", "/reports/cold-files"):
            assert method(path, headers=headers).status_code == 405, (method, path)
