-- cleanup_integration_tests.sql  STATUS: NOT EXECUTED
-- Removes the users created by tests/integration (itest_*@example.test).
-- audit_events is append-only (trigger), and those users have audit rows, so the audit table
-- must be TRUNCATED first. THAT ERASES THE WHOLE AUDIT TRAIL: use only on a development database.
--   sqlplus cloudmeta@localhost:1521/FREEPDB1 '@cleanup_integration_tests.sql'
SET DEFINE OFF
SET FEEDBACK ON
ACCEPT cm_confirm CHAR PROMPT 'This TRUNCATES audit_events (all audit rows) and deletes itest users. Type YES: '
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
DELETE FROM users WHERE email LIKE 'itest\_%' ESCAPE '\';
COMMIT;
SELECT COUNT(*) AS remaining_itest_users FROM users WHERE email LIKE 'itest\_%' ESCAPE '\';
