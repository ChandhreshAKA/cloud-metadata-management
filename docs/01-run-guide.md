# Chunk 1 run guide

**Every result in this guide is NOT EXECUTED.** The "expected output" blocks are what the scripts should print if they are correct, derived by reading the SQL, not by running it. Where a number depends on Oracle internals I say so. After you run a script, paste the real output and I will compare it with these expectations and fix any script errors.

All commands run in PowerShell from the scripts folder. SQL*Plus asks for the password; do not put it on the command line.

```powershell
cd C:\dbmsproject\sql
```

Connect string used below: `localhost:1521/FREEPDB1` (change if your installer printed a different service name).

## Run order

| Path | Steps |
|---|---|
| **A. Fresh install** (use this first) | 0 → 1 → 2 → 3 → 4 → 5 → 6 |
| **B. Legacy migration test** | 0 → 7 (this wipes the schema; do it after path A, or instead of it) |

---

## 0. Create the project user (once, as SYS)  `prereq_as_sysdba.sql`

```powershell
sqlplus sys@localhost:1521/FREEPDB1 as sysdba '@prereq_as_sysdba.sql'
```
Skip this if you already created `cloudmeta` with the manual commands from earlier; in that case just run the five `GRANT` statements from the script.

Expected:
```
CON_NAME
------------------------------
FREEPDB1
User created.
Grant succeeded.      (x5)
== CLOUDMETA ready. ...
```
| Error | Meaning |
|---|---|
| ORA-01920 user name 'CLOUDMETA' conflicts | user already exists; run only the GRANTs |
| ORA-65096 invalid common user or role name | you are in the root container (`CON_NAME = CDB$ROOT`); reconnect to `FREEPDB1` |
| ORA-28003 password verification failed | choose a stronger password |

## 1. Fresh install  `install_fresh.sql`

```powershell
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@install_fresh.sql'
```
Runs `schema.sql`, `indexes.sql`, `views.sql`, `seed_data.sql`, `seed_demo.sql`, `verify_schema.sql` and spools `install_fresh.log`. Stops at the first error (`WHENEVER SQLERROR EXIT`).

Expected counts of messages (order as listed):

| Script | Messages |
|---|---|
| schema.sql | `Table created.` x17; `Index created.` x2 (the two `ux_` function-based indexes); `Trigger created.` x1; `Comment created.` x9 |
| indexes.sql | `Index created.` x15 |
| views.sql | `View created.` x8 |
| seed_data.sql | `1 row created.` x4 (tiers), x3 (providers), x4 (profiles), x12 (config); `6 rows created.` (regions); `24 rows created.` (pricing); `18 rows created.` (performance); `Commit complete.` |
| seed_demo.sql | `1 row created.` x3 (users); `6 rows created.` (files); `7 rows created.` (locations); `10 rows created.` (logs); `Commit complete.` |

Expected `verify_schema.sql` output:

**1. Objects by type**: TABLE 17 VALID, VIEW 8 VALID, TRIGGER 1 VALID, INDEX 43 VALID (17 PK + 9 UNIQUE + 2 function-based + 15 `ix_`), SEQUENCE about 11 (identity sequences, system-named). If SEQUENCE or INDEX differ slightly, tell me; the important rule is **no INVALID rows**.

**2 and 3**: `no rows selected` for both.

**4. Row counts**
```
STORAGE_TIERS 4        CLOUD_PROVIDERS 3      PROVIDER_REGIONS 6     PROVIDER_PRICING 24
WEIGHT_PROFILES 4      SYSTEM_CONFIG 12       PERFORMANCE_MEASUREMENTS 18
USERS 3                FILES 6                FILE_ENCRYPTION 0      STORAGE_LOCATIONS 7
ACCESS_LOGS 10         REPLICATION_JOBS 0     SELECTION_DECISIONS 0  SELECTION_CANDIDATES 0
RECOMMENDATIONS 0      AUDIT_EVENTS 0
```

