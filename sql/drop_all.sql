-- =====================================================================
-- drop_all.sql  STATUS: NOT EXECUTED
-- DESTRUCTIVE: drops EVERY table and view in the connected schema (including
-- LEGACY_* tables). Refuses to run as SYS or SYSTEM. Asks for confirmation.
--   sqlplus cloudmeta@localhost:1521/FREEPDB1 '@drop_all.sql'
-- =====================================================================
SET DEFINE ON
SET VERIFY OFF
SET SERVEROUTPUT ON
WHENEVER SQLERROR EXIT SQL.SQLCODE

ACCEPT cm_confirm CHAR PROMPT 'This drops ALL tables and views in this schema. Type DROP to continue: '

DECLARE
    v_dropped PLS_INTEGER := 0;
BEGIN
    IF '&cm_confirm' <> 'DROP' THEN
        RAISE_APPLICATION_ERROR(-20990, 'Cancelled: confirmation text was not DROP');
    END IF;
    IF USER IN ('SYS', 'SYSTEM') THEN
        RAISE_APPLICATION_ERROR(-20999, 'Refusing to run as ' || USER);
    END IF;
    FOR v IN (SELECT view_name FROM user_views) LOOP
        EXECUTE IMMEDIATE 'DROP VIEW "' || v.view_name || '"';
    END LOOP;
    FOR t IN (SELECT table_name FROM user_tables) LOOP
        EXECUTE IMMEDIATE 'DROP TABLE "' || t.table_name || '" CASCADE CONSTRAINTS PURGE';
        v_dropped := v_dropped + 1;
    END LOOP;
    DBMS_OUTPUT.PUT_LINE('Dropped ' || v_dropped || ' tables.');
END;
/
SELECT COUNT(*) AS tables_left FROM user_tables;
