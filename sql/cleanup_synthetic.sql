-- =====================================================================
-- cleanup_synthetic.sql  STATUS: NOT EXECUTED
-- Removes ONLY the synthetic users, files and logs created by synthetic_data.sql.
--   sqlplus cloudmeta@localhost:1521/FREEPDB1 @cleanup_synthetic.sql
-- =====================================================================
SET DEFINE OFF
SET FEEDBACK ON
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK

DELETE FROM access_logs WHERE file_id IN (SELECT file_id FROM files WHERE file_name LIKE 'synth\_%' ESCAPE '\');
DELETE FROM access_logs WHERE user_id IN (SELECT user_id FROM users WHERE email LIKE 'synth\_user\_%@synthetic.invalid' ESCAPE '\');
DELETE FROM files WHERE file_name LIKE 'synth\_%' ESCAPE '\';
DELETE FROM users WHERE email LIKE 'synth\_user\_%@synthetic.invalid' ESCAPE '\';
COMMIT;

BEGIN
    DBMS_STATS.GATHER_TABLE_STATS(USER, 'USERS',       cascade => TRUE);
    DBMS_STATS.GATHER_TABLE_STATS(USER, 'FILES',       cascade => TRUE);
    DBMS_STATS.GATHER_TABLE_STATS(USER, 'ACCESS_LOGS', cascade => TRUE);
END;
/

SELECT (SELECT COUNT(*) FROM users) AS users_rows, (SELECT COUNT(*) FROM files) AS files_rows,
       (SELECT COUNT(*) FROM access_logs) AS access_logs_rows FROM dual;
