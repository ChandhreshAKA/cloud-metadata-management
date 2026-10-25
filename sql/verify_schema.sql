-- =====================================================================
-- verify_schema.sql  STATUS: NOT EXECUTED
-- Read-only sanity report. Paste its output back for review.
--   sqlplus cloudmeta@localhost:1521/FREEPDB1 '@verify_schema.sql'
-- =====================================================================
SET LINESIZE 200
SET PAGESIZE 200
SET FEEDBACK ON
SET DEFINE OFF
COLUMN object_type FORMAT A15
COLUMN object_name FORMAT A32
COLUMN table_name  FORMAT A28
COLUMN constraint_name FORMAT A30
COLUMN delete_rule FORMAT A12
COLUMN r_table FORMAT A24
COLUMN t FORMAT A28

PROMPT == 1. Objects by type (VALID/INVALID) ==
SELECT object_type, status, COUNT(*) AS n
FROM   user_objects
WHERE  object_type IN ('TABLE','VIEW','TRIGGER','INDEX','SEQUENCE')
GROUP BY object_type, status
ORDER BY object_type, status;

PROMPT == 2. INVALID objects (expect: no rows selected) ==
SELECT object_type, object_name FROM user_objects WHERE status <> 'VALID';

PROMPT == 3. Compilation errors (expect: no rows selected) ==
SELECT name, type, line, text FROM user_errors ORDER BY name, sequence;

PROMPT == 4. Row counts ==
SELECT 'STORAGE_TIERS' AS t, COUNT(*) AS n FROM storage_tiers
UNION ALL SELECT 'CLOUD_PROVIDERS', COUNT(*) FROM cloud_providers
UNION ALL SELECT 'PROVIDER_REGIONS', COUNT(*) FROM provider_regions
UNION ALL SELECT 'PROVIDER_PRICING', COUNT(*) FROM provider_pricing
UNION ALL SELECT 'WEIGHT_PROFILES', COUNT(*) FROM weight_profiles
UNION ALL SELECT 'SYSTEM_CONFIG', COUNT(*) FROM system_config
UNION ALL SELECT 'PERFORMANCE_MEASUREMENTS', COUNT(*) FROM performance_measurements
UNION ALL SELECT 'USERS', COUNT(*) FROM users
UNION ALL SELECT 'FILES', COUNT(*) FROM files
UNION ALL SELECT 'FILE_ENCRYPTION', COUNT(*) FROM file_encryption
UNION ALL SELECT 'STORAGE_LOCATIONS', COUNT(*) FROM storage_locations
UNION ALL SELECT 'ACCESS_LOGS', COUNT(*) FROM access_logs
UNION ALL SELECT 'REPLICATION_JOBS', COUNT(*) FROM replication_jobs
UNION ALL SELECT 'SELECTION_DECISIONS', COUNT(*) FROM selection_decisions
UNION ALL SELECT 'SELECTION_CANDIDATES', COUNT(*) FROM selection_candidates
UNION ALL SELECT 'RECOMMENDATIONS', COUNT(*) FROM recommendations
UNION ALL SELECT 'AUDIT_EVENTS', COUNT(*) FROM audit_events;

PROMPT == 5. Foreign keys and their ON DELETE rule ==
SELECT c.table_name, c.constraint_name, p.table_name AS r_table, c.delete_rule
FROM   user_constraints c
JOIN   user_constraints p ON p.constraint_name = c.r_constraint_name
WHERE  c.constraint_type = 'R'
ORDER BY c.table_name, c.constraint_name;

PROMPT == 6. Constraint counts by type (P=primary U=unique R=foreign C=check) ==
SELECT constraint_type, COUNT(*) AS n FROM user_constraints
WHERE  constraint_name NOT LIKE 'SYS\_C%' ESCAPE '\' OR constraint_type <> 'C'
GROUP BY constraint_type ORDER BY constraint_type;

PROMPT == 7. Indexes (excluding those backing PK/UNIQUE constraints) ==
SELECT table_name, index_name, uniqueness FROM user_indexes
WHERE  index_name NOT IN (SELECT index_name FROM user_constraints WHERE index_name IS NOT NULL)
ORDER BY table_name, index_name;

PROMPT == 8. Integrity views (expect: no rows selected) ==
SELECT * FROM v_unpriced_locations;

PROMPT == 9. Weight profiles sum to 1 ==
SELECT profile_name, w_storage_cost + w_transfer_cost + w_latency + w_reliability + w_security AS total
FROM weight_profiles ORDER BY profile_name;

PROMPT == 10. Sample views ==
SELECT * FROM v_file_metadata ORDER BY file_id;
SELECT * FROM v_file_replication_status ORDER BY file_id;
SELECT * FROM v_duplicate_files;
SELECT file_id, file_name, downloads_total, downloads_30d FROM v_file_access_stats ORDER BY file_id;
