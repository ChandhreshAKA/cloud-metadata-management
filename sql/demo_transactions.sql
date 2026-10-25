-- =====================================================================
-- demo_transactions.sql  STATUS: NOT EXECUTED
-- COMMIT, ROLLBACK, SAVEPOINT / ROLLBACK TO, atomic multi-step upload, ON DELETE CASCADE
-- and RESTRICT, against the revised schema. Requires install_fresh.sql first.
--   cd C:\dbmsproject\sql
--   sqlplus cloudmeta@localhost:1521/FREEPDB1 @demo_transactions.sql
-- Two statements are EXPECTED to fail (marked EXPECT ERROR). Leaves no rows behind.
-- =====================================================================
SET ECHO ON
SET DEFINE OFF
SET LINESIZE 160
SET PAGESIZE 50
SET FEEDBACK ON
WHENEVER SQLERROR CONTINUE
SPOOL demo_transactions.log

PROMPT ===== T1. COMMIT makes a change permanent =====
INSERT INTO files (owner_id, file_name, file_size_bytes, file_type, security_level)
VALUES ((SELECT user_id FROM users WHERE email = 'harini@example.com'), 'txn_demo_commit.txt', 1024, 'TXT', 'LOW');
COMMIT;
SELECT file_name, security_level, status FROM files WHERE file_name = 'txn_demo_commit.txt';

PROMPT ===== T2. ROLLBACK discards uncommitted work =====
INSERT INTO files (owner_id, file_name, file_size_bytes, file_type, security_level)
VALUES ((SELECT user_id FROM users WHERE email = 'harini@example.com'), 'txn_demo_rollback.txt', 2048, 'TXT', 'LOW');
SELECT COUNT(*) AS visible_in_my_session FROM files WHERE file_name = 'txn_demo_rollback.txt';
ROLLBACK;
SELECT COUNT(*) AS after_rollback FROM files WHERE file_name = 'txn_demo_rollback.txt';

PROMPT ===== T3. SAVEPOINT and ROLLBACK TO: undo part of a transaction =====
INSERT INTO files (owner_id, file_name, file_size_bytes, file_type, security_level)
VALUES ((SELECT user_id FROM users WHERE email = 'harini@example.com'), 'txn_demo_savepoint.txt', 4096, 'TXT', 'LOW');
SAVEPOINT sp_after_file;
INSERT INTO storage_locations (file_id, region_id, tier_code, object_key, is_primary, replica_status)
VALUES ((SELECT file_id FROM files WHERE file_name = 'txn_demo_savepoint.txt'),
        (SELECT r.region_id FROM provider_regions r JOIN cloud_providers p ON p.provider_id = r.provider_id
          WHERE p.provider_code = 'AWS' AND r.region_code = 'ap-south-1'),
        'HOT', 'txn/demo/savepoint.txt', 'Y', 'PENDING');
UPDATE files SET security_level = 'HIGH' WHERE file_name = 'txn_demo_savepoint.txt';
SELECT f.security_level,
       (SELECT COUNT(*) FROM storage_locations s WHERE s.file_id = f.file_id) AS locations
FROM   files f WHERE f.file_name = 'txn_demo_savepoint.txt';
ROLLBACK TO sp_after_file;
SELECT f.security_level,
       (SELECT COUNT(*) FROM storage_locations s WHERE s.file_id = f.file_id) AS locations
FROM   files f WHERE f.file_name = 'txn_demo_savepoint.txt';
COMMIT;
SELECT COUNT(*) AS file_survived_commit FROM files WHERE file_name = 'txn_demo_savepoint.txt';

PROMPT ===== T4. A failed multi-step upload leaves no orphan metadata (EXPECT ERROR ORA-02291) =====
SAVEPOINT sp_upload_start;
INSERT INTO files (owner_id, file_name, file_size_bytes, file_type, security_level)
VALUES ((SELECT user_id FROM users WHERE email = 'harini@example.com'), 'txn_demo_failed_upload.bin', 8192, 'OTHER', 'LOW');
INSERT INTO storage_locations (file_id, region_id, tier_code, object_key, is_primary, replica_status)
VALUES ((SELECT file_id FROM files WHERE file_name = 'txn_demo_failed_upload.bin'),
        999999, 'HOT', 'txn/demo/failed.bin', 'Y', 'PENDING');
ROLLBACK TO sp_upload_start;
SELECT COUNT(*) AS orphan_file_rows FROM files WHERE file_name = 'txn_demo_failed_upload.bin';
COMMIT;

PROMPT ===== T5. ON DELETE CASCADE: deleting a file removes its locations =====
INSERT INTO storage_locations (file_id, region_id, tier_code, object_key, is_primary, replica_status)
VALUES ((SELECT file_id FROM files WHERE file_name = 'txn_demo_savepoint.txt'),
        (SELECT r.region_id FROM provider_regions r JOIN cloud_providers p ON p.provider_id = r.provider_id
          WHERE p.provider_code = 'AWS' AND r.region_code = 'ap-south-1'),
        'HOT', 'txn/demo/savepoint.txt', 'Y', 'OK');
COMMIT;
SELECT COUNT(*) AS locations_before FROM storage_locations WHERE object_key = 'txn/demo/savepoint.txt';
DELETE FROM files WHERE file_name = 'txn_demo_savepoint.txt';
SELECT COUNT(*) AS locations_after FROM storage_locations WHERE object_key = 'txn/demo/savepoint.txt';
COMMIT;

PROMPT ===== T6. ON DELETE RESTRICT: a file with access history cannot be deleted (EXPECT ERROR ORA-02292) =====
DELETE FROM files WHERE file_name = 'project_report.pdf';
ROLLBACK;
SELECT COUNT(*) AS project_report_still_there FROM files WHERE file_name = 'project_report.pdf';

PROMPT ===== Cleanup =====
DELETE FROM files WHERE file_name LIKE 'txn\_demo\_%' ESCAPE '\';
COMMIT;
SELECT COUNT(*) AS txn_demo_rows_left FROM files WHERE file_name LIKE 'txn\_demo\_%' ESCAPE '\';

SPOOL OFF
