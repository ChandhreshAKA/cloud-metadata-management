-- =====================================================================
-- migrate_legacy.sql  STATUS: NOT EXECUTED
-- Migrates a schema that holds the ORIGINAL 5 tables (see legacy_schema.sql) to the
-- revised schema, PRESERVING IDs. Legacy tables are kept as LEGACY_* until you
-- are satisfied; undo with rollback_migration.sql.
--   cd C:\dbmsproject\sql
--   sqlplus cloudmeta@localhost:1521/FREEPDB1 @migrate_legacy.sql
-- Mapping decisions (see docs/01-schema-audit.md section 5):
--   Users.Name -> full_name; email lower-cased; no password -> PENDING_RESET
--   Cloud_Providers(name, region, scores) -> cloud_providers + provider_regions
--   Files.AccessFrequency is NOT migrated (derived; stays in LEGACY_FILES)
--   NULL SecurityLevel -> 'HIGH' (most restrictive, owner may lower it)
--   unknown FileType -> 'OTHER'; StorageClass STANDARD/HOT -> tier HOT
--   legacy Access_Logs rows have no outcome -> assumed 'SUCCESS'; Latency = ms
-- =====================================================================
SET ECHO OFF
SET DEFINE OFF
SET SQLBLANKLINES ON
SET LINESIZE 200
SET PAGESIZE 100
SET SERVEROUTPUT ON
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK
SPOOL migrate_legacy.log

PROMPT == 1. Guard: legacy schema present, not yet migrated ==
DECLARE
    v_cnt NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_cnt FROM user_tab_columns WHERE table_name = 'USERS' AND column_name = 'NAME';
    IF v_cnt = 0 THEN
        RAISE_APPLICATION_ERROR(-20100, 'Legacy USERS(NAME) not found: nothing to migrate');
    END IF;
    SELECT COUNT(*) INTO v_cnt FROM user_tables WHERE table_name LIKE 'LEGACY\_%' ESCAPE '\';
    IF v_cnt > 0 THEN
        RAISE_APPLICATION_ERROR(-20101, 'LEGACY_* tables already exist: run rollback_migration.sql first');
    END IF;
END;
/

PROMPT == 2. Drop legacy view, rename legacy tables to LEGACY_* ==
BEGIN
    EXECUTE IMMEDIATE 'DROP VIEW file_metadata_view';
EXCEPTION WHEN OTHERS THEN
    IF SQLCODE <> -942 THEN RAISE; END IF;
END;
/
ALTER TABLE access_logs       RENAME TO legacy_access_logs;
ALTER TABLE storage_locations RENAME TO legacy_storage_locations;
ALTER TABLE files             RENAME TO legacy_files;
ALTER TABLE cloud_providers   RENAME TO legacy_cloud_providers;
ALTER TABLE users             RENAME TO legacy_users;

PROMPT == 3. Create the revised schema and reference data ==
@@schema.sql
@@indexes.sql
@@views.sql
@@seed_data.sql

PROMPT == 4. Copy users (IDs preserved) ==
INSERT INTO users (user_id, full_name, email, role, password_hash, account_status, created_at)
SELECT userid, name, LOWER(TRIM(email)), COALESCE(role, 'USER'), NULL, 'PENDING_RESET',
       COALESCE(createdat, SYSTIMESTAMP)
FROM   legacy_users;

PROMPT == 5. Providers and provider-regions ==
-- providers not already seeded
INSERT INTO cloud_providers (provider_code, provider_name, max_object_bytes)
SELECT UPPER(SUBSTR(REGEXP_REPLACE(p.pname, '[^A-Za-z0-9]', ''), 1, 20)), p.pname, 5497558138880
FROM  (SELECT DISTINCT TRIM(providername) AS pname FROM legacy_cloud_providers) p
WHERE  NOT EXISTS (SELECT 1 FROM cloud_providers c WHERE UPPER(c.provider_name) = UPPER(p.pname));

-- regions named by legacy Cloud_Providers rows and not already seeded
INSERT INTO provider_regions (provider_id, region_code, bucket_name, reliability_score, security_score, egress_usd_per_gb)
SELECT c.provider_id, COALESCE(TRIM(l.region), 'unknown'),
       'cmm-' || LOWER(c.provider_code) || '-' || LOWER(REGEXP_REPLACE(COALESCE(TRIM(l.region), 'unknown'), '[^A-Za-z0-9]', '-')),
       COALESCE(MIN(l.reliabilityscore), 5), COALESCE(MIN(l.securityscore), 5), 0.09
