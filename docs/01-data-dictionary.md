# Data dictionary (Chunk 1)

Status: NOT EXECUTED. Mirrors `sql/schema.sql`. PK = primary key, UK = unique, FK = foreign key (rule in brackets), NN = NOT NULL. "Illustrative" = a simulation assumption, not vendor data. GB = GiB (2^30 bytes). Timestamps are `TIMESTAMP(6)`.

## Cardinalities

| Relationship | Cardinality | Enforced by |
|---|---|---|
| user owns files | 1 : 0..N | `fk_files_owner` |
| user performs access_logs | 1 : 0..N | `fk_logs_user` |
| cloud_provider offers provider_regions | 1 : 1..N | `fk_regions_provider` (the "at least one" is application-level) |
| provider_region has provider_pricing rows | 1 : 0..4 (one per tier) | PK `(region_id, tier_code)` |
| provider_region hosts storage_locations | 1 : 0..N | `fk_locations_region` |
| file has storage_locations (copies) | 1 : 0..N, at most one per region | `uq_locations_file_region` |
| file has a primary copy | 1 : 0..1 | `ux_locations_one_primary` |
| file has encryption row | 1 : 0..1 | PK = FK on `file_encryption.file_id` |
| file has access_logs | 1 : 0..N | `fk_logs_file` |
| file has replication_jobs | 1 : 0..N | `fk_jobs_file` |
| file has selection_decisions | 1 : 0..N | `fk_decisions_file` |
| selection_decision has candidates | 1 : 1..N | `fk_candidates_decision` |
| region probed by performance_measurements | 1 : 0..N | `fk_perf_region` |
| storage_tier classifies pricing rows and copies | 1 : 0..N each | `fk_pricing_tier`, `fk_locations_tier` |
| weight_profile used by decisions | 1 : 0..N | `fk_decisions_profile` |

## storage_tiers
| Column | Type | Rules | Meaning |
|---|---|---|---|
| tier_code | VARCHAR2(10) | PK | HOT, COOL, COLD, ARCHIVE |
| tier_rank | NUMBER(1) | NN, UK | 1 = hottest |
| description | VARCHAR2(200) | NN | |

## users
| Column | Type | Rules | Meaning |
|---|---|---|---|
| user_id | NUMBER identity | PK | |
| full_name | VARCHAR2(100) | NN | legacy `Name` |
| email | VARCHAR2(150) | NN, UK, lower-case, contains `@` | login identifier |
| role | VARCHAR2(10) | NN, default USER, in (USER, ADMIN, AUDITOR) | RBAC role |
| password_hash | VARCHAR2(255) | NULL only if status PENDING_RESET or DISABLED | argon2 string (chunk 2) |
| account_status | VARCHAR2(15) | NN, default PENDING_RESET; ACTIVE, LOCKED, DISABLED, PENDING_RESET | |
| failed_login_count | NUMBER(3) | NN, default 0, >= 0 | consecutive failures |
| locked_until | TIMESTAMP | | lockout end |
| last_login_at | TIMESTAMP | | |
| created_at | TIMESTAMP | NN, default now | |

## cloud_providers
| Column | Type | Rules | Meaning |
|---|---|---|---|
| provider_id | NUMBER identity | PK | |
| provider_code | VARCHAR2(20) | NN, UK | stable code (AWS, AZURE, GCP) used by scripts |
| provider_name | VARCHAR2(50) | NN, UK | display name (legacy `ProviderName`) |
| max_object_bytes | NUMBER(19) | NN, > 0 | size-fit hard filter (illustrative cap) |
| is_active | CHAR(1) | NN, Y/N | deactivate instead of delete |

## provider_regions
| Column | Type | Rules | Meaning |
|---|---|---|---|
| region_id | NUMBER identity | PK | |
| provider_id | NUMBER | NN, FK (RESTRICT) | |
| region_code | VARCHAR2(50) | NN, UK with provider_id | e.g. ap-south-1 |
| bucket_name | VARCHAR2(150) | NN, UK with provider_id | the single managed bucket (rule R-1) |
| reliability_score | NUMBER(4,2) | NN, 0..10 | illustrative |
| security_score | NUMBER(4,2) | NN, 0..10 | illustrative |
| egress_usd_per_gb | NUMBER(10,6) | NN, >= 0 | illustrative; per region, not per tier |
| health_status | VARCHAR2(10) | NN, UP/DEGRADED/DOWN | current state |
| status_changed_at | TIMESTAMP | NN | |
| is_active | CHAR(1) | NN, Y/N | |
| is_illustrative | CHAR(1) | NN, Y/N | Y = values are simulation assumptions |

