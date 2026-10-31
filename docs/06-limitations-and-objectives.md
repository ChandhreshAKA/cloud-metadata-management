# Limitations and objective-to-evidence map

State of the project: chunks 1 to 4, 6 and 7 are built and tested. Chunk 5 (replication and failover) was **not built**. Nothing below describes an unbuilt feature as working.

## 1. Objective-to-evidence map

Status words: **Fully covered**, **Partially covered**, **Not covered**.

### Objective 1: centralized metadata management system. **Fully covered**

| The objective asks for | Evidence |
|---|---|
| A relational database for file metadata, users, provider details, storage locations and access logs | 17 tables (`sql/schema.sql`); data dictionary `docs/01-data-dictionary.md`; verified by `sql/install_fresh.log` |
| Schema design and normalisation | `docs/01-schema-audit.md`: per-table keys, functional dependencies, anomalies, 8 suspected problems confirmed or rejected, the 3NF argument for every added table |
| Data integrity | 27 foreign keys with an ON DELETE rule each, 60 CHECK constraints, unique and function-based unique indexes, an append-only audit table; every constraint type shown failing with the expected ORA code (`sql/demo_constraint_failures.log`, 37 statements) |
| Transactions | `sql/demo_transactions.log` (COMMIT, ROLLBACK, SAVEPOINT, atomic upload, CASCADE and RESTRICT); in the application, upload writes six kinds of rows in one transaction |
| Indexing and query optimisation | indexes tied to named queries; measured before/after at 100,010 rows (`sql/explain_100k.log`; full scan with 752 buffer reads versus an index scan with 21); Q4 as a control that the index does not help |
| Fast retrieval | the same measurements; joins and 8 views |

Not yet run (optional, listed in `05-test-report.md`): the 10,000-row EXPLAIN comparison and the legacy-migration test.

### Objective 2: intelligent cloud selection engine. **Fully covered** (on illustrative data)

