"""Duplicate and cold-file reports against REAL Oracle, using the seeded SYNTHETIC sample.

This is where the SQL is proved: the sample (app/reports/sample_data.py) holds known duplicates,
known cold files and look-alike traps, and the real queries must return exactly the answer key.
Each owner of the sample owns only synthetic files, so an owner-scoped report is exact even if the
database also holds other data.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.reports import sample_data as sample
from app.reports.service import GIB, ReportScope, ReportService, monthly_cost
from tests.integration.helpers import register_and_login, scalar


@pytest.fixture(scope="module")
def owners(database):
    with database.transaction() as session:
        ids = sample.seed(session.connection)
    yield ids
    with database.transaction() as session:
        sample.cleanup(session.connection)


@pytest.fixture
def service(database):
    return ReportService(database)


def scope(owners, email):
    return ReportScope(owner_id=owners[email])


def groups_by_files(report):
    return {tuple(f["file_name"] for f in g["files"]): g for g in report["groups"]}


def cold_names(report):
    return {f["file_name"] for f in report["files"]}


def price(database, provider, region, tier):
    return scalar(
        database,
        "SELECT pr.storage_usd_per_gb_month FROM provider_pricing pr "
        "JOIN provider_regions r ON r.region_id = pr.region_id "
        "JOIN cloud_providers p ON p.provider_id = r.provider_id "
        "WHERE p.provider_code = :p AND r.region_code = :r AND pr.tier_code = :t",
        {"p": provider, "r": region, "t": tier},
    )


# ---- duplicates ----------------------------------------------------------------------------
def test_every_seeded_duplicate_group_is_found_with_the_right_members(service, owners):
    a = groups_by_files(service.duplicates(scope(owners, sample.OWNER_A)))
    b = groups_by_files(service.duplicates(scope(owners, sample.OWNER_B)))
    expected = sample.EXPECTED_DUPLICATE_GROUPS
    for label in ("D1", "D2"):
        files = tuple(expected[label]["files"])
        assert files in a, label  # members listed oldest first, so the first is the original
        assert a[files]["wasted_bytes"] == expected[label]["wasted_bytes"], label
        assert a[files]["copies"] == len(files)
    d3 = tuple(expected["D3"]["files"])
    assert d3 in b and b[d3]["wasted_bytes"] == expected["D3"]["wasted_bytes"]


def test_no_false_positives_in_duplicates(service, owners):
    a = service.duplicates(scope(owners, sample.OWNER_A))
    b = service.duplicates(scope(owners, sample.OWNER_B))
    assert a["groups_found"] == 2 and b["groups_found"] == 1  # exactly D1+D2 and D3, nothing else
    reported = {f["file_name"] for r in (a, b) for g in r["groups"] for f in g["files"]}
    expected = {n for g in sample.EXPECTED_DUPLICATE_GROUPS.values() for n in g["files"]}
    assert reported == expected
    traps = {
        "SYN_RPT_near_a.bin", "SYN_RPT_near_b.bin",  # same size, different content
        "SYN_RPT_deldup_a.bin", "SYN_RPT_deldup_b.bin",  # second copy deleted
        "SYN_RPT_cross_a.bin", "SYN_RPT_cross_b.bin",  # same content, different owners
        "SYN_RPT_nohash_a.bin", "SYN_RPT_nohash_b.bin",  # no hash at all
    }  # fmt: skip
    assert not (reported & traps)


def test_all_owners_view_never_merges_the_same_content_of_two_owners(service, owners):
    """The all-owners report (what ADMIN/AUDITOR see) must still group per owner: the cross-owner
    pair has one identical hash but two owners, so it is NOT a duplicate group."""
    report = service.duplicates(ReportScope(owner_id=None))
    synthetic = [
        g for g in report["groups"] if any(f["file_name"].startswith("SYN_RPT_") for f in g["files"])
    ]
    reported = {f["file_name"] for g in synthetic for f in g["files"]}
    assert reported == {n for g in sample.EXPECTED_DUPLICATE_GROUPS.values() for n in g["files"]}
    assert not reported & {"SYN_RPT_cross_a.bin", "SYN_RPT_cross_b.bin"}
    owner_ids = set(owners.values())
    for group in synthetic:
        assert group["owner_id"] in owner_ids  # one owner per group
        assert len({f["file_name"][:12] for f in group["files"]}) == 1  # e.g. only dup1_*


def test_the_first_copy_is_the_oldest_one_and_is_marked_original(service, owners):
    group = service.duplicates(scope(owners, sample.OWNER_A))["groups"][0]
    assert group["files"][0]["file_name"] == "SYN_RPT_dup1_a.bin"  # uploaded 300 days ago
    assert [f["role"] for f in group["files"]] == ["original (kept)", "extra copy", "extra copy"]


def test_duplicate_money_matches_a_hand_calculation_from_the_price_table(service, owners, database):
    """D1: extras are dup1_b (Azure Central India, HOT) and dup1_c (GCP asia-south1, HOT), 1 GiB each,
    so waste = 1 * price(Azure) + 1 * price(GCP). D2: one extra 1 MiB in AWS us-east-1 HOT."""
    report = groups_by_files(service.duplicates(scope(owners, sample.OWNER_A)))
    d1 = report[tuple(sample.EXPECTED_DUPLICATE_GROUPS["D1"]["files"])]
    expected_d1 = price(database, "AZURE", "Central India", "HOT") + price(database, "GCP", "asia-south1", "HOT")
    assert Decimal(d1["estimated_monthly_waste_usd"]) == expected_d1.quantize(Decimal("0.000001"))
    assert d1["unpriced_extra_copies"] == 0

    d2 = report[tuple(sample.EXPECTED_DUPLICATE_GROUPS["D2"]["files"])]
    expected_d2 = monthly_cost(2**20, price(database, "AWS", "us-east-1", "HOT"))
    assert Decimal(d2["estimated_monthly_waste_usd"]) == expected_d2.quantize(Decimal("0.000001"))
    assert GIB == 2**30  # the unit used above


def test_an_extra_copy_with_no_location_is_counted_but_flagged_unpriced(service, owners):
    d3 = service.duplicates(scope(owners, sample.OWNER_B))["groups"][0]
    assert d3["wasted_bytes"] == sample.GIB // 2  # still waste
    assert d3["unpriced_extra_copies"] == 1
    assert d3["estimated_monthly_waste_usd"] == "0.000000"  # no price, so no invented money


# ---- cold files ----------------------------------------------------------------------------
def test_every_seeded_cold_file_is_found_and_no_other(service, owners):
    for email, expected in sample.EXPECTED_COLD.items():
        report = service.cold_files(scope(owners, email))
        assert cold_names(report) == expected, email  # exact: nothing missing, nothing extra


def test_cold_evidence_is_correct_for_known_files(service, owners):
    files = {f["file_name"]: f for f in service.cold_files(scope(owners, sample.OWNER_A))["files"]}

    cold1 = files["SYN_RPT_cold1.pdf"]  # one download 300 days ago
    assert (cold1["idle_days"], cold1["downloads_last_30_days"], cold1["downloads_in_window"]) == (300, 0, 1)
    assert (cold1["provider"], cold1["region"], cold1["storage_class"]) == ("AWS", "ap-south-1", "COOL")
    assert cold1["provider_storage_class_name"] == "STANDARD_IA"
    assert cold1["last_access"] is not None and cold1["label"] == "candidate for archival review"

    cold2 = files["SYN_RPT_cold2.pdf"]  # never downloaded: aged from upload (200 days)
    assert cold2["last_access"] is None and cold2["idle_days"] == 200
    assert "never downloaded" in cold2["evidence"]

    cold3 = files["SYN_RPT_cold3.pdf"]  # failed + denied downloads yesterday do not count
    assert cold3["idle_days"] == 220 and cold3["downloads_last_30_days"] == 0

    assert files["SYN_RPT_edge91.bin"]["idle_days"] == 91

    cold4 = {f["file_name"]: f for f in service.cold_files(scope(owners, sample.OWNER_B))["files"]}
    assert cold4["SYN_RPT_cold4.zip"]["downloads_in_window"] == 1  # the 400-day-old one is outside


def test_thresholds_change_the_result_as_expected(service, owners):
    a = scope(owners, sample.OWNER_A)
    default = sample.EXPECTED_COLD[sample.OWNER_A]

    looser_access = cold_names(service.cold_files(a, max_accesses=10))
    assert looser_access == default | sample.EXPECTED_COLD_MAX_ACCESSES_10_EXTRA  # warm.xlsx joins

    lower_idle = cold_names(service.cold_files(a, idle_days=60))
    assert lower_idle == default | sample.EXPECTED_COLD_IDLE_60_EXTRA  # edge90 joins

    higher_idle = cold_names(service.cold_files(a, idle_days=250))
    assert higher_idle == sample.EXPECTED_COLD_IDLE_250  # only the 300-day file is left

    # "fewer than 1 download" means none: only cold2 (never downloaded). cold1, cold3 and edge91
    # each have exactly one successful download, which is not fewer than 1.
    assert cold_names(service.cold_files(a, max_accesses=1)) == {"SYN_RPT_cold2.pdf"}


def test_the_idle_boundary_is_strict_more_than(service, owners):
    a = scope(owners, sample.OWNER_A)
    names = cold_names(service.cold_files(a, idle_days=90))
    assert "SYN_RPT_edge91.bin" in names and "SYN_RPT_edge90.bin" not in names  # 91 > 90, 90 !> 90
    assert "SYN_RPT_edge90.bin" in cold_names(service.cold_files(a, idle_days=89))


def test_a_shorter_window_forgets_older_downloads(service, owners):
    """Window 90 days: warm.xlsx's downloads (100 to 140 days ago) all fall outside the window,
    so it has too few accesses and becomes cold at the default idle limit."""
    names = cold_names(service.cold_files(scope(owners, sample.OWNER_A), window_days=90))
    assert "SYN_RPT_warm.xlsx" in names


def test_deleted_and_young_and_busy_files_are_never_cold(service, owners):
    names = cold_names(service.cold_files(scope(owners, sample.OWNER_A)))
    assert not names & {
        "SYN_RPT_delcold.pdf",  # deleted
        "SYN_RPT_new.txt",  # uploaded 10 days ago
        "SYN_RPT_hot1.docx",  # downloaded yesterday
        "SYN_RPT_warm.xlsx",  # idle but 5 downloads in the window
        "SYN_RPT_edge90.bin",  # exactly 90 days
    }  # fmt: skip


# ---- read-only and access control ------------------------------------------------------------
def test_running_the_reports_changes_no_data(service, owners, database):
    def snapshot():
        return tuple(
            scalar(database, f"SELECT COUNT(*) FROM {table}")  # noqa: S608  (fixed table names)
            for table in ("files", "access_logs", "storage_locations", "system_config", "users")
        ) + (
            scalar(database, "SELECT NVL(SUM(file_size_bytes), 0) FROM files"),
            scalar(database, "SELECT COUNT(*) FROM files WHERE status = 'DELETED'"),
        )

    before = snapshot()
    for _ in range(3):
        service.duplicates(ReportScope(owner_id=None))
        service.cold_files(ReportScope(owner_id=None))
    assert snapshot() == before


def test_http_user_sees_only_own_files_and_auditor_sees_the_sample(client, database, owners):
    _, user_headers = register_and_login(client)  # a brand-new user owns no files
    body = client.get("/reports/duplicates", headers=user_headers).json()
    assert body["groups_found"] == 0 and body["scope"].startswith("owner ")
    assert client.get("/reports/cold-files", headers=user_headers).json()["files_found"] == 0

    _, auditor = register_and_login(client, role="AUDITOR", database=database)
    dups = client.get("/reports/duplicates", headers=auditor).json()
    assert dups["scope"] == "all owners"
    names = {f["file_name"] for g in dups["groups"] for f in g["files"]}
    assert {n for g in sample.EXPECTED_DUPLICATE_GROUPS.values() for n in g["files"]} <= names
    cold = client.get("/reports/cold-files", headers=auditor).json()
    assert {n for s in sample.EXPECTED_COLD.values() for n in s} <= {f["file_name"] for f in cold["files"]}

    assert client.get("/reports/cold-files?idle_days=-5", headers=auditor).status_code == 422
