-- =====================================================================
-- demo_constraint_failures.sql  STATUS: NOT EXECUTED
-- Deliberately failing statements, one per constraint. EVERY statement below is
-- EXPECTED to fail; the expected Oracle error is printed before it. Nothing is
-- committed (ROLLBACK at the end). Requires install_fresh.sql (or the migration) first.
--   cd C:\dbmsproject\sql
--   sqlplus cloudmeta@localhost:1521/FREEPDB1 @demo_constraint_failures.sql
-- The constraint name inside the error message is what to compare.
-- =====================================================================
SET ECHO ON
SET DEFINE OFF
SET LINESIZE 200
SET FEEDBACK ON
WHENEVER SQLERROR CONTINUE
SPOOL demo_constraint_failures.log

PROMPT ---- PRIMARY KEY / UNIQUE (ORA-00001) ----

PROMPT F01 expect ORA-00001 PK_USERS (user_id 1 already exists)
INSERT INTO users (user_id, full_name, email, role) VALUES (1, 'Dup Id', 'dup1@example.com', 'USER');

PROMPT F02 expect ORA-00001 UQ_USERS_EMAIL
INSERT INTO users (full_name, email, role) VALUES ('Dup Mail', 'harini@example.com', 'USER');

PROMPT F03 expect ORA-00001 UQ_LOCATIONS_FILE_REGION (same file twice in the same provider-region)
INSERT INTO storage_locations (file_id, region_id, tier_code, object_key, is_primary, replica_status)
SELECT f.file_id, r.region_id, 'HOT', 'other/key.pdf', 'N', 'OK'
FROM files f, provider_regions r JOIN cloud_providers p ON p.provider_id = r.provider_id
WHERE f.file_name = 'project_report.pdf' AND p.provider_code = 'AWS' AND r.region_code = 'ap-south-1';

PROMPT F04 expect ORA-00001 UX_LOCATIONS_ONE_PRIMARY (second primary copy for one file)
INSERT INTO storage_locations (file_id, region_id, tier_code, object_key, is_primary, replica_status)
SELECT f.file_id, r.region_id, 'HOT', 'second/primary.pdf', 'Y', 'OK'
FROM files f, provider_regions r JOIN cloud_providers p ON p.provider_id = r.provider_id
WHERE f.file_name = 'project_report.pdf' AND p.provider_code = 'AWS' AND r.region_code = 'us-east-1';

PROMPT F05 expect ORA-00001 UQ_LOCATIONS_REGION_KEY (same object key in same provider-region, different file)
INSERT INTO storage_locations (file_id, region_id, tier_code, object_key, is_primary, replica_status)
SELECT f.file_id, r.region_id, 'HOT', 'reports/project_report.pdf', 'N', 'OK'
FROM files f, provider_regions r JOIN cloud_providers p ON p.provider_id = r.provider_id
WHERE f.file_name = 'presentation.pptx' AND p.provider_code = 'AWS' AND r.region_code = 'ap-south-1';

PROMPT F06 expect ORA-00001 UX_WEIGHTS_ONE_DEFAULT (second default weight profile)
INSERT INTO weight_profiles (profile_name, description, w_storage_cost, w_transfer_cost, w_latency, w_reliability, w_security, is_default)
VALUES ('SECOND_DEFAULT', 'x', 0.2, 0.2, 0.2, 0.2, 0.2, 'Y');

PROMPT ---- NOT NULL (ORA-01400) ----

PROMPT F07 expect ORA-01400 cannot insert NULL into USERS.EMAIL
INSERT INTO users (full_name, role) VALUES ('No Email', 'USER');

PROMPT F08 expect ORA-01400 cannot insert NULL into FILES.SECURITY_LEVEL
INSERT INTO files (owner_id, file_name, file_size_bytes, file_type)
VALUES ((SELECT user_id FROM users WHERE email = 'harini@example.com'), 'f08.txt', 10, 'TXT');

PROMPT F09 expect ORA-01400 cannot insert NULL into PROVIDER_REGIONS.SECURITY_SCORE
INSERT INTO provider_regions (provider_id, region_code, bucket_name, reliability_score, egress_usd_per_gb)
VALUES ((SELECT provider_id FROM cloud_providers WHERE provider_code = 'AWS'), 'f09', 'f09-bucket', 5, 0.09);

PROMPT ---- CHECK (ORA-02290) ----

