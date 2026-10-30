"""The report arithmetic, thresholds and wording. No database, no HTTP.

The money checks are worked out BY HAND in the comments, so a failing test points at the code,
not at the expected number. Whether the SQL finds the right files is proved against Oracle in
tests/integration/test_oracle_reports.py.
"""

from datetime import datetime
from decimal import Decimal

import pytest

from app.reports.models import ColdRow, ColdThresholds, DuplicateRow
from app.reports.service import (
    COLD_LABEL,
    DEFAULT_THRESHOLDS,
    GIB,
    InvalidReportRequest,
    build_cold_report,
    build_duplicate_report,
    monthly_cost,
    resolve_thresholds,
)

AS_OF = datetime(2026, 6, 1, 12, 0, 0)
UPLOADED = datetime(2026, 1, 1, 9, 30, 0)
HASH = "a" * 64
MIB = 2**20


def dup(file_id, rank, size=2**30, price="0.025", owner=1, sha=HASH, region="us-east-1"):
    return DuplicateRow(
        owner_id=owner, content_sha256=sha, file_id=file_id, file_name=f"f{file_id}.bin",
        file_size_bytes=size, upload_time=UPLOADED, copy_rank=rank,
        provider_code=None if price is None else "AWS",
        region_code=None if price is None else region,
        tier_code=None if price is None else "HOT",
        storage_usd_per_gb_month=None if price is None else Decimal(price),
        is_illustrative=None if price is None else True,
    )  # fmt: skip


# ---- the arithmetic, by hand ---------------------------------------------------------------
def test_one_copy_per_month_is_gib_times_price():
    # 2 GiB * $0.025 per GiB-month = $0.05
    assert monthly_cost(2 * 2**30, Decimal("0.025")) == Decimal("0.05")
    # 512 MiB = 0.5 GiB; 0.5 * $0.024 = $0.012
    assert monthly_cost(2**29, Decimal("0.024")) == Decimal("0.012")


def test_gib_is_two_to_the_thirtieth_not_a_billion():
    assert GIB == 1073741824
    assert monthly_cost(10**9, Decimal("1")) != Decimal("1")  # 1e9 bytes is NOT one GiB


def test_three_copies_waste_is_the_two_extras_only():
    """Three 1 GiB copies. Original (rank 1) is NOT waste.
    Extras: rank 2 at $0.024 -> 0.024; rank 3 at $0.0125 -> 0.0125. Total = 0.0365.
    Wasted bytes = 2 GiB = 2147483648."""
    report = build_duplicate_report(
        [dup(1, 1, price="0.025"), dup(2, 2, price="0.024"), dup(3, 3, price="0.0125")],
        AS_OF, "test",
    )  # fmt: skip
    group = report["groups"][0]
    assert group["copies"] == 3 and group["extra_copies"] == 2
    assert group["wasted_bytes"] == 2 * 2**30 == 2147483648
    assert group["estimated_monthly_waste_usd"] == "0.036500"
    assert group["unpriced_extra_copies"] == 0


def test_original_price_does_not_change_the_waste():
    cheap_original = build_duplicate_report([dup(1, 1, price="0.001"), dup(2, 2, price="0.02")], AS_OF, "t")
    dear_original = build_duplicate_report([dup(1, 1, price="9.9"), dup(2, 2, price="0.02")], AS_OF, "t")
    assert cheap_original["groups"][0]["estimated_monthly_waste_usd"] == "0.020000"
    assert dear_original["groups"][0]["estimated_monthly_waste_usd"] == "0.020000"


def test_one_mebibyte_extra_rounds_to_six_places():
    """1 MiB = 1/1024 GiB. 1/1024 * 0.025 = 0.0000244140625 -> 0.000024 (six places)."""
    report = build_duplicate_report(
        [dup(1, 1, size=MIB), dup(2, 2, size=MIB)], AS_OF, "t"
    )  # fmt: skip
    assert report["groups"][0]["estimated_monthly_waste_usd"] == "0.000024"
    assert report["groups"][0]["wasted_bytes"] == MIB


def test_rounding_is_half_up_not_banker_s():
    """1 GiB at $0.0000005 is exactly 0.0000005 -> half-up gives 0.000001 (banker's would give 0)."""
    report = build_duplicate_report([dup(1, 1), dup(2, 2, price="0.0000005")], AS_OF, "t")
    assert report["groups"][0]["estimated_monthly_waste_usd"] == "0.000001"


def test_an_unpriced_extra_is_counted_but_adds_no_money_and_is_flagged():
    """Extras: rank 2 priced $0.024 for 1 GiB -> 0.024. Rank 3 has no price: counted as waste
    bytes (still 2 GiB wasted) and flagged, but adds $0, so the money is a floor, not a guess."""
    report = build_duplicate_report(
        [dup(1, 1), dup(2, 2, price="0.024"), dup(3, 3, price=None)], AS_OF, "t"
    )  # fmt: skip
    group = report["groups"][0]
    assert group["wasted_bytes"] == 2 * 2**30
    assert group["estimated_monthly_waste_usd"] == "0.024000"
    assert group["unpriced_extra_copies"] == 1


