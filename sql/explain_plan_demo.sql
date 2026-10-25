-- =====================================================================
-- explain_plan_demo.sql  STATUS: NOT EXECUTED
-- BEFORE/AFTER index comparison on access_logs, using synthetic data.
--   1. sqlplus cloudmeta@localhost:1521/FREEPDB1 '@synthetic_data.sql' 10000
--   2. sqlplus cloudmeta@localhost:1521/FREEPDB1 '@explain_plan_demo.sql'
--   3. sqlplus cloudmeta@localhost:1521/FREEPDB1 '@synthetic_data.sql' 100000
--   4. sqlplus cloudmeta@localhost:1521/FREEPDB1 '@explain_plan_demo.sql'
-- Paste the spooled output (explain_plan_demo.log) into docs/01-explain-results.md.
--
-- For each query it shows:
--   (a) the optimizer's ESTIMATED plan   (EXPLAIN PLAN + DBMS_XPLAN.DISPLAY)
--   (b) the ACTUAL plan with real row counts and buffer gets
--       (GATHER_PLAN_STATISTICS + DBMS_XPLAN.DISPLAY_CURSOR, needs SELECT_CATALOG_ROLE)
-- Phase BEFORE = the three ix_logs_* indexes dropped. Phase AFTER = recreated from
-- indexes_access_logs.sql. The indexes remain created when the script ends.
-- SERVEROUTPUT must stay OFF or DISPLAY_CURSOR reports the wrong statement.
-- =====================================================================
SET DEFINE ON
SET VERIFY OFF
SET ECHO ON
SET SERVEROUTPUT OFF
SET TIMING ON
SET LINESIZE 220
SET PAGESIZE 200
SET TRIMSPOOL ON
SET LONG 100000
WHENEVER SQLERROR EXIT SQL.SQLCODE
DECLARE
    v_cnt NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_cnt FROM files WHERE file_name LIKE 'synth\_%' ESCAPE '\';
    IF v_cnt = 0 THEN
        RAISE_APPLICATION_ERROR(-20020, 'No synthetic data. Run synthetic_data.sql first (see run guide step 4)');
    END IF;
END;
/
WHENEVER SQLERROR CONTINUE
SPOOL explain_plan_demo.log

PROMPT ===== Data volume =====
SELECT (SELECT COUNT(*) FROM access_logs) AS access_logs_rows,
       (SELECT COUNT(*) FROM files)       AS files_rows,
       (SELECT COUNT(*) FROM users)       AS users_rows
FROM dual;

COLUMN v_fid NEW_VALUE v_fid NOPRINT
COLUMN v_uid NEW_VALUE v_uid NOPRINT
SELECT MIN(file_id) AS v_fid FROM files WHERE file_name LIKE 'synth\_%' ESCAPE '\';
SELECT MIN(user_id) AS v_uid FROM users WHERE email LIKE 'synth\_user\_%@synthetic.invalid' ESCAPE '\';
PROMPT Using file_id = &v_fid and user_id = &v_uid
SELECT COUNT(*) AS logs_for_this_file FROM access_logs WHERE file_id = &v_fid;
SELECT COUNT(*) AS logs_for_this_user FROM access_logs WHERE user_id = &v_uid;

PROMPT
PROMPT ############ PHASE BEFORE: drop the access_logs indexes ############
BEGIN
    FOR i IN (SELECT 'IX_LOGS_FILE_TIME' AS n FROM dual UNION ALL
              SELECT 'IX_LOGS_USER_TIME' FROM dual UNION ALL
              SELECT 'IX_LOGS_REGION_TIME' FROM dual) LOOP
        BEGIN
            EXECUTE IMMEDIATE 'DROP INDEX ' || i.n;
        EXCEPTION WHEN OTHERS THEN
            IF SQLCODE <> -1418 THEN RAISE; END IF;   -- ORA-01418 index does not exist
        END;
    END LOOP;
END;
/
EXEC DBMS_STATS.GATHER_TABLE_STATS(USER, 'ACCESS_LOGS', cascade => TRUE);
SELECT index_name FROM user_indexes WHERE table_name = 'ACCESS_LOGS' ORDER BY index_name;

PROMPT ===== Q2 BEFORE: access history of one file, newest first =====
EXPLAIN PLAN SET STATEMENT_ID = 'Q2_BEFORE' FOR
SELECT log_id, action, outcome, access_time, latency_ms
FROM   access_logs WHERE file_id = &v_fid ORDER BY access_time DESC;
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY('PLAN_TABLE', 'Q2_BEFORE', 'TYPICAL'));
SET FEEDBACK ONLY
SELECT /*+ GATHER_PLAN_STATISTICS */ log_id, action, outcome, access_time, latency_ms
FROM   access_logs WHERE file_id = &v_fid ORDER BY access_time DESC;
SET FEEDBACK ON
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR(NULL, NULL, 'ALLSTATS LAST'));

