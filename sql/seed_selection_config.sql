-- seed_selection_config.sql  (Chunk 3)  STATUS: NOT EXECUTED
-- OPTIONAL. Stores the per-security-level RELIABILITY minimums used by the selection engine's
-- hard constraints, so they can be changed without a code edit. The security minimums
-- (security.min_score.*) were already seeded in Chunk 1. If this script is not run, the code
-- uses the same values as built-in defaults (app/selection/presets.py DEFAULT_POLICY), so the
-- results are identical either way. VALUES ARE ILLUSTRATIVE.
--   cd C:\dbmsproject\sql
--   sqlplus cloudmeta@localhost:1521/FREEPDB1 '@seed_selection_config.sql'
-- Safe to run more than once.
SET DEFINE OFF
SET FEEDBACK ON
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK

MERGE INTO system_config t
USING (SELECT 'selection.min_reliability.LOW' AS config_key, '0' AS config_value,
              'Hard filter: minimum reliability_score for LOW files' AS description FROM dual
       UNION ALL
       SELECT 'selection.min_reliability.MEDIUM', '9.0',
              'Hard filter: minimum reliability_score for MEDIUM files (illustrative)' FROM dual
       UNION ALL
       SELECT 'selection.min_reliability.HIGH', '9.4',
              'Hard filter: minimum reliability_score for HIGH files (illustrative)' FROM dual) s
   ON (t.config_key = s.config_key)
 WHEN NOT MATCHED THEN INSERT (config_key, config_value, description)
      VALUES (s.config_key, s.config_value, s.description);

COMMIT;
SELECT config_key, config_value FROM system_config WHERE config_key LIKE 'selection.min_reliability.%' ORDER BY config_key;
