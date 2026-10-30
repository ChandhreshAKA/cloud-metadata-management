-- =====================================================================
-- explain_reports.sql (Chunk 7 documentation)  READ-ONLY. Plans of the two report queries.
-- Chunk 6 added the duplicate-file and cold-file queries but NO index and NO schema change, so this
-- script only shows how Oracle runs them with the indexes that already exist
-- (ix_files_sha256, ix_files_owner_time, ix_logs_file_time, ...). No plan has been recorded yet.
--
--   sqlplus cloudmeta@localhost:1521/FREEPDB1 '@explain_reports.sql'
--
-- For each query: (a) the optimizer's ESTIMATED plan (EXPLAIN PLAN + DBMS_XPLAN.DISPLAY), (b) the
-- ACTUAL plan with real row counts and buffer gets (GATHER_PLAN_STATISTICS + DISPLAY_CURSOR, needs
-- SELECT_CATALOG_ROLE). SERVEROUTPUT must stay OFF or DISPLAY_CURSOR reports the wrong statement.
-- For a meaningful plan load data first, for example sql/synthetic_data.sql 100000 (clean up with
-- sql/cleanup_synthetic.sql). The thresholds are the report defaults; change them here to compare.
-- Writes nothing except the session's plan table rows.
-- =====================================================================
SET DEFINE ON
SET VERIFY OFF
SET SERVEROUTPUT OFF
SET TIMING ON
SET LINESIZE 220
SET PAGESIZE 200
SET TRIMSPOOL ON
SPOOL explain_reports.log

DEFINE idle_days    = 90
DEFINE max_accesses = 3
DEFINE window_days  = 365

PROMPT == R1. DUPLICATE query: estimated plan ==
EXPLAIN PLAN SET STATEMENT_ID = 'R1_DUP' FOR
WITH base AS (
    SELECT f.owner_id, f.content_sha256, f.file_id, f.file_name, f.file_size_bytes, f.upload_time,
           ROW_NUMBER() OVER (PARTITION BY f.owner_id, f.content_sha256
                              ORDER BY f.upload_time, f.file_id) AS copy_rank,
           COUNT(*) OVER (PARTITION BY f.owner_id, f.content_sha256) AS copies
      FROM files f
     WHERE f.status <> 'DELETED' AND f.content_sha256 IS NOT NULL
)
SELECT b.owner_id, b.content_sha256, b.file_id, b.copy_rank, pr.storage_usd_per_gb_month
  FROM base b
  LEFT JOIN storage_locations sl ON sl.file_id = b.file_id AND sl.is_primary = 'Y'
  LEFT JOIN provider_pricing pr  ON pr.region_id = sl.region_id AND pr.tier_code = sl.tier_code
 WHERE b.copies > 1;
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY('PLAN_TABLE', 'R1_DUP', 'TYPICAL'));

PROMPT == R1. DUPLICATE query: actual plan with row counts and buffers ==
SELECT /*+ GATHER_PLAN_STATISTICS */ COUNT(*) AS duplicate_member_rows FROM (
    WITH base AS (
        SELECT f.owner_id, f.content_sha256, f.file_id, f.file_size_bytes, f.upload_time,
               ROW_NUMBER() OVER (PARTITION BY f.owner_id, f.content_sha256
                                  ORDER BY f.upload_time, f.file_id) AS copy_rank,
               COUNT(*) OVER (PARTITION BY f.owner_id, f.content_sha256) AS copies
          FROM files f
         WHERE f.status <> 'DELETED' AND f.content_sha256 IS NOT NULL
    )
    SELECT b.owner_id, b.file_id, pr.storage_usd_per_gb_month
      FROM base b
      LEFT JOIN storage_locations sl ON sl.file_id = b.file_id AND sl.is_primary = 'Y'
      LEFT JOIN provider_pricing pr  ON pr.region_id = sl.region_id AND pr.tier_code = sl.tier_code
     WHERE b.copies > 1
);
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR(NULL, NULL, 'ALLSTATS LAST'));

PROMPT == R2. COLD-FILE query: estimated plan ==
EXPLAIN PLAN SET STATEMENT_ID = 'R2_COLD' FOR
WITH stats AS (
    SELECT f.file_id, f.owner_id, f.upload_time,
           MAX(CASE WHEN a.action = 'DOWNLOAD' AND a.outcome = 'SUCCESS'
                    THEN a.access_time END) AS last_download_at,
           COUNT(CASE WHEN a.action = 'DOWNLOAD' AND a.outcome = 'SUCCESS'
                       AND a.access_time > SYSTIMESTAMP - NUMTODSINTERVAL(&window_days, 'DAY')
                      THEN 1 END) AS downloads_window
      FROM files f
      LEFT JOIN access_logs a ON a.file_id = f.file_id
     WHERE f.status <> 'DELETED'
     GROUP BY f.file_id, f.owner_id, f.upload_time
), idle AS (
    SELECT s.*, EXTRACT(DAY FROM (CAST(SYSTIMESTAMP AS TIMESTAMP)
                                  - COALESCE(s.last_download_at, s.upload_time))) AS idle_days
      FROM stats s
)
SELECT i.file_id, i.idle_days, i.downloads_window FROM idle i
 WHERE i.idle_days > &idle_days AND i.downloads_window < &max_accesses;
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY('PLAN_TABLE', 'R2_COLD', 'TYPICAL'));

PROMPT == R2. COLD-FILE query: actual plan with row counts and buffers ==
SELECT /*+ GATHER_PLAN_STATISTICS */ COUNT(*) AS cold_candidates FROM (
    WITH stats AS (
        SELECT f.file_id, f.owner_id, f.upload_time,
               MAX(CASE WHEN a.action = 'DOWNLOAD' AND a.outcome = 'SUCCESS'
                        THEN a.access_time END) AS last_download_at,
               COUNT(CASE WHEN a.action = 'DOWNLOAD' AND a.outcome = 'SUCCESS'
                           AND a.access_time > SYSTIMESTAMP - NUMTODSINTERVAL(&window_days, 'DAY')
                          THEN 1 END) AS downloads_window
          FROM files f
          LEFT JOIN access_logs a ON a.file_id = f.file_id
         WHERE f.status <> 'DELETED'
         GROUP BY f.file_id, f.owner_id, f.upload_time
    ), idle AS (
        SELECT s.*, EXTRACT(DAY FROM (CAST(SYSTIMESTAMP AS TIMESTAMP)
                                      - COALESCE(s.last_download_at, s.upload_time))) AS idle_days
          FROM stats s
    )
    SELECT i.file_id FROM idle i
     WHERE i.idle_days > &idle_days AND i.downloads_window < &max_accesses
);
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR(NULL, NULL, 'ALLSTATS LAST'));

SPOOL OFF
PROMPT == explain_reports.sql finished (read-only) ==
