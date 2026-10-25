-- =====================================================================
-- synthetic_data.sql  STATUS: NOT EXECUTED
-- Generates SYNTHETIC load so index benefits can be measured.
--   cd C:\dbmsproject\sql
--   sqlplus cloudmeta@localhost:1521/FREEPDB1 @synthetic_data.sql 10000
--   sqlplus cloudmeta@localhost:1521/FREEPDB1 @synthetic_data.sql 100000
-- Argument = number of access_logs rows. Safe to re-run with a different size:
-- it first removes earlier synthetic access_logs, then keeps (or creates) 100 synthetic
-- users and 5,000 synthetic files, then inserts the requested number of log rows.
-- Synthetic rows are recognisable: users synth_user_NNNN@synthetic.invalid,
-- files synth_NNNNNN.dat. Remove everything with cleanup_synthetic.sql.
-- Every 20th file duplicates the content hash of the previous file (for chunk 6).
-- Requires: install_fresh.sql (or the migration) done; EXECUTE on DBMS_RANDOM / DBMS_STATS.
-- =====================================================================
SET DEFINE ON
SET VERIFY OFF
SET ECHO OFF
SET SERVEROUTPUT ON
SET TIMING ON
SET LINESIZE 160
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK

DEFINE n_logs = &1

PROMPT == synthetic_data.sql: target &n_logs access_logs rows ==

