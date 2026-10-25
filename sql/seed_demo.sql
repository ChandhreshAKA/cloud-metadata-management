-- =====================================================================
-- seed_demo.sql (Chunk 1)  DEMO DATA, FRESH INSTALL ONLY  STATUS: NOT EXECUTED
-- Not used by the migration (the migration brings the legacy rows instead).
-- Content hashes are SHA-256 of placeholder strings, NOT of real file bytes.
-- notes.docx and notes_copy.docx share a hash on purpose (duplicate demo);
-- old_backup.zip has no download in 400 days on purpose (cold-file demo).
-- Users have no password yet (PENDING_RESET); chunk 2 sets real hashes.
-- =====================================================================
SET DEFINE OFF
SET SQLBLANKLINES ON
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK
PROMPT == seed_demo.sql ==

INSERT INTO users (full_name, email, role) VALUES ('Harini',  'harini@example.com',  'USER');
INSERT INTO users (full_name, email, role) VALUES ('Admin',   'admin@example.com',   'ADMIN');
INSERT INTO users (full_name, email, role) VALUES ('Auditor', 'auditor@example.com', 'AUDITOR');

INSERT INTO files (owner_id, file_name, file_size_bytes, file_type, security_level, content_sha256, desired_replicas, expected_accesses_per_month, upload_time)
SELECT u.user_id, v.fname, v.fsize, v.ftype, v.slevel,
       LOWER(RAWTOHEX(STANDARD_HASH(v.hash_seed, 'SHA256'))), v.repl, v.expected,
       SYSTIMESTAMP - NUMTODSINTERVAL(v.age_days, 'DAY')
FROM   users u
JOIN  (SELECT 'project_report.pdf' fname, 5242880 fsize, 'PDF' ftype, 'HIGH' slevel, 'demo-content-1' hash_seed, 2 repl, 10 expected, 30 age_days FROM dual
       UNION ALL SELECT 'presentation.pptx', 8388608,   'PPTX', 'MEDIUM', 'demo-content-2',     1, 5, 20  FROM dual
       UNION ALL SELECT 'dataset.csv',       104857600, 'CSV',  'LOW',    'demo-content-3',     2, 2, 10  FROM dual
       UNION ALL SELECT 'notes.docx',        2097152,   'DOCX', 'LOW',    'demo-content-notes', 1, 1, 3   FROM dual
       UNION ALL SELECT 'notes_copy.docx',   2097152,   'DOCX', 'LOW',    'demo-content-notes', 1, 1, 3   FROM dual
       UNION ALL SELECT 'old_backup.zip',    524288000, 'ZIP',  'LOW',    'demo-content-6',     1, 0, 400 FROM dual) v
       ON 1 = 1
WHERE  u.email = 'harini@example.com';

INSERT INTO storage_locations (file_id, region_id, tier_code, object_key, is_primary, replica_status, last_verified_at)
SELECT f.file_id, r.region_id, 'HOT', v.object_key, v.is_primary, 'OK', SYSTIMESTAMP
FROM  (SELECT 'project_report.pdf' fname, 'AWS' pc, 'ap-south-1' rc, 'reports/project_report.pdf' object_key, 'Y' is_primary FROM dual
       UNION ALL SELECT 'project_report.pdf', 'AZURE', 'Central India', 'replicas/project_report.pdf', 'N' FROM dual
       UNION ALL SELECT 'presentation.pptx',  'AZURE', 'Central India', 'ppt/presentation.pptx',       'Y' FROM dual
       UNION ALL SELECT 'dataset.csv',        'GCP',   'asia-south1',   'datasets/dataset.csv',        'Y' FROM dual
       UNION ALL SELECT 'notes.docx',         'AWS',   'ap-south-1',    'notes/notes.docx',            'Y' FROM dual
       UNION ALL SELECT 'notes_copy.docx',    'AWS',   'ap-south-1',    'notes/notes_copy.docx',       'Y' FROM dual
       UNION ALL SELECT 'old_backup.zip',     'GCP',   'asia-south1',   'backups/old_backup.zip',      'Y' FROM dual) v
JOIN   files f             ON f.file_name = v.fname
JOIN   cloud_providers p   ON p.provider_code = v.pc
JOIN   provider_regions r  ON r.provider_id = p.provider_id AND r.region_code = v.rc;

INSERT INTO access_logs (file_id, user_id, action, outcome, access_time, latency_ms, served_by_region_id)
SELECT f.file_id, u.user_id, v.action, v.outcome,
       SYSTIMESTAMP - NUMTODSINTERVAL(v.days_ago, 'DAY'), v.lat, r.region_id
FROM  (SELECT 'project_report.pdf' fname, 'harini@example.com' email, 'UPLOAD' action, 'SUCCESS' outcome, 30 days_ago, 120.50 lat, 'AWS' pc, 'ap-south-1' rc FROM dual
       UNION ALL SELECT 'project_report.pdf', 'harini@example.com',  'DOWNLOAD', 'SUCCESS', 5,   85.20,  'AWS',   'ap-south-1'    FROM dual
       UNION ALL SELECT 'project_report.pdf', 'harini@example.com',  'DOWNLOAD', 'SUCCESS', 2,   80.10,  'AWS',   'ap-south-1'    FROM dual
       UNION ALL SELECT 'project_report.pdf', 'auditor@example.com', 'DOWNLOAD', 'DENIED',  1,   NULL,   NULL,    NULL            FROM dual
       UNION ALL SELECT 'presentation.pptx',  'harini@example.com',  'UPLOAD',   'SUCCESS', 20,  150.00, 'AZURE', 'Central India' FROM dual
       UNION ALL SELECT 'presentation.pptx',  'harini@example.com',  'DOWNLOAD', 'SUCCESS', 1,   72.40,  'AZURE', 'Central India' FROM dual
       UNION ALL SELECT 'dataset.csv',        'harini@example.com',  'UPLOAD',   'SUCCESS', 10,  900.00, 'GCP',   'asia-south1'   FROM dual
       UNION ALL SELECT 'notes.docx',         'harini@example.com',  'UPLOAD',   'SUCCESS', 3,   60.00,  'AWS',   'ap-south-1'    FROM dual
       UNION ALL SELECT 'notes_copy.docx',    'harini@example.com',  'UPLOAD',   'SUCCESS', 3,   62.00,  'AWS',   'ap-south-1'    FROM dual
       UNION ALL SELECT 'old_backup.zip',     'harini@example.com',  'UPLOAD',   'SUCCESS', 400, 2000.00,'GCP',   'asia-south1'   FROM dual) v
JOIN   files f ON f.file_name = v.fname
JOIN   users u ON u.email = v.email
LEFT JOIN cloud_providers p  ON p.provider_code = v.pc
LEFT JOIN provider_regions r ON r.provider_id = p.provider_id AND r.region_code = v.rc;

COMMIT;
PROMPT == seed_demo.sql finished ==
