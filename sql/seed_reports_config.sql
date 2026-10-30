-- =====================================================================
-- seed_reports_config.sql (Chunk 6)  OPTIONAL and safe to run repeatedly.
-- Adds the two cold-file settings that complement the existing cold.idle_days (90).
-- Without these rows the reports use the same values as built-in defaults.
-- An admin can change a value with an UPDATE; a request can still override it.
-- =====================================================================
SET DEFINE OFF
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK
PROMPT == seed_reports_config.sql ==

MERGE INTO system_config c
USING (SELECT 'cold.max_accesses' AS k, '3' AS v,
              'A cold file has FEWER than this many successful downloads inside cold.window_days' AS d FROM dual
       UNION ALL
       SELECT 'cold.window_days', '365',
              'Lookback window (days) for counting downloads when deciding if a file is cold' FROM dual) s
   ON (c.config_key = s.k)
 WHEN NOT MATCHED THEN INSERT (config_key, config_value, description) VALUES (s.k, s.v, s.d);

COMMIT;
SELECT config_key, config_value FROM system_config WHERE config_key LIKE 'cold.%' ORDER BY 1;
PROMPT == seed_reports_config.sql finished ==