-- 1. remove earlier synthetic logs
DELETE FROM access_logs
WHERE  file_id IN (SELECT file_id FROM files WHERE file_name LIKE 'synth\_%' ESCAPE '\');

-- 2. synthetic users (only if none exist yet)
INSERT INTO users (full_name, email, role, account_status)
SELECT 'Synthetic User ' || LPAD(n, 4, '0'),
       'synth_user_' || LPAD(n, 4, '0') || '@synthetic.invalid',
       'USER', 'DISABLED'
FROM  (SELECT LEVEL AS n FROM dual CONNECT BY LEVEL <= 100)
WHERE NOT EXISTS (SELECT 1 FROM users WHERE email LIKE 'synth\_user\_%@synthetic.invalid' ESCAPE '\');

-- 3. synthetic files (only if none exist yet): 5,000 files, sizes 1 KiB..1 GiB, ~5% duplicate hashes
INSERT INTO files (owner_id, file_name, file_size_bytes, file_type, security_level, content_sha256, upload_time)
SELECT u.user_id,
       'synth_' || LPAD(g.n, 6, '0') || '.dat',
       TRUNC(DBMS_RANDOM.VALUE(1024, 1073741824)),
       'OTHER',
       CASE MOD(g.n, 3) WHEN 0 THEN 'LOW' WHEN 1 THEN 'MEDIUM' ELSE 'HIGH' END,
       LOWER(RAWTOHEX(STANDARD_HASH('synth-content-' || CASE WHEN MOD(g.n, 20) = 0 THEN g.n - 1 ELSE g.n END, 'SHA256'))),
       SYSTIMESTAMP - NUMTODSINTERVAL(DBMS_RANDOM.VALUE(0, 365), 'DAY')
FROM  (SELECT LEVEL AS n FROM dual CONNECT BY LEVEL <= 5000) g
JOIN  (SELECT user_id, ROW_NUMBER() OVER (ORDER BY user_id) AS rn
       FROM   users WHERE email LIKE 'synth\_user\_%@synthetic.invalid' ESCAPE '\') u
      ON u.rn = MOD(g.n, 100) + 1
WHERE NOT EXISTS (SELECT 1 FROM files WHERE file_name LIKE 'synth\_%' ESCAPE '\');

COMMIT;

-- 4. synthetic access logs: 80% DOWNLOAD / 15% VIEW_METADATA / 5% UPLOAD,
--    95% SUCCESS / 3% FAILURE / 2% DENIED, latency 15 ms + |N(0,1)| * 60 ms, last 365 days
DECLARE
    c_total  CONSTANT PLS_INTEGER := &n_logs;
    c_batch  CONSTANT PLS_INTEGER := 10000;
    TYPE t_num IS TABLE OF NUMBER;
    TYPE t_str IS TABLE OF VARCHAR2(20);
    TYPE t_ts  IS TABLE OF TIMESTAMP;
    v_files    t_num;
    v_users    t_num;
    v_regions  t_num;
    a_file     t_num;
    a_user     t_num;
    a_action   t_str;
    a_outcome  t_str;
    a_time     t_ts;
    a_lat      t_num;
    a_region   t_num;
    v_done     PLS_INTEGER := 0;
    v_n        PLS_INTEGER;
    r          NUMBER;
BEGIN
    SELECT file_id   BULK COLLECT INTO v_files   FROM files WHERE file_name LIKE 'synth\_%' ESCAPE '\';
    SELECT user_id   BULK COLLECT INTO v_users   FROM users WHERE email LIKE 'synth\_user\_%@synthetic.invalid' ESCAPE '\';
    SELECT region_id BULK COLLECT INTO v_regions FROM provider_regions;
    IF v_files.COUNT = 0 OR v_users.COUNT = 0 OR v_regions.COUNT = 0 THEN
        RAISE_APPLICATION_ERROR(-20010, 'synthetic users/files or provider_regions missing');
    END IF;

    WHILE v_done < c_total LOOP
        v_n := LEAST(c_batch, c_total - v_done);
        a_file := t_num();    a_user := t_num();   a_action := t_str();  a_outcome := t_str();
        a_time := t_ts();     a_lat := t_num();    a_region := t_num();
        a_file.EXTEND(v_n);   a_user.EXTEND(v_n);  a_action.EXTEND(v_n); a_outcome.EXTEND(v_n);
        a_time.EXTEND(v_n);   a_lat.EXTEND(v_n);   a_region.EXTEND(v_n);

        FOR i IN 1 .. v_n LOOP
            a_file(i) := v_files(TRUNC(DBMS_RANDOM.VALUE(1, v_files.COUNT + 1)));
            a_user(i) := v_users(TRUNC(DBMS_RANDOM.VALUE(1, v_users.COUNT + 1)));
            r := DBMS_RANDOM.VALUE(0, 1);
            a_action(i) := CASE WHEN r < 0.80 THEN 'DOWNLOAD' WHEN r < 0.95 THEN 'VIEW_METADATA' ELSE 'UPLOAD' END;
            r := DBMS_RANDOM.VALUE(0, 1);
            a_outcome(i) := CASE WHEN r < 0.95 THEN 'SUCCESS' WHEN r < 0.98 THEN 'FAILURE' ELSE 'DENIED' END;
            a_time(i) := SYSTIMESTAMP - NUMTODSINTERVAL(DBMS_RANDOM.VALUE(0, 365), 'DAY');
            IF a_outcome(i) = 'DENIED' THEN
                a_lat(i)    := NULL;
                a_region(i) := NULL;
            ELSE
                a_lat(i)    := ROUND(15 + ABS(DBMS_RANDOM.NORMAL) * 60, 2);
                a_region(i) := v_regions(TRUNC(DBMS_RANDOM.VALUE(1, v_regions.COUNT + 1)));
            END IF;
        END LOOP;

        FORALL i IN 1 .. v_n
            INSERT INTO access_logs (file_id, user_id, action, outcome, access_time, latency_ms, served_by_region_id)
            VALUES (a_file(i), a_user(i), a_action(i), a_outcome(i), a_time(i), a_lat(i), a_region(i));

        v_done := v_done + v_n;
        COMMIT;
        DBMS_OUTPUT.PUT_LINE('inserted ' || v_done || ' of ' || c_total);
    END LOOP;
END;
/

-- 5. fresh optimizer statistics so plans reflect the new volume
BEGIN
    DBMS_STATS.GATHER_TABLE_STATS(USER, 'USERS',       cascade => TRUE);
    DBMS_STATS.GATHER_TABLE_STATS(USER, 'FILES',       cascade => TRUE);
    DBMS_STATS.GATHER_TABLE_STATS(USER, 'ACCESS_LOGS', cascade => TRUE);
END;
/

PROMPT == Result ==
SELECT (SELECT COUNT(*) FROM users WHERE email LIKE 'synth\_user\_%@synthetic.invalid' ESCAPE '\') AS synthetic_users,
       (SELECT COUNT(*) FROM files WHERE file_name LIKE 'synth\_%' ESCAPE '\')                     AS synthetic_files,
       (SELECT COUNT(*) FROM access_logs)                                                         AS access_logs_total
FROM dual;
SELECT COUNT(*) AS synthetic_duplicate_groups FROM v_duplicate_files WHERE content_sha256 IN
       (SELECT content_sha256 FROM files WHERE file_name LIKE 'synth\_%' ESCAPE '\');
