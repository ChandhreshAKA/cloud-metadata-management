-- =====================================================================
-- seed_data.sql (Chunk 1)  REFERENCE DATA  STATUS: NOT EXECUTED
-- Needed by BOTH the fresh install and the legacy migration.
-- *** Every price, score, latency and size cap below is ILLUSTRATIVE ***
-- *** (simulation assumptions), not vendor data.                     ***
-- Client is assumed to be in India (see system_config sim.client_location).
-- =====================================================================
SET DEFINE OFF
SET SQLBLANKLINES ON
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK
PROMPT == seed_data.sql (reference data) ==

INSERT INTO storage_tiers (tier_code, tier_rank, description) VALUES ('HOT',     1, 'Frequent access, lowest latency, highest storage price');
INSERT INTO storage_tiers (tier_code, tier_rank, description) VALUES ('COOL',    2, 'Infrequent access (about monthly), 30-day minimum');
INSERT INTO storage_tiers (tier_code, tier_rank, description) VALUES ('COLD',    3, 'Rare access (about quarterly), 90-day minimum');
INSERT INTO storage_tiers (tier_code, tier_rank, description) VALUES ('ARCHIVE', 4, 'Long-term retention, slow and costly retrieval');

-- max_object_bytes: AWS 5 TiB, Azure 190 TiB, Google 5 TiB (illustrative caps)
INSERT INTO cloud_providers (provider_code, provider_name, max_object_bytes) VALUES ('AWS',   'AWS',          5497558138880);
INSERT INTO cloud_providers (provider_code, provider_name, max_object_bytes) VALUES ('AZURE', 'Azure',      208907209277440);
INSERT INTO cloud_providers (provider_code, provider_name, max_object_bytes) VALUES ('GCP',   'Google Cloud', 5497558138880);

INSERT INTO provider_regions (provider_id, region_code, bucket_name, reliability_score, security_score, egress_usd_per_gb)
SELECT p.provider_id, v.region_code, v.bucket_name, v.rel, v.sec, v.egress
FROM   cloud_providers p
JOIN  (SELECT 'AWS' pc, 'ap-south-1' region_code, 'project-bucket' bucket_name, 9.50 rel, 9.20 sec, 0.0900 egress FROM dual
       UNION ALL SELECT 'AWS',   'us-east-1',     'cmm-aws-us-east-1',     9.60, 9.20, 0.0900 FROM dual
       UNION ALL SELECT 'AZURE', 'Central India', 'presentation-bucket',   9.40, 9.30, 0.0870 FROM dual
       UNION ALL SELECT 'AZURE', 'East US',       'cmm-azure-eastus',      9.45, 9.30, 0.0870 FROM dual
       UNION ALL SELECT 'GCP',   'asia-south1',   'data-bucket',           9.30, 9.10, 0.1200 FROM dual
       UNION ALL SELECT 'GCP',   'us-central1',   'cmm-gcp-us-central1',   9.35, 9.10, 0.1200 FROM dual) v
       ON v.pc = p.provider_code;

-- Pricing = base price per provider and tier x regional multiplier (India 1.00, US 0.92)
INSERT INTO provider_pricing (region_id, tier_code, native_class_name, storage_usd_per_gb_month,
                              retrieval_usd_per_gb, put_usd_per_1k, get_usd_per_1k, min_storage_days)
SELECT r.region_id, b.tier_code, b.native_class_name,
       ROUND(b.storage * m.mult, 6), ROUND(b.retrieval * m.mult, 6),
       ROUND(b.put_1k * m.mult, 6),  ROUND(b.get_1k * m.mult, 6), b.min_days
FROM   cloud_providers p
JOIN   provider_regions r ON r.provider_id = p.provider_id
JOIN  (SELECT 'AWS' pc, 'HOT' tier_code, 'STANDARD' native_class_name, 0.0250 storage, 0.0000 retrieval, 0.0050 put_1k, 0.00040 get_1k,   0 min_days FROM dual
       UNION ALL SELECT 'AWS',   'COOL',    'STANDARD_IA',  0.0138, 0.0100, 0.0100, 0.00100,  30 FROM dual
       UNION ALL SELECT 'AWS',   'COLD',    'GLACIER_IR',   0.0050, 0.0300, 0.0200, 0.01000,  90 FROM dual
       UNION ALL SELECT 'AWS',   'ARCHIVE', 'DEEP_ARCHIVE', 0.0020, 0.0200, 0.0500, 0.00040, 180 FROM dual
       UNION ALL SELECT 'AZURE', 'HOT',     'HOT',          0.0240, 0.0000, 0.0055, 0.00044,   0 FROM dual
       UNION ALL SELECT 'AZURE', 'COOL',    'COOL',         0.0150, 0.0100, 0.0100, 0.00100,  30 FROM dual
       UNION ALL SELECT 'AZURE', 'COLD',    'COLD',         0.0045, 0.0300, 0.0180, 0.01000,  90 FROM dual
       UNION ALL SELECT 'AZURE', 'ARCHIVE', 'ARCHIVE',      0.0020, 0.0220, 0.0500, 0.00500, 180 FROM dual
       UNION ALL SELECT 'GCP',   'HOT',     'STANDARD',     0.0230, 0.0000, 0.0050, 0.00040,   0 FROM dual
       UNION ALL SELECT 'GCP',   'COOL',    'NEARLINE',     0.0100, 0.0100, 0.0100, 0.00100,  30 FROM dual
       UNION ALL SELECT 'GCP',   'COLD',    'COLDLINE',     0.0040, 0.0200, 0.0100, 0.00500,  90 FROM dual
       UNION ALL SELECT 'GCP',   'ARCHIVE', 'ARCHIVE',      0.0012, 0.0500, 0.0500, 0.05000, 365 FROM dual) b
       ON b.pc = p.provider_code