PROMPT F10 expect ORA-02290 CK_USERS_EMAIL_LOWER
INSERT INTO users (full_name, email, role) VALUES ('Upper Mail', 'HARINI@EXAMPLE.COM', 'USER');

PROMPT F11 expect ORA-02290 CK_USERS_ROLE
INSERT INTO users (full_name, email, role) VALUES ('Boss', 'boss@example.com', 'SUPERUSER');

PROMPT F12 expect ORA-02290 CK_USERS_PWD (ACTIVE account without a password hash)
INSERT INTO users (full_name, email, role, account_status) VALUES ('No Pwd', 'nopwd@example.com', 'USER', 'ACTIVE');

PROMPT F13 expect ORA-02290 CK_FILES_SIZE (negative size)
INSERT INTO files (owner_id, file_name, file_size_bytes, file_type, security_level)
VALUES ((SELECT user_id FROM users WHERE email = 'harini@example.com'), 'f13.txt', -1, 'TXT', 'LOW');

PROMPT F14 expect ORA-02290 CK_FILES_TYPE (file type outside the allowed list)
INSERT INTO files (owner_id, file_name, file_size_bytes, file_type, security_level)
VALUES ((SELECT user_id FROM users WHERE email = 'harini@example.com'), 'f14.exe', 10, 'EXE', 'LOW');

PROMPT F15 expect ORA-02290 CK_FILES_SECURITY (invalid security level)
INSERT INTO files (owner_id, file_name, file_size_bytes, file_type, security_level)
VALUES ((SELECT user_id FROM users WHERE email = 'harini@example.com'), 'f15.txt', 10, 'TXT', 'ULTRA');

PROMPT F16 expect ORA-02290 CK_FILES_SHA256 (not 64 lowercase hex characters)
INSERT INTO files (owner_id, file_name, file_size_bytes, file_type, security_level, content_sha256)
VALUES ((SELECT user_id FROM users WHERE email = 'harini@example.com'), 'f16.txt', 10, 'TXT', 'LOW', 'xyz');

PROMPT F17 expect ORA-02290 CK_FILES_DELETED (DELETED status without deleted_at)
INSERT INTO files (owner_id, file_name, file_size_bytes, file_type, security_level, status)
VALUES ((SELECT user_id FROM users WHERE email = 'harini@example.com'), 'f17.txt', 10, 'TXT', 'LOW', 'DELETED');

PROMPT F18 expect ORA-02290 CK_FILES_NAME (path separator in file name)
INSERT INTO files (owner_id, file_name, file_size_bytes, file_type, security_level)
VALUES ((SELECT user_id FROM users WHERE email = 'harini@example.com'), '../escape.txt', 10, 'TXT', 'LOW');

PROMPT F19 expect ORA-02290 CK_LOGS_ACTION
INSERT INTO access_logs (file_id, user_id, action)
VALUES ((SELECT file_id FROM files WHERE file_name = 'project_report.pdf'),
        (SELECT user_id FROM users WHERE email = 'harini@example.com'), 'HACK');

PROMPT F20 expect ORA-02290 CK_LOGS_OUTCOME
INSERT INTO access_logs (file_id, user_id, action, outcome)
VALUES ((SELECT file_id FROM files WHERE file_name = 'project_report.pdf'),
        (SELECT user_id FROM users WHERE email = 'harini@example.com'), 'DOWNLOAD', 'MAYBE');

PROMPT F21 expect ORA-02290 CK_LOGS_LATENCY (negative latency)
INSERT INTO access_logs (file_id, user_id, action, latency_ms)
VALUES ((SELECT file_id FROM files WHERE file_name = 'project_report.pdf'),
        (SELECT user_id FROM users WHERE email = 'harini@example.com'), 'DOWNLOAD', -5);

PROMPT F22 expect ORA-02290 CK_REGIONS_RELIABILITY (score above 10)
INSERT INTO provider_regions (provider_id, region_code, bucket_name, reliability_score, security_score, egress_usd_per_gb)
VALUES ((SELECT provider_id FROM cloud_providers WHERE provider_code = 'AWS'), 'f22', 'f22-bucket', 11, 5, 0.09);

PROMPT F23 expect ORA-02290 CK_WEIGHTS_SUM (weights sum to 1.5, not 1)
INSERT INTO weight_profiles (profile_name, description, w_storage_cost, w_transfer_cost, w_latency, w_reliability, w_security)
VALUES ('BAD_SUM', 'x', 0.5, 0.5, 0.5, 0, 0);