**5. Foreign keys**: 27 rows. DELETE_RULE column: `CASCADE` for FK_PRICING_REGION, FK_ENCRYPTION_FILE, FK_LOCATIONS_FILE, FK_JOBS_FILE, FK_DECISIONS_FILE, FK_CANDIDATES_DECISION, FK_REC_FILE, FK_REC_RELATED (8); `SET NULL` for FK_JOBS_SOURCE (1); `NO ACTION` for the other 18.

**6. Constraint counts**: `C` 60, `P` 17, `R` 27, `U` 9.

**7. Non-constraint indexes**: 17 rows (the 15 `IX_` plus `UX_LOCATIONS_ONE_PRIMARY` and `UX_WEIGHTS_ONE_DEFAULT`).

**8**: `no rows selected`. **9**: four rows each with total `1`.

**10. Sample views.** Ids may not be 1..6 in the order below (an `INSERT ... SELECT` over a join does not guarantee identity order), so compare by file name:

| file | primary | replication_state | downloads_total / 30d |
|---|---|---|---|
| project_report.pdf | AWS ap-south-1 HOT OK | FULL (2 of 2) | 2 / 2 |
| presentation.pptx | Azure Central India HOT OK | FULL | 1 / 1 |
| dataset.csv | Google Cloud asia-south1 HOT OK | **PARTIAL** (1 of 2) | 0 / 0 |
| notes.docx | AWS ap-south-1 | FULL | 0 / 0 |
| notes_copy.docx | AWS ap-south-1 | FULL | 0 / 0 |
| old_backup.zip | Google Cloud asia-south1 | FULL | 0 / 0 |

`v_duplicate_files`: one row, `copies 2`, `total_bytes 4194304`, `wasted_bytes 2097152`. The DENIED download of project_report.pdf is correctly not counted.

| Error | Meaning |
|---|---|
| ORA-00955 name is already used | schema not empty; run `drop_all.sql` |
| ORA-01031 insufficient privileges | step 0 grants missing |
| ORA-01950 no privileges on tablespace | quota missing; `ALTER USER cloudmeta QUOTA UNLIMITED ON users;` as SYS |

## 2. Transactions demo  `demo_transactions.sql`

```powershell
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@demo_transactions.sql'
```
Needs step 1 done. Leaves no rows. Expected (key lines):

| Step | Expected |
|---|---|
| T1 | `1 row created.` `Commit complete.` then a row `txn_demo_commit.txt LOW ACTIVE` |
| T2 | `visible_in_my_session 1`, `Rollback complete.`, `after_rollback 0` |
| T3 | savepoint created; location `1 row created.`; first SELECT `HIGH, 1`; `Rollback complete.`; second SELECT `LOW, 0`; `file_survived_commit 1` |
| T4 | the second insert fails: **`ORA-02291: integrity constraint (CLOUDMETA.FK_LOCATIONS_REGION) violated - parent key not found`**; then `orphan_file_rows 0` |
| T5 | `locations_before 1`, `1 row deleted.`, `locations_after 0` |
| T6 | **`ORA-02292: integrity constraint (CLOUDMETA.FK_LOGS_FILE) violated - child record found`**, then `project_report_still_there 1` |
| Cleanup | `1 row deleted.`, `txn_demo_rows_left 0` |

Exactly two ORA errors appear (T4 and T6). Any other error is a script bug; send it to me.

**Optional two-session isolation test** (shows row locking, `READ COMMITTED`). Open two SQL*Plus windows as `cloudmeta`.
```sql
-- Session A
UPDATE files SET security_level = 'HIGH' WHERE file_name = 'notes.docx';      -- do NOT commit
-- Session B
SELECT security_level FROM files WHERE file_name = 'notes.docx';              -- still LOW (no dirty read)
SELECT * FROM files WHERE file_name = 'notes.docx' FOR UPDATE NOWAIT;         -- ORA-00054 resource busy
UPDATE files SET security_level = 'MEDIUM' WHERE file_name = 'notes.docx';    -- hangs until A ends
-- Session A
ROLLBACK;                                                                      -- B's UPDATE now completes; ROLLBACK in B too
```

## 3. Constraint failure demo  `demo_constraint_failures.sql`

