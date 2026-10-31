-- =====================================================================
-- prereq_autonomous.sql
-- Run ONCE as ADMIN on an Oracle Autonomous Database (Always Free).
-- It is the cloud twin of prereq_as_sysdba.sql: the default tablespace on Autonomous is DATA,
-- and the demo-only grants (DBMS_RANDOM, DBMS_XPLAN, ...) are left out.
--   sql /nolog
--   set cloudconfig C:\path\Wallet_yourdb.zip
--   connect admin@yourdb_low
--   @prereq_autonomous.sql
-- The password is prompted (hidden) and never stored in any file.
-- =====================================================================
SET DEFINE ON
SET VERIFY OFF
WHENEVER SQLERROR EXIT SQL.SQLCODE

ACCEPT cm_pwd CHAR PROMPT 'Choose a password for user CLOUDMETA: ' HIDE

CREATE USER cloudmeta IDENTIFIED BY "&cm_pwd" DEFAULT TABLESPACE data QUOTA UNLIMITED ON data;

GRANT CREATE SESSION, CREATE TABLE, CREATE VIEW, CREATE SEQUENCE,
      CREATE PROCEDURE, CREATE TRIGGER TO cloudmeta;

UNDEFINE cm_pwd
PROMPT == CLOUDMETA ready. Next: connect cloudmeta@yourdb_low, then @install_fresh.sql ==
