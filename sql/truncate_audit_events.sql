-- truncate_audit_events.sql  STATUS: NOT EXECUTED
-- audit_events is append-only (trigger blocks DELETE). TRUNCATE is DDL and bypasses it.
-- Use ONLY to remove demo rows, e.g. the row left by an earlier demo_constraint_failures run.
--   sqlplus cloudmeta@localhost:1521/FREEPDB1 '@truncate_audit_events.sql'
SET DEFINE OFF
SELECT COUNT(*) AS audit_rows_before FROM audit_events;
TRUNCATE TABLE audit_events;
SELECT COUNT(*) AS audit_rows_after FROM audit_events;