## provider_pricing
| Column | Type | Rules | Meaning |
|---|---|---|---|
| region_id | NUMBER | PK part, FK (CASCADE) | |
| tier_code | VARCHAR2(10) | PK part, FK (RESTRICT) | |
| native_class_name | VARCHAR2(40) | NN, UK with region_id | provider's own name, e.g. NEARLINE |
| storage_usd_per_gb_month | NUMBER(10,6) | NN, >= 0 | illustrative |
| retrieval_usd_per_gb | NUMBER(10,6) | NN, >= 0 | illustrative |
| put_usd_per_1k / get_usd_per_1k | NUMBER(10,6) | NN, >= 0 | per 1,000 requests, illustrative |
| min_storage_days | NUMBER(4) | NN, >= 0 | early-deletion window |
| is_illustrative | CHAR(1) | NN, Y/N | |

## weight_profiles
| Column | Type | Rules | Meaning |
|---|---|---|---|
| profile_name | VARCHAR2(30) | PK | BALANCED, COST_SAVER, PERFORMANCE, SECURE |
| description | VARCHAR2(200) | NN | |
| w_storage_cost, w_transfer_cost, w_latency, w_reliability, w_security | NUMBER(5,4) | NN, each 0..1, **sum exactly 1** | criterion weights |
| is_default | CHAR(1) | NN, Y/N, at most one Y | |

## system_config
| Column | Type | Rules | Meaning |
|---|---|---|---|
| config_key | VARCHAR2(80) | PK | e.g. cold.idle_days |
| config_value | VARCHAR2(200) | NN | stored as text; the app parses |
| description | VARCHAR2(300) | NN | |
| updated_at / updated_by | TIMESTAMP / NUMBER | updated_by FK users (RESTRICT) | |