```powershell
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@demo_constraint_failures.sql'
```
**Every statement fails except F35.** The error text names the constraint in parentheses; match on that. Exact wording after the code may differ a little between Oracle versions.

| # | Constraint type | Expected error |
|---|---|---|
| F01 | PRIMARY KEY | ORA-00001 unique constraint (CLOUDMETA.PK_USERS) violated |
| F02 | UNIQUE | ORA-00001 ... UQ_USERS_EMAIL |
| F03 | UNIQUE composite | ORA-00001 ... UQ_LOCATIONS_FILE_REGION |
| F04 | UNIQUE function-based | ORA-00001 ... UX_LOCATIONS_ONE_PRIMARY |
| F05 | UNIQUE composite | ORA-00001 ... UQ_LOCATIONS_REGION_KEY |
| F06 | UNIQUE function-based | ORA-00001 ... UX_WEIGHTS_ONE_DEFAULT |
| F07 | NOT NULL | ORA-01400 cannot insert NULL into ("CLOUDMETA"."USERS"."EMAIL") |
| F08 | NOT NULL | ORA-01400 ... ("CLOUDMETA"."FILES"."SECURITY_LEVEL") |
| F09 | NOT NULL | ORA-01400 ... ("CLOUDMETA"."PROVIDER_REGIONS"."SECURITY_SCORE") |
| F10 | CHECK | ORA-02290 check constraint (CLOUDMETA.CK_USERS_EMAIL_LOWER) violated |
| F11 | CHECK | ORA-02290 ... CK_USERS_ROLE |
| F12 | CHECK | ORA-02290 ... CK_USERS_PWD |
| F13 | CHECK | ORA-02290 ... CK_FILES_SIZE |
| F14 | CHECK | ORA-02290 ... CK_FILES_TYPE |
| F15 | CHECK | ORA-02290 ... CK_FILES_SECURITY |
| F16 | CHECK | ORA-02290 ... CK_FILES_SHA256 |
| F17 | CHECK | ORA-02290 ... CK_FILES_DELETED |
| F18 | CHECK | ORA-02290 ... CK_FILES_NAME |
| F19 | CHECK | ORA-02290 ... CK_LOGS_ACTION |
| F20 | CHECK | ORA-02290 ... CK_LOGS_OUTCOME |
| F21 | CHECK | ORA-02290 ... CK_LOGS_LATENCY |
| F22 | CHECK | ORA-02290 ... CK_REGIONS_RELIABILITY |
| F23 | CHECK | ORA-02290 ... CK_WEIGHTS_SUM |
| F24 | CHECK (JSON) | ORA-02290 ... CK_DECISIONS_WEIGHTS_JSON |
| F25 | CHECK | ORA-02290 ... CK_REC_SHAPE |
| F26 | CHECK | ORA-02290 ... CK_PERF_OK_HAS_LATENCY |
| F27 | CHECK | ORA-02290 ... CK_AUDIT_TYPE |
| F28 | FOREIGN KEY (child) | ORA-02291 integrity constraint (CLOUDMETA.FK_FILES_OWNER) violated - parent key not found |
| F29 | FOREIGN KEY (child) | ORA-02291 ... FK_LOCATIONS_TIER ... parent key not found |
| F30 | FOREIGN KEY (parent) | ORA-02292 integrity constraint (CLOUDMETA.FK_xxx) violated - child record found. **xxx is whichever child Oracle checks first** (FK_FILES_OWNER, FK_LOGS_USER, ...); any is correct |
| F31 | FOREIGN KEY (parent) | ORA-02292 ... FK_LOGS_FILE |
| F32 | FOREIGN KEY (parent) | ORA-02292 ... FK_REGIONS_PROVIDER |
| F33 | data type length | ORA-12899 value too large for column "CLOUDMETA"."FILES"."FILE_NAME" (actual: 256, maximum: 255) |
| F34 | data type precision | ORA-01438 value larger than specified precision allowed for this column |
| F35 | (setup) | `1 row created.` (succeeds) |
| F36 | trigger | ORA-20001 audit_events is append-only: UPDATE and DELETE are not allowed, followed by ORA-06512 at "CLOUDMETA.TRG_AUDIT_EVENTS_IMMUTABLE" |
| F37 | trigger | same as F36 |
| end | | `Rollback complete.` then `users_rows 3  files_rows 6  audit_rows 0` |