def test_totals_add_up_across_groups_and_groups_sort_by_wasted_bytes():
    rows = [
        dup(1, 1, size=MIB, sha="b" * 64), dup(2, 2, size=MIB, sha="b" * 64),  # waste 1 MiB
        dup(3, 1, sha="c" * 64), dup(4, 2, sha="c" * 64, price="0.02"),  # waste 1 GiB, $0.02
    ]  # fmt: skip
    report = build_duplicate_report(rows, AS_OF, "t")
    assert [g["content_sha256"][0] for g in report["groups"]] == ["c", "b"]  # biggest waste first
    assert report["groups_found"] == 2
    assert report["total_extra_copies"] == 2
    assert report["total_wasted_bytes"] == 2**30 + MIB
    # 0.02 (1 GiB at 0.02) + 0.000024 (1 MiB at 0.025, rounded) = 0.020024
    assert report["total_estimated_monthly_waste_usd"] == "0.020024"


def test_same_hash_for_two_owners_makes_two_separate_groups():
    rows = [dup(1, 1, owner=1), dup(2, 2, owner=1), dup(3, 1, owner=2), dup(4, 2, owner=2)]
    report = build_duplicate_report(rows, AS_OF, "t")
    assert report["groups_found"] == 2
    assert {g["owner_id"] for g in report["groups"]} == {1, 2}


def test_no_rows_means_an_empty_report_not_an_error():
    report = build_duplicate_report([], AS_OF, "t")
    assert report["groups_found"] == 0 and report["groups"] == []
    assert report["total_estimated_monthly_waste_usd"] == "0.000000"


def test_duplicate_report_states_its_limits_and_that_it_is_read_only():
    report = build_duplicate_report([], AS_OF, "t")
    text = " ".join(report["notes"]).lower()
    assert report["read_only"] is True and report["prices_are_illustrative"] is True
    assert "illustrative" in text
    assert "near-duplicates" in text and "not detected" in text
    assert "nothing was merged, moved or deleted" in text


def test_copy_roles_label_the_original_and_the_extras():
    report = build_duplicate_report([dup(2, 2), dup(1, 1)], AS_OF, "t")  # given out of order
    assert [f["role"] for f in report["groups"][0]["files"]] == ["original (kept)", "extra copy"]


# ---- thresholds ----------------------------------------------------------------------------
def test_request_beats_config_beats_default():
    config = {"cold.idle_days": "120", "cold.max_accesses": "7", "cold.window_days": "200"}
    assert resolve_thresholds({}, None, None, None) == DEFAULT_THRESHOLDS == ColdThresholds(90, 3, 365)
    assert resolve_thresholds(config, None, None, None) == ColdThresholds(120, 7, 200)
    assert resolve_thresholds(config, 10, None, 30) == ColdThresholds(10, 7, 30)


def test_a_broken_config_value_falls_back_to_the_default():
    assert resolve_thresholds({"cold.idle_days": "ninety"}, None, None, None).idle_days == 90


@pytest.mark.parametrize(
    ("idle", "accesses", "window"),
    [(-1, 3, 365), (99999, 3, 365), (90, 0, 365), (90, 3, 0), (90, 3, 99999)],
)
def test_out_of_range_thresholds_are_refused(idle, accesses, window):
    with pytest.raises(InvalidReportRequest):
        resolve_thresholds({}, idle, accesses, window)


# ---- cold report wording -------------------------------------------------------------------
def cold(**over):
    base = dict(
        file_id=7, owner_id=1, file_name="old.pdf", file_size_bytes=2**30, upload_time=UPLOADED,
        last_download_at=datetime(2025, 12, 30, 8, 0, 0), idle_days=153, downloads_30d=0,
        downloads_window=1, provider_code="AWS", region_code="ap-south-1", tier_code="COOL",
        native_class_name="STANDARD_IA",
    )  # fmt: skip
    base.update(over)
    return ColdRow(**base)


def test_every_cold_file_carries_the_label_and_its_evidence():
    report = build_cold_report([cold()], DEFAULT_THRESHOLDS, AS_OF, "t")
    item = report["files"][0]
    assert item["label"] == COLD_LABEL == "candidate for archival review"
    assert item["last_access"] == "2025-12-30T08:00:00"
    assert (item["idle_days"], item["downloads_last_30_days"], item["downloads_in_window"]) == (153, 0, 1)
    assert (item["provider"], item["region"], item["storage_class"]) == ("AWS", "ap-south-1", "COOL")
    assert item["provider_storage_class_name"] == "STANDARD_IA"
    evidence = item["evidence"]
    assert "idle 153 days (threshold: more than 90)" in evidence
    assert "successful downloads in the last 365 days: 1 (threshold: fewer than 3)" in evidence
    assert "in the last 30 days: 0" in evidence and "2025-12-30" in evidence


def test_a_never_downloaded_file_says_it_is_aged_from_its_upload():
    report = build_cold_report(
        [cold(last_download_at=None, idle_days=151, downloads_window=0)], DEFAULT_THRESHOLDS, AS_OF, "t"
    )  # fmt: skip
    item = report["files"][0]
    assert item["last_access"] is None
    assert "never downloaded" in item["evidence"] and "2026-01-01" in item["evidence"]


def test_cold_report_echoes_thresholds_and_is_read_only():
    thresholds = ColdThresholds(30, 5, 100)
    report = build_cold_report([], thresholds, AS_OF, "t")
    assert report["thresholds"] == {
        "idle_days_more_than": 30, "downloads_fewer_than": 5, "download_window_days": 100,
    }  # fmt: skip
    assert report["read_only"] is True and report["files_found"] == 0
    text = " ".join(report["notes"]).lower()
    assert "nothing was migrated, archived or deleted" in text
    assert "successful download" in text