PROMPT F24 expect ORA-02290 CK_DECISIONS_WEIGHTS_JSON (not valid JSON)
INSERT INTO selection_decisions (file_id, profile_name, weights_json, explanation)
VALUES ((SELECT file_id FROM files WHERE file_name = 'project_report.pdf'), 'BALANCED', 'not json', 'x');

PROMPT F25 expect ORA-02290 CK_REC_SHAPE (DEDUPLICATE needs related_file_id)
INSERT INTO recommendations (rec_type, file_id, est_monthly_saving_usd, evidence_json)
VALUES ('DEDUPLICATE', (SELECT file_id FROM files WHERE file_name = 'project_report.pdf'), 0.01, '{}');

PROMPT F26 expect ORA-02290 CK_PERF_OK_HAS_LATENCY (successful probe must have a latency)
INSERT INTO performance_measurements (region_id, operation, success, source)
VALUES ((SELECT MIN(region_id) FROM provider_regions), 'GET', 'Y', 'SIMULATED');

PROMPT F27 expect ORA-02290 CK_AUDIT_TYPE (unknown event type)
INSERT INTO audit_events (event_type, outcome) VALUES ('SOMETHING_ELSE', 'SUCCESS');

PROMPT ---- FOREIGN KEY (ORA-02291 child without parent, ORA-02292 parent with children) ----

PROMPT F28 expect ORA-02291 FK_FILES_OWNER (parent key not found)
INSERT INTO files (owner_id, file_name, file_size_bytes, file_type, security_level)
VALUES (999999, 'f28.txt', 10, 'TXT', 'LOW');

PROMPT F29 expect ORA-02291 FK_LOCATIONS_TIER (unknown storage tier)
INSERT INTO storage_locations (file_id, region_id, tier_code, object_key, is_primary, replica_status)
SELECT f.file_id, r.region_id, 'FREEZER', 'x/y', 'N', 'OK'
FROM files f, provider_regions r JOIN cloud_providers p ON p.provider_id = r.provider_id
WHERE f.file_name = 'project_report.pdf' AND p.provider_code = 'GCP' AND r.region_code = 'us-central1';

PROMPT F30 expect ORA-02292 child record found (one of FK_FILES_OWNER, FK_LOGS_USER, ...) - users are never hard-deleted
DELETE FROM users WHERE email = 'harini@example.com';

PROMPT F31 expect ORA-02292 FK_LOGS_FILE - a file with access history cannot be hard-deleted
DELETE FROM files WHERE file_name = 'project_report.pdf';

PROMPT F32 expect ORA-02292 child record found (FK_REGIONS_PROVIDER) - a provider with regions cannot be deleted
DELETE FROM cloud_providers WHERE provider_code = 'AWS';

PROMPT ---- DATA TYPE LIMITS ----

PROMPT F33 expect ORA-12899 value too large for column FILES.FILE_NAME
INSERT INTO files (owner_id, file_name, file_size_bytes, file_type, security_level)
VALUES ((SELECT user_id FROM users WHERE email = 'harini@example.com'), RPAD('a', 256, 'a'), 10, 'TXT', 'LOW');

PROMPT F34 expect ORA-01438 value larger than specified precision (NUMBER(4,2) holds up to 99.99)
INSERT INTO provider_regions (provider_id, region_code, bucket_name, reliability_score, security_score, egress_usd_per_gb)
VALUES ((SELECT provider_id FROM cloud_providers WHERE provider_code = 'AWS'), 'f34', 'f34-bucket', 100.5, 5, 0.09);

PROMPT ---- TRIGGER: audit_events is append-only (ORA-20001) ----

PROMPT F35 setup (this insert SUCCEEDS; it is rolled back at the end)
INSERT INTO audit_events (event_type, outcome, details_json) VALUES ('CONFIG_CHANGED', 'SUCCESS', '{"demo":true}');

PROMPT F36 expect ORA-20001 audit_events is append-only (UPDATE)
UPDATE audit_events SET outcome = 'FAILURE';

PROMPT F37 expect ORA-20001 audit_events is append-only (DELETE)
DELETE FROM audit_events;

PROMPT ---- Cleanup: nothing should have been persisted ----
ROLLBACK;
SELECT (SELECT COUNT(*) FROM users)        AS users_rows,
       (SELECT COUNT(*) FROM files)        AS files_rows,
       (SELECT COUNT(*) FROM audit_events) AS audit_rows
FROM dual;

SPOOL OFF
