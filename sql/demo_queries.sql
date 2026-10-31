-- =====================================================================
-- demo_queries.sql (Chunk 7)  READ-ONLY queries to run LIVE during the review.
-- Each block matches a section of  scripts/demo.py  (Q1 ... Q10). Run one block after the
-- matching step to show the database changing. Run the demo with --pause and --keep to have
-- time between sections and to keep the files for these queries.
--
--   sqlplus cloudmeta@localhost:1521/FREEPDB1
--   SQL> @sql/demo_queries.sql          (runs everything for the latest demo file)
-- or paste one block at a time. Nothing here changes data.
-- All prices and scores are ILLUSTRATIVE; storage is SIMULATED (local folders).
-- =====================================================================
SET DEFINE ON
SET VERIFY OFF
SET LINESIZE 200
SET PAGESIZE 60
COLUMN file_name FORMAT A32
COLUMN details_json FORMAT A60
COLUMN event_type FORMAT A22
COLUMN object_key FORMAT A44
COLUMN explanation FORMAT A70 WORD_WRAPPED
COLUMN provider_code FORMAT A8
COLUMN region_code FORMAT A14

-- the latest ORIGINAL file the demo user uploaded (the demo also uploads a "_copy" to make a duplicate)
COLUMN fid NEW_VALUE fid NOPRINT
SELECT MAX(file_id) AS fid FROM files
 WHERE owner_id = (SELECT user_id FROM users WHERE email = 'demo_user@example.test')
   AND file_name NOT LIKE '%\_copy.pdf' ESCAPE '\';
PROMPT latest demo file_id = &fid

PROMPT == Q1  (section 1) database is reachable and has the 17 tables ==
SELECT COUNT(*) AS tables_in_schema FROM user_tables;

PROMPT == Q2  (section 2) accounts: the password is a hash, never plain text, and each has a role ==
SELECT user_id, email, role, account_status, SUBSTR(password_hash, 1, 20) || '...' AS hash_start
  FROM users WHERE email LIKE 'demo\_%@example.test' ESCAPE '\';

PROMPT == Q3  (section 3) the selection engine's candidates for the demo file, after upload ==
-- (the dry run writes nothing; the SAVED decision appears after the upload, see Q4/Q5)
SELECT p.provider_code, r.region_code, r.reliability_score, r.security_score, r.health_status
  FROM provider_regions r JOIN cloud_providers p ON p.provider_id = r.provider_id
 ORDER BY p.provider_code, r.region_code;

PROMPT == Q4  (section 4) the new file row and where it was placed ==
SELECT f.file_id, f.file_name, f.file_size_bytes, f.security_level, f.status,
       p.provider_code, r.region_code, sl.tier_code, sl.replica_status
  FROM files f
  JOIN storage_locations sl ON sl.file_id = f.file_id AND sl.is_primary = 'Y'
  JOIN provider_regions r ON r.region_id = sl.region_id
  JOIN cloud_providers p ON p.provider_id = r.provider_id
 WHERE f.file_id = &fid;

PROMPT == Q5  (section 5) encryption record, saved decision, every candidate with its score or reason ==
SELECT algorithm, LENGTH(wrapped_dek) AS wrapped_dek_bytes, LENGTH(nonce) AS nonce_bytes,
       master_key_version, SUBSTR(ciphertext_sha256, 1, 16) AS ciphertext_sha256_start
  FROM file_encryption WHERE file_id = &fid;
SELECT sl.object_key FROM storage_locations sl WHERE sl.file_id = &fid;
SELECT d.profile_name, d.explanation FROM selection_decisions d WHERE d.file_id = &fid;
SELECT p.provider_code, r.region_code, c.total_score, c.excluded_reason
  FROM selection_decisions d
  JOIN selection_candidates c ON c.decision_id = d.decision_id
  JOIN provider_regions r ON r.region_id = c.region_id
  JOIN cloud_providers p ON p.provider_id = r.provider_id
 WHERE d.file_id = &fid
 ORDER BY c.total_score DESC NULLS LAST;

PROMPT == Q6  (section 6) hashes: the stored (encrypted) hash and the plaintext hash are different ==
SELECT SUBSTR(f.content_sha256, 1, 16) AS plaintext_sha256_start,
       SUBSTR(e.ciphertext_sha256, 1, 16) AS ciphertext_sha256_start
  FROM files f JOIN file_encryption e ON e.file_id = f.file_id WHERE f.file_id = &fid;

PROMPT == Q7  (section 7) the access log for the demo file ==
SELECT a.log_id, a.action, a.outcome, a.latency_ms, r.region_code AS served_by,
       TO_CHAR(a.access_time, 'HH24:MI:SS') AS at
  FROM access_logs a LEFT JOIN provider_regions r ON r.region_id = a.served_by_region_id
 WHERE a.file_id = &fid ORDER BY a.log_id;

PROMPT == Q8  (section 8) duplicates (same owner, same hash) and the cold-file rule ==
SELECT SUBSTR(content_sha256, 1, 12) || '...' AS hash_start, COUNT(*) AS copies,
       SUM(file_size_bytes) - MAX(file_size_bytes) AS wasted_bytes
  FROM files
 WHERE status <> 'DELETED' AND content_sha256 IS NOT NULL
   AND owner_id = (SELECT user_id FROM users WHERE email = 'demo_user@example.test')
 GROUP BY content_sha256 HAVING COUNT(*) > 1;
-- the full reports with money and thresholds:  @sql/reports.sql

PROMPT == Q9  (section 9) the denied attempts are in the audit trail (append-only) ==
SELECT e.event_id, e.event_type, e.outcome, u.email AS actor, e.details_json
  FROM audit_events e JOIN users u ON u.user_id = e.actor_user_id
 WHERE e.outcome = 'DENIED' AND u.email LIKE 'demo\_%@example.test' ESCAPE '\'
 ORDER BY e.event_id DESC FETCH FIRST 6 ROWS ONLY;
-- the trail cannot be edited (the trigger refuses):
--   UPDATE audit_events SET outcome = 'SUCCESS' WHERE event_id = (SELECT MAX(event_id) FROM audit_events);

PROMPT == Q10 (section 10) after the delete: status DELETED, the key row is gone, location pending delete ==
SELECT f.status, TO_CHAR(f.deleted_at, 'HH24:MI:SS') AS deleted_at,
       (SELECT COUNT(*) FROM file_encryption e WHERE e.file_id = f.file_id) AS key_rows_left,
       (SELECT MAX(sl.replica_status) FROM storage_locations sl WHERE sl.file_id = f.file_id) AS location_status
  FROM files f WHERE f.file_id = &fid;

PROMPT == demo_queries.sql finished (read-only) ==