If one of these statements **succeeds** (`1 row created.`) that constraint is not working: send me the line.

## 4. Synthetic data, 10,000 rows  `synthetic_data.sql`

```powershell
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@synthetic_data.sql' 10000
```
Expected: `inserted 10000 of 10000`, `PL/SQL procedure successfully completed.`, then
```
SYNTHETIC_USERS SYNTHETIC_FILES ACCESS_LOGS_TOTAL
            100            5000             10010
SYNTHETIC_DUPLICATE_GROUPS
                       250
```
(10 = the 10 demo logs.) It should take seconds. `ORA-06550` / `PLS-00201 identifier DBMS_RANDOM must be declared` means the step 0 grants are missing.

## 5. EXPLAIN PLAN demo  `explain_plan_demo.sql`

```powershell
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@explain_plan_demo.sql'
```
Then repeat with 100,000 rows:
```powershell
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@synthetic_data.sql' 100000
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@explain_plan_demo.sql'
```
Paste each `explain_plan_demo.log` into `docs/01-explain-results.md` (blank sections provided). **I have not seen any real plan; the table below is a prediction to test, not a result.**

| Query | BEFORE (no `ix_logs_*`) | AFTER | Confidence |
|---|---|---|---|
| Q2 file history, ORDER BY time DESC | `TABLE ACCESS FULL ACCESS_LOGS` + `SORT ORDER BY`; A-Rows about 10,010 read for a handful returned | `INDEX RANGE SCAN DESCENDING IX_LOGS_FILE_TIME` + `TABLE ACCESS BY INDEX ROWID`; no sort; buffers drop sharply | High at both sizes |
| Q3 latest 50 of one user | Full scan + sort + `COUNT STOPKEY` | `INDEX RANGE SCAN DESCENDING IX_LOGS_USER_TIME` with `COUNT STOPKEY` | Medium at 10k (a user is about 1% of rows; the optimizer may still scan), higher at 100k |
| Q4 control, MAX per file | Full scan + `HASH GROUP BY` | the same | Control. If it changes, tell me; the optimizer may use a fast full scan of an index |

What to read in each block: the `Operation` column (FULL vs INDEX RANGE SCAN, presence of SORT), `Cost (%CPU)` in the estimated plan, and `A-Rows`, `Buffers`, `A-Time` in the `ALLSTATS LAST` plan. Buffers is the most reliable evidence (logical reads do not depend on caching or machine speed).

| Problem | Meaning |
|---|---|
| `SP2-0158: unknown SET option "ONLY"` | SQL*Plus older than 12.2; replace `SET FEEDBACK ONLY` with `SET AUTOTRACE OFF` and `SET TERMOUT OFF` around the query (tell me and I will patch) |
| `ORA-00942` or "User has no SELECT privilege on V$SESSION" from DISPLAY_CURSOR | `SELECT_CATALOG_ROLE` not granted; run step 0 grants |
| ORA-01418 inside the script | harmless, handled |
| `Using file_id =  and user_id =` is empty | step 4 not run |

After step 5 the three `ix_logs_*` indexes exist again.

## 6. Clean up synthetic data  `cleanup_synthetic.sql`

```powershell
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@cleanup_synthetic.sql'
```
Expected final line: `USERS_ROWS 3  FILES_ROWS 6  ACCESS_LOGS_ROWS 10`.

## 7. Legacy migration test (path B)

This destroys the revised schema to rebuild the old one. Order:
```powershell
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@drop_all.sql'          # type DROP when asked; prints "Dropped 17 tables." (or the number you had), tables_left 0
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@legacy_schema.sql'     # builds the original 5 tables; expect no errors
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@migrate_legacy.sql'
```
Legacy state after step 2: Users 3, Cloud_Providers 3, Files **5** (ids 1, 2, 3, 4, 6; id 5 was the deleted temp file), Storage_Locations 3, Access_Logs 3.

