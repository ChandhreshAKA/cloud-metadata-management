-- =====================================================================
-- demo_purge_procedure.sql  STATUS: NOT EXECUTED
-- Shows purge_deleted_files: an old soft-deleted file (with a log row) is purged, a recently
-- deleted file and an active file are kept. Ends with ROLLBACK, so it leaves no rows behind.
--   sqlplus cloudmeta@localhost:1521/XEPDB1 '@demo_purge_procedure.sql'
-- Requires install_fresh.sql and proc_purge_deleted_files.sql first.
-- =====================================================================
SET ECHO ON
SET DEFINE OFF
SET LINESIZE 160
SET PAGESIZE 50
SET FEEDBACK ON
WHENEVER SQLERROR CONTINUE
SPOOL demo_purge_procedure.log

PROMPT ===== P1. Set up three files: old-deleted (60 days), recently-deleted (1 day), active =====
INSERT INTO files (owner_id, file_name, file_size_bytes, file_type, security_level, status, deleted_at)
VALUES ((SELECT MIN(user_id) FROM users), 'purge_demo_old.txt', 10, 'TXT', 'LOW', 'DELETED', SYSTIMESTAMP - INTERVAL '60' DAY);
INSERT INTO files (owner_id, file_name, file_size_bytes, file_type, security_level, status, deleted_at)
VALUES ((SELECT MIN(user_id) FROM users), 'purge_demo_recent.txt', 10, 'TXT', 'LOW', 'DELETED', SYSTIMESTAMP - INTERVAL '1' DAY);
INSERT INTO files (owner_id, file_name, file_size_bytes, file_type, security_level)
VALUES ((SELECT MIN(user_id) FROM users), 'purge_demo_active.txt', 10, 'TXT', 'LOW');

-- a log row on the OLD file: this is what would block a plain DELETE with ORA-02292
INSERT INTO access_logs (file_id, user_id, action)
VALUES ((SELECT file_id FROM files WHERE file_name = 'purge_demo_old.txt'),
        (SELECT MIN(user_id) FROM users), 'DELETE');

SELECT file_name, status, deleted_at FROM files WHERE file_name LIKE 'purge\_demo\_%' ESCAPE '\' ORDER BY file_name;

PROMPT ===== P2. EXPECT ERROR: a plain DELETE is blocked by the access_logs row (ORA-02292) =====
DELETE FROM files WHERE file_name = 'purge_demo_old.txt';

PROMPT ===== P3. Call the procedure: purge files deleted more than 30 days ago =====
VARIABLE v_files NUMBER
VARIABLE v_logs  NUMBER
EXEC purge_deleted_files(30, :v_files, :v_logs);
PRINT v_files v_logs

PROMPT ===== P4. Result: only the old file is gone; recent and active remain =====
SELECT file_name, status FROM files WHERE file_name LIKE 'purge\_demo\_%' ESCAPE '\' ORDER BY file_name;

PROMPT ===== P5. The purge left one audit row =====
SELECT event_type, target_type, outcome, details_json
FROM   audit_events
WHERE  details_json LIKE '%purge_deleted_files%'
ORDER  BY event_id DESC FETCH FIRST 1 ROWS ONLY;

PROMPT ===== P6. EXPECT ERROR: bad argument is rejected (ORA-20030) =====
EXEC purge_deleted_files(-5, :v_files, :v_logs);

PROMPT ===== P7. ROLLBACK: leave no demo rows behind =====
ROLLBACK;
SELECT COUNT(*) AS demo_files_left FROM files WHERE file_name LIKE 'purge\_demo\_%' ESCAPE '\';

SPOOL OFF
