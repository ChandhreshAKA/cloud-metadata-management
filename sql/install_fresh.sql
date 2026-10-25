-- =====================================================================
-- install_fresh.sql  STATUS: NOT EXECUTED
-- Fresh install on an EMPTY schema:
--   cd C:\dbmsproject\sql
--   sqlplus cloudmeta@localhost:1521/FREEPDB1 '@install_fresh.sql'
-- (If the schema is not empty, run drop_all.sql first.)
-- =====================================================================
SET ECHO OFF
SET LINESIZE 200
SET PAGESIZE 100
SET FEEDBACK ON
SET SERVEROUTPUT ON
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK
SPOOL install_fresh.log

@@schema.sql
@@indexes.sql
@@views.sql
@@seed_data.sql
@@seed_demo.sql
@@verify_schema.sql

SPOOL OFF
