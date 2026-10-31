"""SQL for the read-only reports (chunk 6). SELECT statements only: nothing here writes.

Every statement is a module-level constant with bind variables. The only variation is whether the
query is limited to one owner; that is two complete constants (built from constant fragments),
never a value from a request.

Definitions used by both reports (and by sql/reports.sql, which is the same logic for SQL*Plus):
  * a file counts only while status <> 'DELETED';
  * "access" means a SUCCESSFUL DOWNLOAD (as in v_file_access_stats); uploads, failed and denied
    attempts do not count;
  * duplicates are files of the SAME OWNER with the SAME plaintext SHA-256.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import oracledb

from app.reports.models import ColdRow, ColdThresholds, DuplicateRow

_NOW = "SELECT CAST(SYSTIMESTAMP AS TIMESTAMP) FROM dual"

_CONFIG = """
    SELECT config_key, config_value FROM system_config
     WHERE config_key IN ('cold.idle_days', 'cold.max_accesses', 'cold.window_days')
"""

# --- duplicates ---------------------------------------------------------------------------------
# COUNT(*) OVER (...) is the group size and is computed AFTER the status/hash filter, so deleted
# files and files without a hash never form or join a group. A group needs 2 or more members.
_DUP_HEAD = """
    WITH base AS (
        SELECT f.owner_id, f.content_sha256, f.file_id, f.file_name, f.file_size_bytes,
               f.upload_time,
               ROW_NUMBER() OVER (PARTITION BY f.owner_id, f.content_sha256
                                  ORDER BY f.upload_time, f.file_id) AS copy_rank,
               COUNT(*) OVER (PARTITION BY f.owner_id, f.content_sha256) AS copies
          FROM files f
         WHERE f.status <> 'DELETED' AND f.content_sha256 IS NOT NULL
"""
_DUP_OWN = "           AND f.owner_id = :owner_id\n"
_DUP_TAIL = """
    )
    SELECT b.owner_id, b.content_sha256, b.file_id, b.file_name, b.file_size_bytes, b.upload_time,
           b.copy_rank, p.provider_code, r.region_code, sl.tier_code,
           pr.storage_usd_per_gb_month, pr.is_illustrative
      FROM base b
      LEFT JOIN storage_locations sl ON sl.file_id = b.file_id AND sl.is_primary = 'Y'
      LEFT JOIN provider_regions r   ON r.region_id = sl.region_id
      LEFT JOIN cloud_providers p    ON p.provider_id = r.provider_id
      LEFT JOIN provider_pricing pr  ON pr.region_id = sl.region_id AND pr.tier_code = sl.tier_code
     WHERE b.copies > 1
     ORDER BY b.owner_id, b.content_sha256, b.copy_rank
"""
_DUPLICATES_ALL = f"{_DUP_HEAD}{_DUP_TAIL}"
_DUPLICATES_OWN = f"{_DUP_HEAD}{_DUP_OWN}{_DUP_TAIL}"

# --- cold files ---------------------------------------------------------------------------------
# Rows later than :as_of are ignored so a report "as of" a past time is consistent.
# idle_days is whole days between :as_of and the last successful download (or the upload if the
# file was never downloaded). A file is cold when idle_days > :idle_days AND the number of
# successful downloads inside the lookback window is < :max_accesses (both strict).
_COLD_HEAD = """
    WITH stats AS (
        SELECT f.file_id, f.owner_id, f.file_name, f.file_size_bytes, f.upload_time,
               MAX(CASE WHEN a.action = 'DOWNLOAD' AND a.outcome = 'SUCCESS'
                        THEN a.access_time END) AS last_download_at,
               COUNT(CASE WHEN a.action = 'DOWNLOAD' AND a.outcome = 'SUCCESS'
                           AND a.access_time > :as_of - NUMTODSINTERVAL(30, 'DAY')
                          THEN 1 END) AS downloads_30d,
               COUNT(CASE WHEN a.action = 'DOWNLOAD' AND a.outcome = 'SUCCESS'
                           AND a.access_time > :as_of - NUMTODSINTERVAL(:window_days, 'DAY')
                          THEN 1 END) AS downloads_window
          FROM files f
          LEFT JOIN access_logs a ON a.file_id = f.file_id AND a.access_time <= :as_of
         WHERE f.status <> 'DELETED' AND f.upload_time <= :as_of
"""
_COLD_OWN = "           AND f.owner_id = :owner_id\n"
_COLD_TAIL = """
         GROUP BY f.file_id, f.owner_id, f.file_name, f.file_size_bytes, f.upload_time
    ), idle AS (
        SELECT s.*, EXTRACT(DAY FROM (:as_of - COALESCE(s.last_download_at, s.upload_time)))
                    AS idle_days
          FROM stats s
    )
    SELECT i.file_id, i.owner_id, i.file_name, i.file_size_bytes, i.upload_time,
           i.last_download_at, i.idle_days, i.downloads_30d, i.downloads_window,
           p.provider_code, r.region_code, sl.tier_code, pr.native_class_name
      FROM idle i
      LEFT JOIN storage_locations sl ON sl.file_id = i.file_id AND sl.is_primary = 'Y'
      LEFT JOIN provider_regions r   ON r.region_id = sl.region_id
      LEFT JOIN cloud_providers p    ON p.provider_id = r.provider_id
      LEFT JOIN provider_pricing pr  ON pr.region_id = sl.region_id AND pr.tier_code = sl.tier_code
     WHERE i.idle_days > :idle_days AND i.downloads_window < :max_accesses
     ORDER BY i.idle_days DESC, i.file_id
