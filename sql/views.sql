-- =====================================================================
-- views.sql (Chunk 1)  STATUS: NOT EXECUTED
-- v_file_metadata replaces legacy File_Metadata_View (which used INNER JOINs and
-- therefore hid any file without a storage location).
-- =====================================================================
SET DEFINE OFF
SET SQLBLANKLINES ON
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK
PROMPT == views.sql ==

CREATE OR REPLACE VIEW v_file_metadata AS
SELECT f.file_id, f.file_name, f.file_size_bytes, f.file_type, f.security_level, f.status,
       u.full_name AS owner_name,
       p.provider_name, r.region_code, sl.tier_code, sl.replica_status,
       f.upload_time
FROM   files f
JOIN   users u ON u.user_id = f.owner_id
LEFT JOIN storage_locations sl ON sl.file_id = f.file_id AND sl.is_primary = 'Y'
LEFT JOIN provider_regions r   ON r.region_id = sl.region_id
LEFT JOIN cloud_providers p    ON p.provider_id = r.provider_id;

-- Replaces Files.AccessFrequency. "Access" = successful DOWNLOAD.
CREATE OR REPLACE VIEW v_file_access_stats AS
SELECT f.file_id, f.file_name,
       COUNT(CASE WHEN a.action = 'DOWNLOAD' AND a.outcome = 'SUCCESS' THEN 1 END) AS downloads_total,
       COUNT(CASE WHEN a.action = 'DOWNLOAD' AND a.outcome = 'SUCCESS'
                   AND a.access_time >= SYSTIMESTAMP - INTERVAL '30' DAY THEN 1 END) AS downloads_30d,
       MAX(CASE WHEN a.action = 'DOWNLOAD' AND a.outcome = 'SUCCESS' THEN a.access_time END) AS last_download_at
FROM   files f
LEFT JOIN access_logs a ON a.file_id = f.file_id
GROUP BY f.file_id, f.file_name;

CREATE OR REPLACE VIEW v_file_replication_status AS
SELECT f.file_id, f.file_name, f.desired_replicas,
       COUNT(CASE WHEN sl.replica_status = 'OK' THEN 1 END) AS ok_copies,
       COUNT(sl.location_id)                                AS total_copies,
       CASE WHEN COUNT(CASE WHEN sl.replica_status = 'OK' THEN 1 END) = 0 THEN 'NONE'
            WHEN COUNT(CASE WHEN sl.replica_status = 'OK' THEN 1 END) >= f.desired_replicas THEN 'FULL'
            ELSE 'PARTIAL' END AS replication_state
FROM   files f
LEFT JOIN storage_locations sl ON sl.file_id = f.file_id
WHERE  f.status <> 'DELETED'
GROUP BY f.file_id, f.file_name, f.desired_replicas;

-- Files with identical plaintext hash (scoping by owner is done by the application)
CREATE OR REPLACE VIEW v_duplicate_files AS
SELECT content_sha256,
       COUNT(*)                                     AS copies,
       SUM(file_size_bytes)                         AS total_bytes,
       SUM(file_size_bytes) - MAX(file_size_bytes)  AS wasted_bytes
FROM   files
WHERE  content_sha256 IS NOT NULL AND status <> 'DELETED'
GROUP BY content_sha256
HAVING COUNT(*) > 1;

-- Estimated storage cost per copy (ILLUSTRATIVE prices). NULL = no price row.
CREATE OR REPLACE VIEW v_location_monthly_cost AS
SELECT sl.location_id, sl.file_id, f.file_name,
       p.provider_name, r.region_code, sl.tier_code, sl.is_primary,
       ROUND(f.file_size_bytes / 1073741824 * pr.storage_usd_per_gb_month, 6) AS est_storage_usd_month,
       pr.is_illustrative
FROM   storage_locations sl
JOIN   files f             ON f.file_id = sl.file_id
JOIN   provider_regions r  ON r.region_id = sl.region_id
JOIN   cloud_providers p   ON p.provider_id = r.provider_id
LEFT JOIN provider_pricing pr ON pr.region_id = sl.region_id AND pr.tier_code = sl.tier_code
WHERE  f.status <> 'DELETED';

-- Integrity check: should return 0 rows
CREATE OR REPLACE VIEW v_unpriced_locations AS
SELECT sl.location_id, sl.file_id, sl.region_id, sl.tier_code
FROM   storage_locations sl
LEFT JOIN provider_pricing pr ON pr.region_id = sl.region_id AND pr.tier_code = sl.tier_code
WHERE  pr.region_id IS NULL;

CREATE OR REPLACE VIEW v_region_usage AS
SELECT p.provider_name, r.region_code, r.health_status,
       COUNT(sl.location_id)                         AS copies,
       NVL(SUM(f.file_size_bytes), 0)                AS bytes_stored
FROM   provider_regions r
JOIN   cloud_providers p ON p.provider_id = r.provider_id
LEFT JOIN storage_locations sl ON sl.region_id = r.region_id
LEFT JOIN files f ON f.file_id = sl.file_id AND f.status <> 'DELETED'
GROUP BY p.provider_name, r.region_code, r.health_status;

CREATE OR REPLACE VIEW v_region_latency_summary AS
SELECT p.provider_name, r.region_code,
       COUNT(*)                                                   AS samples,
       ROUND(AVG(a.latency_ms), 2)                                AS avg_latency_ms,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY a.latency_ms), 2) AS median_latency_ms,
       MAX(a.latency_ms)                                          AS max_latency_ms
FROM   access_logs a
JOIN   provider_regions r ON r.region_id = a.served_by_region_id
JOIN   cloud_providers p  ON p.provider_id = r.provider_id
WHERE  a.outcome = 'SUCCESS' AND a.latency_ms IS NOT NULL
GROUP BY p.provider_name, r.region_code;

PROMPT == views.sql finished ==