PROMPT ===== Q3 BEFORE: latest 50 actions of one user =====
EXPLAIN PLAN SET STATEMENT_ID = 'Q3_BEFORE' FOR
SELECT log_id, file_id, action, access_time
FROM   access_logs WHERE user_id = &v_uid ORDER BY access_time DESC FETCH FIRST 50 ROWS ONLY;
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY('PLAN_TABLE', 'Q3_BEFORE', 'TYPICAL'));
SET FEEDBACK ONLY
SELECT /*+ GATHER_PLAN_STATISTICS */ log_id, file_id, action, access_time
FROM   access_logs WHERE user_id = &v_uid ORDER BY access_time DESC FETCH FIRST 50 ROWS ONLY;
SET FEEDBACK ON
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR(NULL, NULL, 'ALLSTATS LAST'));

PROMPT ===== Q4 BEFORE (control): last successful download per file; the indexes are NOT expected to help much =====
EXPLAIN PLAN SET STATEMENT_ID = 'Q4_BEFORE' FOR
SELECT file_id, MAX(access_time) AS last_download
FROM   access_logs WHERE action = 'DOWNLOAD' AND outcome = 'SUCCESS' GROUP BY file_id;
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY('PLAN_TABLE', 'Q4_BEFORE', 'TYPICAL'));
SET FEEDBACK ONLY
SELECT /*+ GATHER_PLAN_STATISTICS */ file_id, MAX(access_time) AS last_download
FROM   access_logs WHERE action = 'DOWNLOAD' AND outcome = 'SUCCESS' GROUP BY file_id;
SET FEEDBACK ON
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR(NULL, NULL, 'ALLSTATS LAST'));

PROMPT
PROMPT ############ PHASE AFTER: create the access_logs indexes ############
@@indexes_access_logs.sql
EXEC DBMS_STATS.GATHER_TABLE_STATS(USER, 'ACCESS_LOGS', cascade => TRUE);
SELECT index_name FROM user_indexes WHERE table_name = 'ACCESS_LOGS' ORDER BY index_name;

PROMPT ===== Q2 AFTER =====
EXPLAIN PLAN SET STATEMENT_ID = 'Q2_AFTER' FOR
SELECT log_id, action, outcome, access_time, latency_ms
FROM   access_logs WHERE file_id = &v_fid ORDER BY access_time DESC;
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY('PLAN_TABLE', 'Q2_AFTER', 'TYPICAL'));
SET FEEDBACK ONLY
SELECT /*+ GATHER_PLAN_STATISTICS */ log_id, action, outcome, access_time, latency_ms
FROM   access_logs WHERE file_id = &v_fid ORDER BY access_time DESC;
SET FEEDBACK ON
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR(NULL, NULL, 'ALLSTATS LAST'));

PROMPT ===== Q3 AFTER =====
EXPLAIN PLAN SET STATEMENT_ID = 'Q3_AFTER' FOR
SELECT log_id, file_id, action, access_time
FROM   access_logs WHERE user_id = &v_uid ORDER BY access_time DESC FETCH FIRST 50 ROWS ONLY;
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY('PLAN_TABLE', 'Q3_AFTER', 'TYPICAL'));
SET FEEDBACK ONLY
SELECT /*+ GATHER_PLAN_STATISTICS */ log_id, file_id, action, access_time
FROM   access_logs WHERE user_id = &v_uid ORDER BY access_time DESC FETCH FIRST 50 ROWS ONLY;
SET FEEDBACK ON
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR(NULL, NULL, 'ALLSTATS LAST'));

PROMPT ===== Q4 AFTER (control) =====
EXPLAIN PLAN SET STATEMENT_ID = 'Q4_AFTER' FOR
SELECT file_id, MAX(access_time) AS last_download
FROM   access_logs WHERE action = 'DOWNLOAD' AND outcome = 'SUCCESS' GROUP BY file_id;
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY('PLAN_TABLE', 'Q4_AFTER', 'TYPICAL'));
SET FEEDBACK ONLY
SELECT /*+ GATHER_PLAN_STATISTICS */ file_id, MAX(access_time) AS last_download
FROM   access_logs WHERE action = 'DOWNLOAD' AND outcome = 'SUCCESS' GROUP BY file_id;
SET FEEDBACK ON
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR(NULL, NULL, 'ALLSTATS LAST'));

SPOOL OFF