| The objective asks for | Evidence |
|---|---|
| A decision algorithm using configurable parameters: cost, latency, file size, reliability, security requirements | `app/selection/engine.py`: five weighted metrics (storage cost, transfer and request cost, latency, reliability, security) plus hard constraints (including **size fit** and the security level's minimums); weights in the `weight_profiles` table, four presets and per-request overrides |
| Not random and not cheapest-only | worked example where the cheapest provider loses (`docs/03-selection-engine.md`); `cheapest_only` and `fixed_provider` baselines in `app/selection/baselines.py`; scenario tests where three presets give three winners |
| Explains its choice | stored per upload: `selection_decisions` and one `selection_candidates` row per provider-region (scores or rejection reasons); plain-English explanation in every response |
| Correctness | worked example matches the hand calculation exactly (`tests/unit/test_selection_worked_example.py`); 8 deliberate bugs all caught |

Limit: the prices, scores, latencies and size caps are **made up** (section 3). The engine is correct and tested; its
recommendations are only as good as that data.

### Objective 3: secure and highly available storage. **Partially covered**

| Part | Status | Evidence or gap |
|---|---|---|
| Authentication | **Covered** | Argon2id hashing, signed JWT with expiry (`app/auth/`, `test_api_auth`, `test_oracle_integration`) |
| Role-based access control | **Covered** | USER / ADMIN / AUDITOR permission table; AUDITOR holds no modifying permission (tested); role re-read from the database on every request |
| Audit logging | **Covered** | append-only `audit_events`, `access_logs`; denials and failures recorded |
| Encryption at rest | **Covered**, key management **SIMULATED** | AES-256-GCM with a data key per file, wrapped by a master key (`docs/04-storage-encryption.md`); stored bytes proven not to contain the plaintext; tamper detection |
| Encryption in transit | **Not covered** | the API is served over plain HTTP in this project; TLS is not configured |
| Optional multi-cloud replication | **Not covered** | designed only; tables exist, no code |
| Failure handling for cloud outages | **Not covered** (partly: a region marked DOWN is excluded from *new* selections) | no replicas, no failover read, no repair. A file whose provider fails is unavailable |
| Failure handling for write errors | **Covered** | a storage failure writes no metadata; a database failure after storing deletes the object |

So "secure" is covered; "highly available" is not.

### Objective 4: optimize storage efficiency and monitor performance. **Partially covered**

What is implemented is **identifying** waste and showing where files are. Nothing **acts** on it, and nothing monitors
provider performance or cost over time.

| Part | Status | Evidence or gap |
|---|---|---|
| Identify duplicate files and the bytes they waste | **Covered** (exact duplicates, within one owner) | `GET /reports/duplicates`, `sql/reports.sql`, `09-reports.md`; tests `test_reports_logic`, `test_api_reports`, `test_oracle_reports` |
| Estimate the monthly cost of duplicate waste | **Covered, estimate only** | bytes / 2^30 x the **illustrative** price of each extra copy's location; an unpriced extra adds $0 and is flagged |
| Identify cold files | **Covered** as a read-only list | `GET /reports/cold-files`: idle more than N days and fewer than M downloads in a window, last access, 30-day count, provider, region, storage class, evidence, label "candidate for archival review" |
| Show a user their files and why each is where it is | **Covered** | `GET /files`, `GET /files/{id}/placement` (the saved ranking and explanation) |
| Recommend archiving or migrating, estimate saving, migration cost or payback; migrate anything | **Not covered** | not built; the `recommendations` table is unused; no saving or payback is computed for cold files |
| Storage usage and cost reports (per provider, region or tier) | **Not covered** | the views `v_region_usage` and `v_location_monthly_cost` exist, but no code or endpoint uses them |
| Provider performance reports and monitoring | **Not covered** | latency is logged in `access_logs` and the view `v_region_latency_summary` exists, but nothing reports or charts it |
| Dashboard | **Not covered** beyond one optional page | `dashboard.py` shows the file list, placement and the two report tables; no usage, cost or performance pages, no charts |

So "find waste" is covered; "optimise" (act on it) and "monitor performance" are not.

Underlying groundwork:
* `access_logs` rows (with latency and outcome) are written by upload, download and delete.
* The plaintext SHA-256 of every file is stored in `files.content_sha256`.
* SQL views created in chunk 1: `v_duplicate_files`, `v_file_access_stats`, `v_location_monthly_cost`,
  `v_region_usage`, `v_region_latency_summary`. All eight views are valid and can be queried (checked read-only
  against the database). No application code calls them (the chunk 6 reports use their own SQL) and they have no
  tests of their own beyond the chunk 1 install verification, which queried four of them.
* An unused `recommendations` table.

## 2. What is SIMULATED

| Part | What it really is | Where it lives |
|---|---|---|
| Cloud providers and storage | a **local folder per provider and region** under `storage_sim/`. Nothing talks to AWS, Azure or Google Cloud | `app/storage/simulated.py` |
| Prices | invented numbers shaped like real price lists. Flagged `is_illustrative = 'Y'`; every selection response carries an "ILLUSTRATIVE" warning | `provider_pricing` (`sql/seed_data.sql`) |
| Reliability and security scores, egress prices, size caps | invented | `provider_regions`, `cloud_providers` (`sql/seed_data.sql`) |
| Latency | seeded probes marked `source = 'SIMULATED'`, assuming a client in India; the latency recorded on upload and download is local disk time | `performance_measurements`, `access_logs` |
| Key management | the master key is the `MASTER_KEY` environment variable in the application's own memory. The cryptography (AES-256-GCM, key wrapping) is real; the *KMS* is not. What a real KMS would add is listed in `docs/04-storage-encryption.md` | `app/crypto/service.py` |
| Demo and test data | seeded users, files and logs; synthetic users and logs for EXPLAIN; the synthetic report sample (`rptsyn_*` users, `SYN_RPT_*` files); the demo accounts `demo_user` and `demo_auditor`, whose auditor role the demo script sets directly in the database | `sql/seed_demo.sql`, `sql/synthetic_data.sql`, `app/reports/sample_data.py`, `scripts/demo.py`, `tests/` |
| Provider health | set by an administrator through the API, not detected | `provider_regions.health_status` |

Real and unsimulated: the schema and every constraint, all SQL, authentication, RBAC, the audit trail, the selection
algorithm, the encryption algorithm and the integrity checks.

To list every simulated figure in the database:
```sql
SELECT r.region_code, pr.tier_code, pr.is_illustrative FROM provider_pricing pr JOIN provider_regions r ON r.region_id = pr.region_id;
SELECT region_id, source, COUNT(*) FROM performance_measurements GROUP BY region_id, source;
```
(The `assumptions.md` file planned in chunk 0 was not written; this section and those flags replace it.)

## 3. What is NOT implemented

| Not implemented | Consequence |
|---|---|
| Replication and failover | one copy per file; a provider outage makes that file unavailable |
| Near-duplicate and cross-owner duplicate detection | exact duplicates within one owner are reported (chunk 6); a slightly edited copy is not found |
| Migration or archival recommendations, savings and payback, migration workflow | none; cold files are only listed as candidates (chunk 6) |
| Storage usage and cost reports, provider performance reports, monitoring over time | none (the duplicate report estimates waste and the cold report counts downloads; neither is a usage, cost or performance report) |
| Monitoring pages and charts | only a one-page optional Streamlit view of files, placement and the two reports (`dashboard.py`); the scripted demo is `scripts/demo.py` |
| Real cloud adapters | one simulated adapter only |
| TLS / HTTPS | not configured |
| Key rotation | a file encrypted under another master-key version cannot be decrypted |
| Orphan sweep | a crash between storing an object and committing the metadata leaves an unreferenced object |
| Retry jobs | a failed physical delete leaves the location `PENDING_DELETE` with nothing to retry it |
| Streaming and multipart upload | whole files are held in memory (default limit 50 MiB); upload is a raw request body |
| Account lockout, password reset, token revocation, user-management endpoints, rate limiting, quotas | none |
| File versions | a replacement is a new file |
| Continuous integration, type checking, containers | none |

## 4. Security and design limitations

* **Plaintext hashes reveal whether a known file exists (limitation L-1).** `files.content_sha256` is the SHA-256 of
  the *plaintext*. Anyone who can read that column (a database administrator, a compromised reporting account, an
  injection that reads the table) can compute the hash of a file they already hold and learn whether it is stored
  here and by whom; equal hashes also show that two files are identical, even across owners. Low-entropy files can be
  guessed offline. The upload response returns the hash to its owner. Not built: using a keyed hash (HMAC-SHA-256 with
  a server-side secret), which keeps duplicate detection inside the system and defeats outside confirmation. The
  duplicate report (chunk 6) is built on this hash. It only groups files within one owner and a USER only sees their own groups, but ADMIN and
  AUDITOR see every owner's group hashes through the report, and the database column itself is unchanged, so the caveat above still applies.
* **403 versus 404 reveals which file ids exist** (ids are sequential).
* **JWT is HS256 with no revocation:** a stolen token works until it expires (30 minutes); a disabled account or a
  changed role does take effect immediately because the database is consulted on every request.
* **Requests with a missing or invalid token are not audited**, only login failures and 403 denials.
* **Audit protection is a trigger.** It blocks `UPDATE` and `DELETE`; a database administrator can disable the trigger
  or `TRUNCATE` the table (the cleanup script does the latter for test data).
* **The master key lives in the process environment.** Losing it makes every stored file unreadable.
* **Scores are administrator-entered**; the engine trusts them.

## 5. Selection-engine caveats

* Min-max scores depend on which candidates are eligible; adding or removing a provider can change the others'
  scores and, rarely, the order of two others (rank reversal).
* Min-max stretches *any* difference to the range 0 to 1, so for a very small file a fraction-of-a-cent price difference can
  outweigh latency. It happened: a 20 KB `HIGH` file under `BALANCED` weights went to Azure East US over a nearer
  region. The `PERFORMANCE` preset or a heavier latency weight changes this.
* One storage tier is priced per request (default `HOT`); the engine does not choose a tier.
* Costs depend on caller estimates of accesses and retention, and assume each read downloads the whole file.

## 6. What the evidence can and cannot prove

* The **unit tests** use an in-memory stand-in for the database. They prove application logic, **not** that the SQL
  works on Oracle. The **integration tests** prove that, but only against one environment (Oracle Database Free 26ai on
  Windows).
* The EXPLAIN PLAN comparison is **one run on one machine**; elapsed times of hundredths of a second are too small to
  compare, so the claim rests on the plan operation and buffer reads. No statistical claim is made about speed-ups.
* The selection engine's quality is shown by worked examples, scenario tests and baselines on **invented data**. It
  cannot show that the engine would choose well for real prices and real latency.
* The simulated storage cannot show behaviour under real network failure, throttling, eventual consistency or cost.
* The **duplicate and cold-file reports** are checked against a **synthetic** sample with a written answer key, on one machine.
  The unit tests use canned rows, so they prove the arithmetic, role scoping and wording, not the SQL; the Oracle tests prove the SQL on that
  sample. They cannot show how the rules behave on real usage patterns, and no query plan or timing has been recorded for the report queries.
* The **demo script** passes against the real API and database on the machine where it was written; its results table in
  `05-test-report.md` (T12) is for your own run. The optional Streamlit page was tested headlessly, not in a browser window.
