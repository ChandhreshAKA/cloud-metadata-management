"""Duplicate-file and cold-file reports. READ-ONLY: this module never writes, migrates or deletes.

The grouping and the threshold filtering are done in SQL (app/db/repositories/reports.py). This
module adds the money arithmetic, the labels and the explanations around the rows SQL returns.

Money: prices come from provider_pricing and are ILLUSTRATIVE, not real quotes. "GB" means GiB
(2**30 bytes) everywhere in this project, as in the selection engine.

Known limits (also returned in every report so they cannot be missed):
  * exact duplicates only: two files are duplicates when their plaintext SHA-256 is identical.
    Near-duplicates (a file with one byte changed, a re-saved copy, a re-encoded image) have a
    different hash and are NOT detected;
  * duplicates are looked for inside ONE owner's files. The same content owned by two different
    people is not reported: each file is encrypted under its own key and has its own owner, so it
    could not be merged safely, and reporting it would reveal what other users store.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.db.base import Database
from app.reports.models import ColdRow, ColdThresholds, DuplicateRow

GIB = Decimal(2**30)
MONEY = Decimal("0.000001")  # six decimal places, like the pricing columns

COLD_LABEL = "candidate for archival review"

DEFAULT_THRESHOLDS = ColdThresholds(idle_days=90, max_accesses=3, window_days=365)
MAX_DAYS = 3650

PRICE_NOTE = (
    "Prices come from provider_pricing and are ILLUSTRATIVE examples, not real cloud quotes."
)
DUPLICATE_NOTES = [
    PRICE_NOTE,
    "Only EXACT duplicates are found: files with an identical SHA-256 of their content. "
    "Near-duplicates (a slightly edited or re-encoded copy) are NOT detected.",
    "Duplicates are looked for within one owner's files. The same content owned by two "
    "different users is not reported.",
    "The oldest copy of each group is treated as the original; the others are the extras. "
    "READ-ONLY: nothing was merged, moved or deleted.",
]
COLD_NOTES = [
    "A file counts as 'accessed' only through a SUCCESSFUL download. Uploads, failed and "
    "denied attempts do not count. If a file was never downloaded, its age is counted from "
    "its upload.",
    "READ-ONLY: this is a list of candidates for a human to review. Nothing was migrated, "
    "archived or deleted, and no saving or payback is estimated.",
]


class InvalidReportRequest(ValueError):
    """A threshold is outside its allowed range (mapped to HTTP 422)."""


def resolve_thresholds(
    config: dict[str, str],
    idle_days: int | None,
    max_accesses: int | None,
    window_days: int | None,
) -> ColdThresholds:
    """Request value > system_config value > built-in default. Invalid values are refused."""

    def pick(request_value: int | None, key: str, default: int) -> int:
        if request_value is not None:
            return request_value
        try:
            return int(config[key])
        except (KeyError, ValueError):
            return default

    thresholds = ColdThresholds(
        idle_days=pick(idle_days, "cold.idle_days", DEFAULT_THRESHOLDS.idle_days),
        max_accesses=pick(max_accesses, "cold.max_accesses", DEFAULT_THRESHOLDS.max_accesses),
        window_days=pick(window_days, "cold.window_days", DEFAULT_THRESHOLDS.window_days),
    )
    if not 0 <= thresholds.idle_days <= MAX_DAYS:
        raise InvalidReportRequest(f"idle_days must be between 0 and {MAX_DAYS}")
    if not 1 <= thresholds.max_accesses <= 1_000_000:
        raise InvalidReportRequest("max_accesses must be at least 1")
    if not 1 <= thresholds.window_days <= MAX_DAYS:
        raise InvalidReportRequest(f"window_days must be between 1 and {MAX_DAYS}")
    return thresholds


def monthly_cost(size_bytes: int, usd_per_gib_month: Decimal) -> Decimal:
    """Storage cost of ONE copy per month, unrounded: (bytes / 2**30) * price per GiB-month."""
    return Decimal(size_bytes) / GIB * usd_per_gib_month


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def _copy_dict(row: DuplicateRow) -> dict[str, Any]:
    return {
        "file_id": row.file_id,
        "file_name": row.file_name,
        "file_size_bytes": row.file_size_bytes,
        "uploaded_at": row.upload_time.isoformat(),
        "provider": row.provider_code,
        "region": row.region_code,
        "storage_class": row.tier_code,
        "role": "original (kept)" if row.copy_rank == 1 else "extra copy",
    }


def build_duplicate_report(rows: list[DuplicateRow], as_of: datetime, scope: str) -> dict[str, Any]:
    """Turn SQL rows (every member of every duplicate group) into the report."""
    members: dict[tuple[int, str], list[DuplicateRow]] = defaultdict(list)
    for row in rows:
        members[(row.owner_id, row.content_sha256)].append(row)

    groups = []
    for (owner_id, sha), copies in members.items():
        copies.sort(key=lambda c: c.copy_rank)
        extras = [c for c in copies if c.copy_rank > 1]
        wasted_bytes = sum(c.file_size_bytes for c in extras)
        priced = [c for c in extras if c.storage_usd_per_gb_month is not None]
        monthly = sum(
            (
                monthly_cost(c.file_size_bytes, c.storage_usd_per_gb_month)
                for c in priced
                if c.storage_usd_per_gb_month is not None
            ),
            Decimal(0),
        )
        groups.append(
            {
                "owner_id": owner_id,
                "content_sha256": sha,
                "copies": len(copies),
                "extra_copies": len(extras),
                "wasted_bytes": wasted_bytes,
                "estimated_monthly_waste_usd": str(_money(monthly)),
                "unpriced_extra_copies": len(extras) - len(priced),
                "files": [_copy_dict(c) for c in copies],
            }
        )
    groups.sort(key=lambda g: (-g["wasted_bytes"], g["owner_id"], g["content_sha256"]))

    total_monthly = sum((Decimal(g["estimated_monthly_waste_usd"]) for g in groups), Decimal(0))
    return {
        "report": "duplicate files",
        "read_only": True,
        "as_of": as_of.isoformat(),
        "scope": scope,
        "groups_found": len(groups),
        "total_extra_copies": sum(g["extra_copies"] for g in groups),
        "total_wasted_bytes": sum(g["wasted_bytes"] for g in groups),
        "total_estimated_monthly_waste_usd": str(_money(total_monthly)),
        "prices_are_illustrative": True,
        "notes": DUPLICATE_NOTES,
        "groups": groups,
    }


def _evidence(row: ColdRow, t: ColdThresholds) -> str:
    if row.last_download_at is None:
        basis = f"never downloaded; counted from its upload on {row.upload_time:%Y-%m-%d}"
    else:
        basis = f"last successful download {row.last_download_at:%Y-%m-%d}"
    return (
        f"idle {row.idle_days} days (threshold: more than {t.idle_days}); {basis}; "
        f"successful downloads in the last {t.window_days} days: {row.downloads_window} "
        f"(threshold: fewer than {t.max_accesses}); in the last 30 days: {row.downloads_30d}"
    )


def build_cold_report(
    rows: list[ColdRow], thresholds: ColdThresholds, as_of: datetime, scope: str
) -> dict[str, Any]:
    files = [
        {
            "file_id": r.file_id,
            "owner_id": r.owner_id,
            "file_name": r.file_name,
            "file_size_bytes": r.file_size_bytes,
            "label": COLD_LABEL,
            "provider": r.provider_code,
            "region": r.region_code,
            "storage_class": r.tier_code,
            "provider_storage_class_name": r.native_class_name,
            "last_access": None if r.last_download_at is None else r.last_download_at.isoformat(),
            "uploaded_at": r.upload_time.isoformat(),
            "idle_days": r.idle_days,
            "downloads_last_30_days": r.downloads_30d,
            "downloads_in_window": r.downloads_window,
            "evidence": _evidence(r, thresholds),
        }
        for r in rows
    ]
    return {
        "report": "cold files",
        "read_only": True,
        "as_of": as_of.isoformat(),
        "scope": scope,
        "thresholds": {
            "idle_days_more_than": thresholds.idle_days,
            "downloads_fewer_than": thresholds.max_accesses,
            "download_window_days": thresholds.window_days,
        },
        "files_found": len(files),
        "notes": COLD_NOTES,
        "files": files,
    }


@dataclass(frozen=True)
class ReportScope:
    """Whose files a report covers. `owner_id` None = every owner (ADMIN and AUDITOR only)."""

    owner_id: int | None

    @property
    def label(self) -> str:
        return "all owners" if self.owner_id is None else f"owner {self.owner_id} only"


class ReportService:
    def __init__(self, database: Database):
        self.database = database

    def duplicates(self, scope: ReportScope) -> dict[str, Any]:
        with self.database.transaction() as session:
            as_of = session.reports.db_now()
            rows = session.reports.duplicate_rows(scope.owner_id)
        return build_duplicate_report(rows, as_of, scope.label)

    def cold_files(
        self,
        scope: ReportScope,
        idle_days: int | None = None,
        max_accesses: int | None = None,
        window_days: int | None = None,
    ) -> dict[str, Any]:
        with self.database.transaction() as session:
            thresholds = resolve_thresholds(
                session.reports.cold_config(), idle_days, max_accesses, window_days
            )
            as_of = session.reports.db_now()
            rows = session.reports.cold_rows(scope.owner_id, as_of, thresholds)
        return build_cold_report(rows, thresholds, as_of, scope.label)