FROM   legacy_cloud_providers l
JOIN   cloud_providers c ON UPPER(c.provider_name) = UPPER(TRIM(l.providername))
WHERE  NOT EXISTS (SELECT 1 FROM provider_regions r
                   WHERE r.provider_id = c.provider_id AND r.region_code = COALESCE(TRIM(l.region), 'unknown'))
GROUP BY c.provider_id, c.provider_code, COALESCE(TRIM(l.region), 'unknown');

-- regions that legacy Storage_Locations use but Cloud_Providers never declared (the audit anomaly)
INSERT INTO provider_regions (provider_id, region_code, bucket_name, reliability_score, security_score, egress_usd_per_gb)
SELECT c.provider_id, COALESCE(TRIM(s.region), 'unknown'),
       COALESCE(MIN(s.bucketname),
                'cmm-' || LOWER(c.provider_code) || '-' || LOWER(REGEXP_REPLACE(COALESCE(TRIM(s.region), 'unknown'), '[^A-Za-z0-9]', '-'))),
       5, 5, 0.09
FROM   legacy_storage_locations s
JOIN   legacy_cloud_providers lp ON lp.providerid = s.providerid
JOIN   cloud_providers c ON UPPER(c.provider_name) = UPPER(TRIM(lp.providername))
WHERE  NOT EXISTS (SELECT 1 FROM provider_regions r
                   WHERE r.provider_id = c.provider_id AND r.region_code = COALESCE(TRIM(s.region), 'unknown'))
GROUP BY c.provider_id, c.provider_code, COALESCE(TRIM(s.region), 'unknown');

PROMPT == 6. Files (AccessFrequency dropped, hash unknown) ==
INSERT INTO files (file_id, owner_id, file_name, file_size_bytes, file_type, security_level, status,
                   content_sha256, upload_time)
SELECT fileid, userid, filename, filesize,
       CASE WHEN UPPER(TRIM(filetype)) IN ('PDF','DOCX','PPTX','XLSX','CSV','TXT','JSON','JPG','PNG','ZIP','MP4')
            THEN UPPER(TRIM(filetype)) ELSE 'OTHER' END,
       COALESCE(securitylevel, 'HIGH'), 'ACTIVE', NULL, COALESCE(uploadtime, SYSTIMESTAMP)
FROM   legacy_files;

PROMPT == 7. Storage locations (first location per file becomes primary) ==
INSERT INTO storage_locations (location_id, file_id, region_id, tier_code, object_key, is_primary, replica_status)
SELECT s.locationid, s.fileid, r.region_id,
       CASE UPPER(TRIM(s.storageclass))
            WHEN 'STANDARD' THEN 'HOT'  WHEN 'HOT' THEN 'HOT'
            WHEN 'COOL' THEN 'COOL'     WHEN 'NEARLINE' THEN 'COOL'     WHEN 'STANDARD_IA' THEN 'COOL'
            WHEN 'COLD' THEN 'COLD'     WHEN 'COLDLINE' THEN 'COLD'     WHEN 'GLACIER_IR' THEN 'COLD'
            WHEN 'ARCHIVE' THEN 'ARCHIVE' WHEN 'DEEP_ARCHIVE' THEN 'ARCHIVE'
            ELSE 'HOT' END,
       COALESCE(s.objectkey, 'legacy/file-' || s.fileid),
       CASE WHEN ROW_NUMBER() OVER (PARTITION BY s.fileid ORDER BY s.locationid) = 1 THEN 'Y' ELSE 'N' END,
       'OK'
FROM  (SELECT l.*, ROW_NUMBER() OVER (PARTITION BY l.fileid, l.providerid, COALESCE(TRIM(l.region), 'unknown')
                                       ORDER BY l.locationid) AS dup_rn
       FROM   legacy_storage_locations l) s
JOIN   legacy_cloud_providers lp ON lp.providerid = s.providerid
JOIN   cloud_providers p ON UPPER(p.provider_name) = UPPER(TRIM(lp.providername))
JOIN   provider_regions r ON r.provider_id = p.provider_id AND r.region_code = COALESCE(TRIM(s.region), 'unknown')
WHERE  s.dup_rn = 1;

