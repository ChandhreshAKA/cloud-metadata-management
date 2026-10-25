-- cleanup_integration_tests.sql  STATUS: NOT EXECUTED
-- Removes the users, files and logs created by tests/integration (itest_*@example.test).
-- audit_events is append-only (trigger), and those users have audit rows, so the audit table
-- must be TRUNCATED first. THAT ERASES THE WHOLE AUDIT TRAIL: use only on a development database.
--   sqlplus cloudmeta@localhost:1521/FREEPDB1 '@cleanup_integration_tests.sql'
-- Files deleted here take their encryption rows, locations and selection decisions with them
-- (ON DELETE CASCADE). Objects in the simulated storage folder are NOT touched by this script
-- (the integration tests use a temporary folder that pytest removes itself).
SET DEFINE OFF
SET FEEDBACK ON
ACCEPT cm_confirm CHAR PROMPT 'This TRUNCATES audit_events (all audit rows) and deletes itest users and files. Type YES: '
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK
SET DEFINE ON
BEGIN
    IF '&cm_confirm' <> 'YES' THEN
        RAISE_APPLICATION_ERROR(-20990, 'Cancelled');
    END IF;
END;
/
SET DEFINE OFF
TRUNCATE TABLE audit_events;
DELETE FROM access_logs WHERE user_id IN (SELECT user_id FROM users WHERE email LIKE 'itest\_%' ESCAPE '\');
DELETE FROM access_logs WHERE file_id IN (SELECT file_id FROM files WHERE owner_id IN
       (SELECT user_id FROM users WHERE email LIKE 'itest\_%' ESCAPE '\'));
DELETE FROM files WHERE owner_id IN (SELECT user_id FROM users WHERE email LIKE 'itest\_%' ESCAPE '\');
DELETE FROM users WHERE email LIKE 'itest\_%' ESCAPE '\';
COMMIT;
SELECT COUNT(*) AS remaining_itest_users FROM users WHERE email LIKE 'itest\_%' ESCAPE '\';
