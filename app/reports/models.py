"""Rows returned by the report queries. Plain data; no behaviour."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class DuplicateRow:
    """One file that belongs to a group of identical files (same owner, same plaintext SHA-256).

    `copy_rank` 1 is the copy that is kept as the original (oldest upload, then lowest file_id);
    every other copy is an "extra" and counts as waste.
    """

    owner_id: int
    content_sha256: str
    file_id: int
    file_name: str
    file_size_bytes: int
    upload_time: datetime
    copy_rank: int
    provider_code: str | None
    region_code: str | None
    tier_code: str | None
    storage_usd_per_gb_month: Decimal | None  # None = this copy has no priced location
    is_illustrative: bool | None


@dataclass(frozen=True)
class ColdRow:
    """One file that passed both cold thresholds in SQL, with the evidence the SQL computed."""

    file_id: int
    owner_id: int
    file_name: str
    file_size_bytes: int
    upload_time: datetime
    last_download_at: datetime | None  # last SUCCESSFUL download, None if never downloaded
    idle_days: int  # whole days since last download, or since upload if never downloaded
    downloads_30d: int
    downloads_window: int  # successful downloads inside the lookback window
    provider_code: str | None
    region_code: str | None
    tier_code: str | None
    native_class_name: str | None


@dataclass(frozen=True)
class ColdThresholds:
    idle_days: int  # a file is cold only if it has been idle for MORE than this many days
    max_accesses: int  # ... and has FEWER than this many successful downloads in the window
    window_days: int  # lookback window for counting downloads