JOIN  (SELECT 'ap-south-1' rc, 1.00 mult FROM dual UNION ALL SELECT 'Central India', 1.00 FROM dual
       UNION ALL SELECT 'asia-south1', 1.00 FROM dual UNION ALL SELECT 'us-east-1', 0.92 FROM dual
       UNION ALL SELECT 'East US', 0.92 FROM dual UNION ALL SELECT 'us-central1', 0.92 FROM dual) m
       ON m.rc = r.region_code;

-- Weight profiles: every row sums to exactly 1.0000 (enforced by ck_weights_sum)
INSERT INTO weight_profiles (profile_name, description, w_storage_cost, w_transfer_cost, w_latency, w_reliability, w_security, is_default)
VALUES ('BALANCED',    'Default: balance of cost, speed, reliability and security', 0.25, 0.15, 0.20, 0.25, 0.15, 'Y');
INSERT INTO weight_profiles (profile_name, description, w_storage_cost, w_transfer_cost, w_latency, w_reliability, w_security, is_default)
VALUES ('COST_SAVER',  'Favour low storage and transfer cost',                      0.40, 0.20, 0.10, 0.20, 0.10, 'N');
INSERT INTO weight_profiles (profile_name, description, w_storage_cost, w_transfer_cost, w_latency, w_reliability, w_security, is_default)
VALUES ('PERFORMANCE', 'Favour low latency',                                         0.10, 0.10, 0.50, 0.20, 0.10, 'N');
INSERT INTO weight_profiles (profile_name, description, w_storage_cost, w_transfer_cost, w_latency, w_reliability, w_security, is_default)
VALUES ('SECURE',      'Favour security and reliability',                            0.10, 0.05, 0.10, 0.25, 0.50, 'N');

-- 12 configuration rows
INSERT INTO system_config (config_key, config_value, description) VALUES ('security.min_score.LOW',    '0',   'Hard filter: minimum security_score for LOW files');
INSERT INTO system_config (config_key, config_value, description) VALUES ('security.min_score.MEDIUM', '8.0', 'Hard filter: minimum security_score for MEDIUM files');
INSERT INTO system_config (config_key, config_value, description) VALUES ('security.min_score.HIGH',   '9.2', 'Hard filter: minimum security_score for HIGH files (illustrative)');
INSERT INTO system_config (config_key, config_value, description) VALUES ('cold.idle_days',            '90',  'A file with no successful download for this many days is cold');
INSERT INTO system_config (config_key, config_value, description) VALUES ('reco.min_monthly_saving_usd', '0.05', 'Do not recommend moves saving less than this per month');
INSERT INTO system_config (config_key, config_value, description) VALUES ('reco.max_payback_months',  '6',   'Do not recommend moves whose payback exceeds this');
INSERT INTO system_config (config_key, config_value, description) VALUES ('auth.max_failed_logins',   '5',   'Lock account after this many consecutive failures');
INSERT INTO system_config (config_key, config_value, description) VALUES ('auth.lockout_minutes',      '15',  'Lockout duration');
INSERT INTO system_config (config_key, config_value, description) VALUES ('auth.jwt_expiry_minutes',   '30',  'JWT lifetime');
INSERT INTO system_config (config_key, config_value, description) VALUES ('replication.max_attempts',  '3',   'Retries before a replication job is FAILED');
INSERT INTO system_config (config_key, config_value, description) VALUES ('replication.default_replicas', '1', 'Default desired_replicas for new files');
INSERT INTO system_config (config_key, config_value, description) VALUES ('sim.client_location',      'IN',  'Assumed client location for the simulated latency baseline');

-- Simulated latency baseline: one GET/HEAD/PUT probe per region (source = SIMULATED)
INSERT INTO performance_measurements (region_id, operation, latency_ms, success, payload_bytes, source)
SELECT r.region_id, o.operation, ROUND(b.base_ms * o.factor, 2), 'Y',
       CASE WHEN o.operation = 'HEAD' THEN 0 ELSE 1048576 END, 'SIMULATED'
FROM   cloud_providers p
JOIN   provider_regions r ON r.provider_id = p.provider_id
JOIN  (SELECT 'AWS' pc, 'ap-south-1' rc, 40 base_ms FROM dual
       UNION ALL SELECT 'AWS',   'us-east-1',     220 FROM dual
       UNION ALL SELECT 'AZURE', 'Central India',  45 FROM dual
       UNION ALL SELECT 'AZURE', 'East US',       225 FROM dual
       UNION ALL SELECT 'GCP',   'asia-south1',    42 FROM dual
       UNION ALL SELECT 'GCP',   'us-central1',   210 FROM dual) b
       ON b.pc = p.provider_code AND b.rc = r.region_code
CROSS JOIN (SELECT 'GET' operation, 1.0 factor FROM dual
            UNION ALL SELECT 'HEAD', 0.6 FROM dual
            UNION ALL SELECT 'PUT',  1.4 FROM dual) o;

COMMIT;
PROMPT == seed_data.sql finished ==
