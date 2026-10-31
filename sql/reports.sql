-- =====================================================================
-- reports.sql (Chunk 6)  READ-ONLY. Duplicate files and cold files, for SQL*Plus.
-- This is the same logic the API runs (app/db/repositories/reports.py), written with
-- substitution variables so you can try other thresholds by hand.
-- Prices come from provider_pricing and are ILLUSTRATIVE. GB means GiB (2^30 bytes).
-- Nothing in this file inserts, updates or deletes.
--
--   sqlplus cloudmeta@localhost:1521/FREEPDB1
--   SQL> @sql/reports.sql
-- Change the three numbers below to see the cold list change.
-- =====================================================================
SET DEFINE ON
SET VERIFY OFF
SET LINESIZE 220
SET PAGESIZE 100
COLUMN file_name FORMAT A24
COLUMN content_sha256 FORMAT A16
COLUMN provider_code FORMAT A8
COLUMN region_code FORMAT A14
COLUMN native_class_name FORMAT A12

DEFINE idle_days    = 90
DEFINE max_accesses = 3
DEFINE window_days  = 365

PROMPT
PROMPT == 1. DUPLICATE GROUPS (same owner, same plaintext SHA-256; oldest copy = original) ==
PROMPT Only EXACT duplicates are found. Near-duplicates (different hash) are NOT detected.
PROMPT Same content owned by two different users is NOT reported.

-- one row per group: copies, wasted bytes, estimated monthly waste (extras only, priced copies only)
WITH base AS (
    SELECT f.owner_id, f.content_sha256, f.file_id, f.file_size_bytes,
           ROW_NUMBER() OVER (PARTITION BY f.owner_id, f.content_sha256
                              ORDER BY f.upload_time, f.file_id) AS copy_rank,
           COUNT(*)     OVER (PARTITION BY f.owner_id, f.content_sha256) AS copies
      FROM files f
     WHERE f.status <> 'DELETED' AND f.content_sha256 IS NOT NULL
)
SELECT b.owner_id,
       SUBSTR(b.content_sha256, 1, 12) || '...'                       AS content_sha256,
       MAX(b.copies)                                                  AS copies,
       SUM(CASE WHEN b.copy_rank > 1 THEN b.file_size_bytes END)      AS wasted_bytes,
       ROUND(SUM(CASE WHEN b.copy_rank > 1
                      THEN b.file_size_bytes / 1073741824 * pr.storage_usd_per_gb_month END), 6)
                                                                      AS est_monthly_waste_usd,
       COUNT(CASE WHEN b.copy_rank > 1 AND pr.region_id IS NULL THEN 1 END) AS unpriced_extra_copies
  FROM base b
  LEFT JOIN storage_locations sl ON sl.file_id = b.file_id AND sl.is_primary = 'Y'
  LEFT JOIN provider_pricing pr  ON pr.region_id = sl.region_id AND pr.tier_code = sl.tier_code
 WHERE b.copies > 1
 GROUP BY b.owner_id, b.content_sha256
 ORDER BY wasted_bytes DESC, b.owner_id;

PROMPT
PROMPT == 2. COLD FILES: idle MORE than &idle_days days AND FEWER than &max_accesses downloads in &window_days days ==
PROMPT Access = a SUCCESSFUL download. Never-downloaded files are aged from their upload.
PROMPT Label for every row below: candidate for archival review. Nothing is moved or deleted.

WITH stats AS (
    SELECT f.file_id, f.owner_id, f.file_name, f.file_size_bytes, f.upload_time,
           MAX(CASE WHEN a.action = 'DOWNLOAD' AND a.outcome = 'SUCCESS'
                    THEN a.access_time END) AS last_download_at,
           COUNT(CASE WHEN a.action = 'DOWNLOAD' AND a.outcome = 'SUCCESS'
                       AND a.access_time > SYSTIMESTAMP - NUMTODSINTERVAL(30, 'DAY')
                      THEN 1 END) AS downloads_30d,
           COUNT(CASE WHEN a.action = 'DOWNLOAD' AND a.outcome = 'SUCCESS'
                       AND a.access_time > SYSTIMESTAMP - NUMTODSINTERVAL(&window_days, 'DAY')
                      THEN 1 END) AS downloads_window
      FROM files f
      LEFT JOIN access_logs a ON a.file_id = f.file_id
     WHERE f.status <> 'DELETED'
     GROUP BY f.file_id, f.owner_id, f.file_name, f.file_size_bytes, f.upload_time
), idle AS (
    SELECT s.*, EXTRACT(DAY FROM (CAST(SYSTIMESTAMP AS TIMESTAMP)
                                  - COALESCE(s.last_download_at, s.upload_time))) AS idle_days
      FROM stats s
)
SELECT i.file_id, i.file_name, p.provider_code, r.region_code, sl.tier_code,
       pr.native_class_name, TO_CHAR(i.last_download_at, 'YYYY-MM-DD') AS last_access,
       i.idle_days, i.downloads_30d, i.downloads_window,
       'candidate for archival review' AS label
  FROM idle i
  LEFT JOIN storage_locations sl ON sl.file_id = i.file_id AND sl.is_primary = 'Y'
  LEFT JOIN provider_regions r   ON r.region_id = sl.region_id
  LEFT JOIN cloud_providers p    ON p.provider_id = r.provider_id
  LEFT JOIN provider_pricing pr  ON pr.region_id = sl.region_id AND pr.tier_code = sl.tier_code
 WHERE i.idle_days > &idle_days AND i.downloads_window < &max_accesses
 ORDER BY i.idle_days DESC, i.file_id;

PROMPT == reports.sql finished (read-only: no rows were changed) ==
