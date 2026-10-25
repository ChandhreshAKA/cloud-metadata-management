-- =====================================================================
-- prereq_as_sysdba.sql  STATUS: NOT EXECUTED
-- Run ONCE as SYS, connected to the pluggable DB (not the CDB root):
--   sqlplus sys@localhost:1521/FREEPDB1 as sysdba '@prereq_as_sysdba.sql'
-- Creates the project schema user CLOUDMETA. The password is prompted (hidden)
-- and never stored in any file.
-- =====================================================================
SET DEFINE ON
SET VERIFY OFF
WHENEVER SQLERROR EXIT SQL.SQLCODE

SHOW CON_NAME
ACCEPT cm_pwd CHAR PROMPT 'Choose a password for user CLOUDMETA: ' HIDE

CREATE USER cloudmeta IDENTIFIED BY "&cm_pwd" DEFAULT TABLESPACE users QUOTA UNLIMITED ON users;

GRANT CREATE SESSION, CREATE TABLE, CREATE VIEW, CREATE SEQUENCE,
      CREATE PROCEDURE, CREATE TRIGGER TO cloudmeta;
-- needed for DBMS_XPLAN.DISPLAY_CURSOR (actual execution statistics)
GRANT SELECT_CATALOG_ROLE TO cloudmeta;
GRANT EXECUTE ON DBMS_RANDOM TO cloudmeta;
GRANT EXECUTE ON DBMS_STATS  TO cloudmeta;
GRANT EXECUTE ON DBMS_XPLAN  TO cloudmeta;

UNDEFINE cm_pwd
PROMPT == CLOUDMETA ready. Next: sqlplus cloudmeta@localhost:1521/FREEPDB1 '@install_fresh.sql' ==