Expected migration output (section numbers match the script's PROMPT lines):

| Section | Expected |
|---|---|
| 1 guard | `PL/SQL procedure successfully completed.` |
| 2 | `Table altered.` x5 |
| 3 | the same messages as install (17 tables, 15 indexes, 8 views, seed rows) |
| 4 | `3 rows created.` |
| 5 | providers `0 rows created.`; regions `0 rows created.`, `0 rows created.` (all legacy provider-regions already exist in the seed) |
| 6 | `5 rows created.` |
| 7 | `3 rows created.` |
| 8 | `3 rows created.` |
| 9 | `Table altered.` x4 |
| 10 | users 3/3, files 5/5, storage_locations 3/3, access_logs 3/3, provider rows 3/3 |
| 11 | `AccessFrequency` counter vs downloads in log: project_report 12 vs 1, presentation 7 vs 1, dataset 2 vs 0, notes.docx 1 vs 0, admin_draft 0 vs 0. This is the evidence for audit item 2.2 |
| 12 | `no rows selected` |
| 13 | `files_with_null_legacy_security 0`, then `no rows selected` |
| 14 | `no rows selected` |

Then `@verify_schema.sql`: TABLE count 22 (17 new + 5 `LEGACY_*`); row counts USERS 3, FILES 5, STORAGE_LOCATIONS 3, ACCESS_LOGS 3, PROVIDER_REGIONS 6; `v_unpriced_locations` empty. Check identity restart:
```sql
INSERT INTO files (owner_id, file_name, file_size_bytes, file_type, security_level)
VALUES (1, 'after_migration.txt', 1, 'TXT', 'LOW');
SELECT file_id FROM files WHERE file_name = 'after_migration.txt';   -- expect 7
ROLLBACK;
```
The demo scripts also work on the migrated data (they only rely on project_report.pdf, presentation.pptx and harini@example.com). Exception: `v_duplicate_files` is empty because the legacy rows have no content hashes.

To undo: `sqlplus cloudmeta@localhost:1521/FREEPDB1 '@rollback_migration.sql'` (type ROLLBACK), which restores the original five tables.

---

## Chunk 6 and 7 SQL files (read-only unless stated; no schema change)

| File | What it does | Run |
|---|---|---|
| `reports.sql` | the duplicate-file and cold-file reports in SQL, with editable thresholds | `sqlplus cloudmeta@localhost:1521/FREEPDB1 '@reports.sql'` (from `sql`) |
| `seed_reports_config.sql` | optional: adds `cold.max_accesses` and `cold.window_days` to `system_config` (writes two rows; safe to repeat) | `'@seed_reports_config.sql'` |
| `demo_queries.sql` | queries Q1 to Q10 to run live after each section of `scripts/demo.py` | `'@demo_queries.sql'` |
| `explain_reports.sql` | plans of the two report queries (writes only plan-table rows) | `'@explain_reports.sql'` |

The synthetic report sample is created and removed from Python, not SQL: `python -m app.reports.sample_data seed|cleanup`.

## Where I am least sure (check these first if something fails)

1. `SET FEEDBACK ONLY` needs SQL*Plus 12.2 or later.
2. `ALTER TABLE ... MODIFY (col GENERATED BY DEFAULT AS IDENTITY (START WITH LIMIT VALUE))` in the migration.
3. `COUNT` of SEQUENCE objects in verify section 1 (identity sequences may or may not appear as I expect).
4. Exact ORA message wording (e.g. 23ai may add words after `violated`). The constraint name is the reliable part.
5. Identity ids of the demo files (order of an `INSERT ... SELECT` over a join).
6. Whether a bare `ROW_NUMBER()` inside the migration's location `CASE` behaves as intended when the dedupe filter `dup_rn = 1` applies.
7. Statement `FROM files f, provider_regions r JOIN ...` (comma join mixed with ANSI join) used in F03, F04, F05, F29.
8. Optimizer choices in step 5 (predictions only).
