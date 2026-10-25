-- =====================================================================
-- rollback_migration.sql  STATUS: NOT EXECUTED
-- Undoes migrate_legacy.sql: drops every NEW table/view, renames LEGACY_* back to
-- the original names and recreates the legacy view. Use after a failed or unwanted
-- migration. Legacy indexes and constraints were never dropped, so nothing else is needed.
--   sqlplus cloudmeta@localhost:1521/FREEPDB1 '@rollback_migration.sql'
-- =====================================================================
SET DEFINE ON
SET VERIFY OFF
SET SERVEROUTPUT ON
WHENEVER SQLERROR EXIT SQL.SQLCODE

ACCEPT cm_confirm CHAR PROMPT 'Drops all NEW tables and restores the legacy ones. Type ROLLBACK to continue: '

DECLARE
    v_cnt NUMBER;
BEGIN
    IF '&cm_confirm' <> 'ROLLBACK' THEN
        RAISE_APPLICATION_ERROR(-20990, 'Cancelled: confirmation text was not ROLLBACK');
    END IF;
    IF USER IN ('SYS', 'SYSTEM') THEN
        RAISE_APPLICATION_ERROR(-20999, 'Refusing to run as ' || USER);
    END IF;
    SELECT COUNT(*) INTO v_cnt FROM user_tables WHERE table_name = 'LEGACY_USERS';
    IF v_cnt = 0 THEN
        RAISE_APPLICATION_ERROR(-20102, 'LEGACY_USERS not found: nothing to restore');
    END IF;
    FOR v IN (SELECT view_name FROM user_views) LOOP
        EXECUTE IMMEDIATE 'DROP VIEW "' || v.view_name || '"';
    END LOOP;
    FOR t IN (SELECT table_name FROM user_tables WHERE table_name NOT LIKE 'LEGACY\_%' ESCAPE '\') LOOP
        EXECUTE IMMEDIATE 'DROP TABLE "' || t.table_name || '" CASCADE CONSTRAINTS PURGE';
    END LOOP;
    FOR t IN (SELECT table_name FROM user_tables WHERE table_name LIKE 'LEGACY\_%' ESCAPE '\') LOOP
        EXECUTE IMMEDIATE 'ALTER TABLE "' || t.table_name || '" RENAME TO "' || SUBSTR(t.table_name, 8) || '"';
    END LOOP;
    DBMS_OUTPUT.PUT_LINE('Legacy tables restored.');
END;
/

SET DEFINE OFF
CREATE OR REPLACE VIEW File_Metadata_View AS
SELECT f.FileID, f.FileName, f.FileSize, f.FileType, u.Name AS Owner, c.ProviderName, s.Region, s.StorageClass, f.UploadTime
FROM Files f
JOIN Users u ON f.UserID = u.UserID
JOIN Storage_Locations s ON f.FileID = s.FileID
JOIN Cloud_Providers c ON s.ProviderID = c.ProviderID;

SELECT table_name FROM user_tables ORDER BY table_name;
