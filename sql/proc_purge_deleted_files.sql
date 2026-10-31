-- =====================================================================
-- proc_purge_deleted_files.sql  STATUS: NOT EXECUTED
-- Stored procedure: permanently removes METADATA of soft-deleted files that were deleted
-- more than p_older_than_days days ago.
--   cd sql
--   sqlplus cloudmeta@localhost:1521/XEPDB1 '@proc_purge_deleted_files.sql'
-- Why access_logs is deleted explicitly: fk_logs_file has no ON DELETE CASCADE, so a file
-- that has log rows cannot be removed until those rows are gone (otherwise ORA-02292).
-- Every other child table of files (file_encryption, storage_locations, replication_jobs,
-- selection_decisions -> selection_candidates, recommendations) is ON DELETE CASCADE.
-- The procedure does NOT commit: the caller decides (COMMIT or ROLLBACK).
-- Stored objects in cloud storage are removed by the application's delete flow, not here.
-- =====================================================================
SET DEFINE OFF
SET SQLBLANKLINES ON

CREATE OR REPLACE PROCEDURE purge_deleted_files (
    p_older_than_days IN  NUMBER DEFAULT 30,
    p_files_purged    OUT NUMBER,
    p_logs_purged     OUT NUMBER
) AS
    v_cutoff TIMESTAMP(6);
BEGIN
    IF p_older_than_days IS NULL OR p_older_than_days < 0 THEN
        RAISE_APPLICATION_ERROR(-20030, 'p_older_than_days must be a number >= 0');
    END IF;

    v_cutoff := SYSTIMESTAMP - NUMTODSINTERVAL(p_older_than_days, 'DAY');

    -- 1. children without cascade
    DELETE FROM access_logs
     WHERE file_id IN (SELECT file_id FROM files
                        WHERE status = 'DELETED' AND deleted_at < v_cutoff);
    p_logs_purged := SQL%ROWCOUNT;

    -- 2. the files (the other child tables cascade)
    DELETE FROM files
     WHERE status = 'DELETED' AND deleted_at < v_cutoff;
    p_files_purged := SQL%ROWCOUNT;

    -- 3. one append-only audit row summarising the purge (audit_events has no FK to files)
    IF p_files_purged > 0 THEN
        INSERT INTO audit_events (event_type, target_type, outcome, details_json)
        VALUES ('FILE_DELETED', 'SYSTEM', 'SUCCESS',
                '{"action":"purge_deleted_files","older_than_days":' || p_older_than_days
                || ',"files_purged":' || p_files_purged
                || ',"logs_purged":' || p_logs_purged || '}');
    END IF;
END purge_deleted_files;
/
SHOW ERRORS PROCEDURE purge_deleted_files

PROMPT == purge_deleted_files created. Demo: @demo_purge_procedure.sql ==