## files
| Column | Type | Rules | Meaning |
|---|---|---|---|
| file_id | NUMBER identity | PK | |
| owner_id | NUMBER | NN, FK users (RESTRICT) | |
| file_name | VARCHAR2(255) | NN, no `/` or `\` | not unique (see audit 1.3) |
| file_size_bytes | NUMBER(19) | NN, >= 0 | plaintext size |
| file_type | VARCHAR2(10) | NN, default OTHER; PDF, DOCX, PPTX, XLSX, CSV, TXT, JSON, JPG, PNG, ZIP, MP4, OTHER | |
| security_level | VARCHAR2(10) | NN, LOW/MEDIUM/HIGH, no default | drives the hard security filter |
| status | VARCHAR2(10) | NN, default ACTIVE; ACTIVE, DEGRADED, DELETED | |
| content_sha256 | VARCHAR2(64) | 64 lowercase hex or NULL | NULL only for migrated legacy rows. Hash of plaintext (limitation L-1) |
| desired_replicas | NUMBER(2) | NN, 1..5, default 1 | target number of copies incl. primary |
| expected_accesses_per_month | NUMBER(8) | NN, >= 0, default 1 | caller hint for transfer-cost scoring |
| upload_time | TIMESTAMP | NN, default now | |
| deleted_at | TIMESTAMP | set iff status = DELETED | |

## file_encryption
| Column | Type | Rules | Meaning |
|---|---|---|---|
| file_id | NUMBER | PK, FK files (CASCADE) | |
| algorithm | VARCHAR2(20) | NN, AES-256-GCM | |
| wrapped_dek | RAW(512) | NN | data key wrapped by the master key (master key is NOT in the DB) |
| nonce | RAW(12) | NN | GCM nonce |
| master_key_version | NUMBER(5) | NN, default 1 | for rotation |
| ciphertext_sha256 | VARCHAR2(64) | NN, 64 lowercase hex | integrity of stored bytes; same for every replica |
| encrypted_at | TIMESTAMP | NN | |

## storage_locations
| Column | Type | Rules | Meaning |
|---|---|---|---|
| location_id | NUMBER identity | PK | |
| file_id | NUMBER | NN, FK files (CASCADE) | |
| region_id | NUMBER | NN, FK provider_regions (RESTRICT) | provider is derived through the region |
| tier_code | VARCHAR2(10) | NN, FK storage_tiers | |
| object_key | VARCHAR2(300) | NN | UK with region_id |
| is_primary | CHAR(1) | NN, Y/N, at most one Y per file | |
| replica_status | VARCHAR2(15) | NN, default PENDING; PENDING, OK, FAILED, STALE, PENDING_DELETE | |
| stored_at / last_verified_at | TIMESTAMP | | |
UK: `(file_id, region_id)`, `(region_id, object_key)`.

## access_logs
| Column | Type | Rules | Meaning |
|---|---|---|---|
| log_id | NUMBER identity | PK | |
| file_id / user_id | NUMBER | NN, FK (RESTRICT) | |
| action | VARCHAR2(20) | NN; UPLOAD, DOWNLOAD, DELETE, VIEW_METADATA, REPLICATE, MIGRATE | |
| outcome | VARCHAR2(10) | NN, default SUCCESS; SUCCESS, FAILURE, DENIED | |
| access_time | TIMESTAMP | NN, default now | |
| latency_ms | NUMBER(10,2) | >= 0, NULL when no request reached a provider | milliseconds |
| served_by_region_id | NUMBER | FK provider_regions (RESTRICT), NULL when denied | which region answered |

## replication_jobs
| Column | Type | Rules | Meaning |
|---|---|---|---|
| job_id | NUMBER identity | PK | |
| file_id | NUMBER | NN, FK (CASCADE) | |
| source_location_id | NUMBER | FK storage_locations (SET NULL) | |
| target_region_id | NUMBER | NN, FK provider_regions | |
| job_type | VARCHAR2(12) | NN; REPLICATE, REPAIR, MIGRATE, DELETE | |
| status | VARCHAR2(10) | NN, default QUEUED; QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED | |
| attempts | NUMBER(3) | NN, >= 0 | |
| error_code / error_message | VARCHAR2 | | last failure |
| created_at, started_at, finished_at, next_retry_at | TIMESTAMP | finished >= started | |

## performance_measurements
| Column | Type | Rules | Meaning |
|---|---|---|---|
| measurement_id | NUMBER identity | PK | |
| region_id | NUMBER | NN, FK | |
| measured_at | TIMESTAMP | NN | |
| operation | VARCHAR2(6) | NN; PUT, GET, HEAD, DELETE | |
| latency_ms | NUMBER(10,2) | >= 0; NN when success = Y | |
| success | CHAR(1) | NN, Y/N | |
| payload_bytes | NUMBER(19) | NN, >= 0 | |
| source | VARCHAR2(10) | NN; SIMULATED, MEASURED | never present simulated numbers as measured |

## selection_decisions / selection_candidates
`selection_decisions`: `decision_id` PK; `file_id` FK (CASCADE); `profile_name` FK; `weights_json` NN valid JSON (frozen copy); `chosen_region_id` FK, NULL if nothing eligible; `explanation` NN VARCHAR2(2000); `decided_at`.
`selection_candidates`: PK `(decision_id, region_id)`; `excluded_reason` (set iff excluded); `raw_json` (raw criterion values, valid JSON); `norm_storage_cost`, `norm_transfer_cost`, `norm_latency`, `norm_reliability`, `norm_security`, `total_score` all 0..1; present iff not excluded (`ck_candidates_shape`).

## recommendations
| Column | Type | Rules | Meaning |
|---|---|---|---|
| recommendation_id | NUMBER identity | PK | |
| rec_type | VARCHAR2(12) | NN; ARCHIVE, MIGRATE, DEDUPLICATE | |
| file_id | NUMBER | NN, FK (CASCADE) | subject |
| related_file_id | NUMBER | FK (CASCADE); required for DEDUPLICATE | the copy to keep |
| target_region_id, target_tier_code | NUMBER, VARCHAR2 | FK; required for ARCHIVE and MIGRATE | |
| est_monthly_saving_usd, est_migration_cost_usd | NUMBER(12,4) | NN | snapshot from pricing at creation (illustrative prices) |
| evidence_json | VARCHAR2(4000) | NN, valid JSON | idle days, access counts, both cost figures, assumptions |
| status | VARCHAR2(10) | NN, default OPEN; OPEN, ACCEPTED, DISMISSED, APPLIED | |
| created_at, decided_by, decided_at | | decided_at set iff status <> OPEN | |

## audit_events (append-only)
| Column | Type | Rules | Meaning |
|---|---|---|---|
| event_id | NUMBER identity | PK | |
| event_time | TIMESTAMP | NN | |
| actor_user_id | NUMBER | FK users (RESTRICT), NULL for unknown user | |
| event_type | VARCHAR2(30) | NN; USER_REGISTERED, LOGIN_OK, LOGIN_FAILED, ACCOUNT_LOCKED, ROLE_CHANGED, FILE_UPLOADED, FILE_DELETED, REPLICA_CREATED, FAILOVER_READ, PROVIDER_STATUS_CHANGED, CONFIG_CHANGED, RECOMMENDATION_DECIDED, ACCESS_DENIED | |
| target_type / target_id | VARCHAR2(20) / NUMBER | no FK by design | |
| outcome | VARCHAR2(10) | NN; SUCCESS, FAILURE, DENIED | |
| ip_address | VARCHAR2(45) | | |
| details_json | VARCHAR2(2000) | valid JSON or NULL | |
Trigger `trg_audit_events_immutable` raises ORA-20001 on UPDATE or DELETE. `TRUNCATE` is DDL and bypasses it (admin use only).

## Views
| View | Purpose |
|---|---|
| `v_file_metadata` | files with owner, primary provider, region, tier (LEFT JOINs: files without a location still appear) |
| `v_file_access_stats` | replaces `AccessFrequency`: downloads total, last 30 days, last download |
| `v_file_replication_status` | OK copies vs desired: NONE / PARTIAL / FULL |
| `v_duplicate_files` | content hashes shared by 2+ files, with wasted bytes |
| `v_location_monthly_cost` | estimated monthly storage cost per copy (illustrative prices) |
| `v_unpriced_locations` | integrity check; expect 0 rows |
| `v_region_usage` | copies and bytes per region |
| `v_region_latency_summary` | average and median observed latency per region |