PROMPT == 8. Access logs (outcome assumed SUCCESS) ==
INSERT INTO access_logs (log_id, file_id, user_id, action, outcome, access_time, latency_ms)
SELECT logid, fileid, userid, UPPER(TRIM(action)), 'SUCCESS', COALESCE(accesstime, SYSTIMESTAMP), latency
FROM   legacy_access_logs;

PROMPT == 9. Restart identity counters above the migrated IDs ==
ALTER TABLE users             MODIFY (user_id     GENERATED BY DEFAULT AS IDENTITY (START WITH LIMIT VALUE));
ALTER TABLE files             MODIFY (file_id     GENERATED BY DEFAULT AS IDENTITY (START WITH LIMIT VALUE));
ALTER TABLE storage_locations MODIFY (location_id GENERATED BY DEFAULT AS IDENTITY (START WITH LIMIT VALUE));
ALTER TABLE access_logs       MODIFY (log_id      GENERATED BY DEFAULT AS IDENTITY (START WITH LIMIT VALUE));

COMMIT;

PROMPT == 10. Reconciliation: row counts (legacy vs new) ==
SELECT 'users' AS entity, (SELECT COUNT(*) FROM legacy_users) AS legacy_rows, (SELECT COUNT(*) FROM users) AS new_rows FROM dual
UNION ALL SELECT 'files',             (SELECT COUNT(*) FROM legacy_files),             (SELECT COUNT(*) FROM files) FROM dual
UNION ALL SELECT 'storage_locations', (SELECT COUNT(*) FROM legacy_storage_locations), (SELECT COUNT(*) FROM storage_locations) FROM dual
UNION ALL SELECT 'access_logs',       (SELECT COUNT(*) FROM legacy_access_logs),       (SELECT COUNT(*) FROM access_logs) FROM dual
UNION ALL SELECT 'legacy provider rows found as provider-region',
       (SELECT COUNT(*) FROM legacy_cloud_providers),
       (SELECT COUNT(*) FROM legacy_cloud_providers l
        JOIN cloud_providers c ON UPPER(c.provider_name) = UPPER(TRIM(l.providername))
        JOIN provider_regions r ON r.provider_id = c.provider_id AND r.region_code = COALESCE(TRIM(l.region), 'unknown')) FROM dual;

PROMPT == 11. Legacy AccessFrequency counter vs what the log actually says (update-anomaly evidence) ==
SELECT lf.fileid, lf.filename, lf.accessfrequency AS legacy_counter,
       (SELECT COUNT(*) FROM legacy_access_logs a WHERE a.fileid = lf.fileid AND UPPER(a.action) = 'DOWNLOAD') AS downloads_in_log
FROM   legacy_files lf ORDER BY lf.fileid;

PROMPT == 12. Legacy bucket names that differ from the region's registered bucket (expect: no rows selected) ==
SELECT s.locationid, s.bucketname AS legacy_bucket, r.bucket_name AS registered_bucket
FROM   legacy_storage_locations s
JOIN   legacy_cloud_providers lp ON lp.providerid = s.providerid
JOIN   cloud_providers p ON UPPER(p.provider_name) = UPPER(TRIM(lp.providername))
JOIN   provider_regions r ON r.provider_id = p.provider_id AND r.region_code = COALESCE(TRIM(s.region), 'unknown')
WHERE  s.bucketname IS NOT NULL AND s.bucketname <> r.bucket_name;

PROMPT == 13. Migrated rows needing attention: NULL legacy SecurityLevel, unpriced locations (expect: 0 and no rows) ==
SELECT COUNT(*) AS files_with_null_legacy_security FROM legacy_files WHERE securitylevel IS NULL;
SELECT * FROM v_unpriced_locations;

PROMPT == 14. Provider-region pairs created from legacy data but absent from the seed (expect: no rows selected) ==
SELECT p.provider_name, r.region_code, r.bucket_name
FROM   provider_regions r JOIN cloud_providers p ON p.provider_id = r.provider_id
WHERE  r.is_illustrative = 'Y' AND r.reliability_score = 5 AND r.security_score = 5;

PROMPT == Migration finished. LEGACY_* tables kept. Run verify_schema.sql next. ==
SPOOL OFF
