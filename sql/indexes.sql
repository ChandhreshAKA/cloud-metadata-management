-- =====================================================================
-- indexes.sql (Chunk 1)  STATUS: NOT EXECUTED 
-- Each index is tied to a named query in docs/01-schema-audit.md section 6.
-- Already indexed by constraints (no extra index needed): every PK, every UNIQUE,
-- storage_locations(file_id) via uq_locations_file_region,
-- storage_locations(region_id) via uq_locations_region_key,
-- provider_regions(provider_id) via uq_regions_provider_code.
-- Legacy idx_files_security (3 distinct values) is intentionally NOT recreated.
-- =====================================================================
SET DEFINE OFF
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK
PROMPT == indexes.sql: creating indexes ==

-- Q1 list a user's files newest first; Q1b find a user's file by name
CREATE INDEX ix_files_owner_time ON files (owner_id, upload_time);
CREATE INDEX ix_files_owner_name ON files (owner_id, file_name);
-- Q4 duplicate detection by content hash
CREATE INDEX ix_files_sha256     ON files (content_sha256);

-- Q2 file access history, Q3 user activity, Q6 per-region latency
@@indexes_access_logs.sql

-- Q5 job queue: next jobs to run; jobs of a file
CREATE INDEX ix_jobs_status_retry ON replication_jobs (status, next_retry_at);
CREATE INDEX ix_jobs_file         ON replication_jobs (file_id);

-- Q7 provider latency history
CREATE INDEX ix_perf_region_time ON performance_measurements (region_id, measured_at);

-- Q8 audit browsing: by time, by actor, by event type
CREATE INDEX ix_audit_time       ON audit_events (event_time);
CREATE INDEX ix_audit_actor_time ON audit_events (actor_user_id, event_time);
CREATE INDEX ix_audit_type_time  ON audit_events (event_type, event_time);

-- Q9 open recommendations; recommendations of a file (FK)
CREATE INDEX ix_rec_status ON recommendations (status, rec_type);
CREATE INDEX ix_rec_file   ON recommendations (file_id);

-- Q10 decisions of a file (FK, explanation lookup)
CREATE INDEX ix_selection_file ON selection_decisions (file_id);

PROMPT == indexes.sql finished ==