"""
_COLD_ALL = f"{_COLD_HEAD}{_COLD_TAIL}"
_COLD_OWN_SCOPED = f"{_COLD_HEAD}{_COLD_OWN}{_COLD_TAIL}"


class OracleReportRepository:
    def __init__(self, connection: Any):
        self._conn = connection

    def db_now(self) -> datetime:
        """The database's own clock, so report ages agree with the stored timestamps."""
        cursor = self._conn.cursor()
        cursor.execute(_NOW)
        return cursor.fetchone()[0]

    def cold_config(self) -> dict[str, str]:
        cursor = self._conn.cursor()
        cursor.execute(_CONFIG)
        return {row[0]: row[1] for row in cursor.fetchall()}

    def duplicate_rows(self, owner_id: int | None) -> list[DuplicateRow]:
        cursor = self._conn.cursor()
        if owner_id is None:
            cursor.execute(_DUPLICATES_ALL)
        else:
            cursor.execute(_DUPLICATES_OWN, {"owner_id": owner_id})
        return [
            DuplicateRow(
                owner_id=int(r[0]), content_sha256=r[1], file_id=int(r[2]), file_name=r[3],
                file_size_bytes=int(r[4]), upload_time=r[5], copy_rank=int(r[6]),
                provider_code=r[7], region_code=r[8], tier_code=r[9],
                storage_usd_per_gb_month=None if r[10] is None else Decimal(r[10]),
                is_illustrative=None if r[11] is None else r[11] == "Y",
            )  # fmt: skip
            for r in cursor.fetchall()
        ]

    def cold_rows(
        self, owner_id: int | None, as_of: datetime, thresholds: ColdThresholds
    ) -> list[ColdRow]:
        binds: dict[str, Any] = {
            "as_of": as_of,
            "window_days": thresholds.window_days,
            "idle_days": thresholds.idle_days,
            "max_accesses": thresholds.max_accesses,
        }
        cursor = self._conn.cursor()
        # A Python datetime binds as DATE and silently drops the fraction of a second, which can
        # push a file at an exact day boundary into the wrong day. Bind it as a full TIMESTAMP.
        cursor.setinputsizes(as_of=oracledb.DB_TYPE_TIMESTAMP)
        if owner_id is None:
            cursor.execute(_COLD_ALL, binds)
        else:
            cursor.execute(_COLD_OWN_SCOPED, {**binds, "owner_id": owner_id})
        return [
            ColdRow(
                file_id=int(r[0]), owner_id=int(r[1]), file_name=r[2], file_size_bytes=int(r[3]),
                upload_time=r[4], last_download_at=r[5], idle_days=int(r[6]),
                downloads_30d=int(r[7]), downloads_window=int(r[8]), provider_code=r[9],
                region_code=r[10], tier_code=r[11], native_class_name=r[12],
            )  # fmt: skip
            for r in cursor.fetchall()
        ]
