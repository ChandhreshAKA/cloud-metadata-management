# Test report: commands and results tables

**How to use this file.** Run the commands in section 2 on your machine and fill the **"Your run"** columns
from the real output. Paste the raw output files (`test-results\*.txt`, `sql\*.log`) next to this report as
evidence. Nothing in a "Your run" column has been filled in for you.

Two kinds of numbers appear here and they are kept apart:

* **"Recorded at build time"** columns hold only numbers or outcomes that came from a real run during
  development (the source is named). If a cell says **not run**, that run has not happened yet.
* **"Expected"** columns hold what the code or the hand calculation says should happen. They are not results.

Blank cells are blank on purpose. Never fill one from memory or from this document's expectations.

**Updated after chunks 6 and 7.** Sections 2.11 to 2.13 and tables T9 to T12 are new and are blank for your
runs. The totals recorded in T1 date from before chunks 6 and 7 and are now out of date: your run is the only
authority on how many tests exist and pass. The only chunk 6 and 7 results written down in this file are the
deliberate-bug checks in section 4 (run by the assistant, source named).

## 1. Environment record (fill in)

| Item | Command | Your value |
|---|---|---|
| Date of the run | | 31-10-2026|
| Git commit | `git rev-parse --short HEAD` | b2578ab |
| Python | `.\.venv\Scripts\python.exe --version` |Python 3.12.10 |
| Oracle | `SELECT banner FROM v$version;` | Oracle Database 21c Express Edition Release 21.0.0.0.0 - Production (Version 21.3.0.0.0) |
| SQL*Plus | first line printed when `sqlplus` starts | |
| `MASTER_KEY` set in `.env`? | yes or no (never write the key here) |yes |

## 2. Commands

All from `C:\dbmsproject` in PowerShell unless stated. Prerequisites: Oracle is running, the chunk 1 install is
done (`sql\install_fresh.sql`), and `.env` contains `ORACLE_*`, `JWT_SECRET` and a `MASTER_KEY` (without it the
app and the integration tests refuse to start; see `README.md`).

```powershell
cd C:\dbmsproject
New-Item -ItemType Directory -Force test-results | Out-Null     # output folder (git-ignored)
```

### 2.1 Lint
```powershell
.\.venv\Scripts\python.exe -m ruff check . | Tee-Object -FilePath test-results\lint.txt
```

### 2.2 Unit tests with coverage (no Oracle needed)
```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit -v --cov=app --cov-report=term-missing | Tee-Object -FilePath test-results\unit.txt
```

### 2.3 Integration tests with coverage (needs Oracle and `MASTER_KEY`)
```powershell
$env:RUN_INTEGRATION = "1"
.\.venv\Scripts\python.exe -m pytest tests/integration -v --cov=app --cov-report=term-missing | Tee-Object -FilePath test-results\integration.txt
Remove-Item Env:RUN_INTEGRATION
```
These leave `itest_*@example.test` users, their files and audit rows in your database. Remove them any time with
`sql\cleanup_integration_tests.sql` (it truncates `audit_events`; development database only).

### 2.4 One combined coverage figure (unit plus integration)
```powershell
$env:RUN_INTEGRATION = "1"
.\.venv\Scripts\python.exe -m pytest --cov=app --cov-report=term-missing --cov-report=html | Tee-Object -FilePath test-results\combined.txt
Remove-Item Env:RUN_INTEGRATION
```
`htmlcov\index.html` shows line-by-line coverage. In a unit-only run the Oracle repository modules
(`app\db\pool.py`, `app\db\repositories\providers.py`, `files.py`) show low coverage because only the integration
tests execute their SQL; the combined run is the fair figure for them.

### 2.5 What to paste back to me
```powershell
Get-Content test-results\unit.txt -Tail 45
Get-Content test-results\integration.txt -Tail 45
Get-Content test-results\lint.txt
Get-Content test-results\demo.txt -Tail 40      # after section 2.12
```

To fill the "Collected" columns (the number of tests pytest finds per file, not a result):
```powershell
.\.venv\Scripts\python.exe -m pytest --collect-only -q | Select-String "::" | ForEach-Object { ($_ -split "::")[0] } | Group-Object | Sort-Object Name | Format-Table Name, Count
```

### 2.6 Chunk 1 database scripts (from the `sql` folder; each asks for the `cloudmeta` password)
```powershell
cd C:\dbmsproject\sql
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@install_fresh.sql'          # empty schema only; else run drop_all.sql first
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@demo_transactions.sql'
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@demo_constraint_failures.sql'
Get-Content .\demo_constraint_failures.log | Select-String "ORA-"
```

### 2.7 EXPLAIN PLAN, 10,000 rows (the run still to do) and 100,000 rows (to repeat if you wish)
```powershell
cd C:\dbmsproject\sql
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@synthetic_data.sql' 10000
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@explain_plan_demo.sql'
Copy-Item explain_plan_demo.log explain_10k.log

sqlplus cloudmeta@localhost:1521/FREEPDB1 '@synthetic_data.sql' 100000
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@explain_plan_demo.sql'
Copy-Item explain_plan_demo.log explain_100k_rerun.log
```
Read the operation, `Cost`, and the `Buffers` column of the `ALLSTATS LAST` plan in each log. To tidy up afterwards:
`sqlplus cloudmeta@localhost:1521/FREEPDB1 '@cleanup_synthetic.sql'`.

### 2.8 Legacy migration test (DESTRUCTIVE: it replaces the revised schema; do this last)
```powershell
cd C:\dbmsproject\sql
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@drop_all.sql'               # type DROP
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@legacy_schema.sql'          # builds the original 5 tables
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@migrate_legacy.sql'
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@verify_schema.sql'
```
Then restore the normal schema and demo data:
```powershell
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@drop_all.sql'               # type DROP
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@install_fresh.sql'
```
Everything you uploaded and registered through the API is gone after this; that is expected.

### 2.9 Selection scenarios
```powershell
cd C:\dbmsproject
.\.venv\Scripts\python.exe scripts\worked_example.py | Tee-Object -FilePath test-results\worked_example.txt
.\.venv\Scripts\python.exe -m pytest tests/unit/test_selection_worked_example.py tests/unit/test_selection_scenarios.py -v
```

### 2.10 Upload and download
Automated: `pytest tests/unit/test_files_api.py -v`, and `pytest tests/integration/test_oracle_files.py -v` (with
`RUN_INTEGRATION=1`). Manual, over real HTTP: run `scripts\demo.py` (section 2.12) or follow the fallback steps in
`docs\07-report-outline-and-demo.md`, Part 2.

### 2.11 Duplicate and cold-file reports (chunk 6)
The synthetic sample is made-up data (users `rptsyn_*@example.test`, files `SYN_RPT_*`); it never touches your own
files and `cleanup` removes it.
```powershell
cd C:\dbmsproject
.\.venv\Scripts\python.exe -m app.reports.sample_data seed         # prints the expected answer
.\.venv\Scripts\python.exe -m pytest tests/unit/test_reports_logic.py tests/unit/test_api_reports.py -v
$env:RUN_INTEGRATION = "1"
.\.venv\Scripts\python.exe -m pytest tests/integration/test_oracle_reports.py -v | Tee-Object -FilePath test-results\reports_integration.txt
Remove-Item Env:RUN_INTEGRATION
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@sql\reports.sql'        # the same two reports in SQL (read-only)
```
Through the API (the integration test seeds and removes its own copy of the sample; run `seed` first if you use
this and remember `cleanup` afterwards). Use an ADMIN or AUDITOR account so the sample owners' files are visible:
```powershell
$base = "http://127.0.0.1:8000"
$tok = (Invoke-RestMethod -Method Post -Uri "$base/auth/login" -ContentType "application/json" -Body (@{email="auditor@example.com";password="YOUR-AUDITOR-PASSWORD"} | ConvertTo-Json)).access_token
$h = @{ Authorization = "Bearer $tok" }
$d = Invoke-RestMethod -Uri "$base/reports/duplicates" -Headers $h
$d.groups | Where-Object { $_.files[0].file_name -like "SYN_RPT_*" } | ForEach-Object { "{0} | {1} copies | {2} wasted bytes | `${3} | unpriced {4}" -f $_.files[0].file_name, $_.copies, $_.wasted_bytes, $_.estimated_monthly_waste_usd, $_.unpriced_extra_copies }
foreach ($q in "", "?max_accesses=10", "?idle_days=60", "?idle_days=89", "?idle_days=90", "?idle_days=250", "?max_accesses=1", "?window_days=90") {
    "== cold-files$q"
    (Invoke-RestMethod -Uri "$base/reports/cold-files$q" -Headers $h).files | Where-Object { $_.file_name -like "SYN_RPT_*" } | ForEach-Object { "{0} (owner {1})" -f $_.file_name, $_.owner_id }
}
.\.venv\Scripts\python.exe -m app.reports.sample_data cleanup
```

### 2.12 The scripted demo (chunk 7)
```powershell
cd C:\dbmsproject
.\.venv\Scripts\python.exe scripts\demo.py | Tee-Object -FilePath test-results\demo.txt
```
It starts the API itself if none is listening. It exits non-zero if any check fails. The live SQL for each section is
`sql\demo_queries.sql`; to use it, run `scripts\demo.py --pause --keep` and, afterwards, `scripts\demo.py --cleanup-only`.
Optional tests of its helpers and of the two new file endpoints:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_demo_helpers.py tests/unit/test_api_file_views.py -v
$env:RUN_INTEGRATION = "1"
.\.venv\Scripts\python.exe -m pytest tests/integration/test_oracle_file_views.py -v
Remove-Item Env:RUN_INTEGRATION
```

### 2.13 Plans of the two report queries (no index or schema change was made in chunks 6 and 7)
```powershell
cd C:\dbmsproject\sql
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@explain_reports.sql'
```
Read the operation and the `Buffers` column in `explain_reports.log`. For a meaningful plan load
`synthetic_data.sql 100000` first (and run `cleanup_synthetic.sql` afterwards). Nothing has been recorded.

## 3. Results tables

### T1. Suite totals

| Suite | Recorded at build time (source) | Your run: passed | failed | skipped | Notes |
|---|---|---|---|---|---|
| Lint (`ruff check .`) | all checks passed (assistant, final run before the chunk 4 commit) | | | | |
| Unit tests | 370 passed (assistant, same run) | | | | |
| Integration tests | 40 passed (assistant, against your Oracle with a process-only master key) | | | | |
| Coverage, unit only, whole `app` | 92% total (assistant) | | | | |
| Coverage, unit plus integration | not run | | | | |

The rows above were recorded before chunks 6 and 7 and are historical. Chunks 6 and 7 added new test files (T11
and the "Collected" command in section 2.5) and several modules, so the current totals are different: use your own run.

### T2. Coverage by module (fill from `test-results\combined.txt`)

| Module | Statements | Missed | Cover | Notes |
|---|---|---|---|---|
| `app/selection/engine.py` | | | | |
| `app/selection/explain.py` | | | | |
| `app/crypto/service.py` | | | | |
| `app/storage/simulated.py` | | | | |
| `app/files/service.py` | | | | |
| `app/auth/` (all) | | | | |
| `app/providers/service.py` | | | | |
| `app/db/pool.py` | | | | |
| `app/db/repositories/` (all) | | | | |
| `app/reports/service.py` (chunk 6) | | | | |
| `app/db/repositories/reports.py` (chunk 6) | | | | |
| `app/api/routers/reports.py`, `app/api/routers/files.py` | | | | |
| **TOTAL** | | | | |

### T3. Authentication, RBAC and audit

**T3a. Test groups.** "Collected" is the number pytest counts in that file (`pytest --collect-only`), not a result.

| Behaviour proved | Test file | Collected | Your run: passed / failed | Notes |
|---|---|---|---|---|
| Password hashing (Argon2id, salted, bad hash is a failed check) | `tests/unit/test_passwords.py` | 6 | | |
| Token creation, expiry, forged, `alg=none`, tampered | `tests/unit/test_tokens.py` | 12 | | |
| Configuration from the environment only; secrets hidden | `tests/unit/test_config.py` | 10 | | |
| Register, login, wrong password, disabled account, `/auth/me` | `tests/unit/test_api_auth.py` | 18 | | |
| Role matrix through the API; denial audited; forged role ignored | `tests/unit/test_api_rbac.py` | 18 | | |
| Permission table itself; AUDITOR holds no modifying permission | `tests/unit/test_rbac_table.py` | 12 | | |
| Database errors become clean API errors; health check | `tests/unit/test_db_errors.py` | 23 | | |
| SQL injection: validation, bind-only SQL, static scan | `tests/unit/test_sql_injection.py` | 29 | | |
| Same behaviours against real Oracle (round trip, duplicate e-mail 409, audit rows, role matrix, injection, constraints, transactions) | `tests/integration/test_oracle_integration.py` | 13 | | |

The "Collected" numbers in T3a date from chunk 4. `test_sql_injection.py` now collects one more case because its static
scan also covers the new `app/db/repositories/reports.py`; re-collect with the command in section 2.5.

**T3b. Role matrix (expected status code per role).**

| Request | USER | ADMIN | AUDITOR | Your run (USER / ADMIN / AUDITOR) |
|---|---|---|---|---|
| `GET /protected/files` | 200 | 403 | 403 | |
| `GET /protected/providers` | 403 | 200 | 403 | |
| `GET /protected/audit` | 403 | 200 | 200 | |
| `GET /providers` (read) | 200 | 200 | 200 | |
| `POST /providers` (write) | 403 | 201 | 403 | |
| `POST /selection/select` | 200 | 200 | 200 | |
| `POST /files` (upload) | 201 | 403 | 403 | |
| `GET /files/{id}/download` (own file) | 200 | 403 | 403 | |
| `GET /files` (own file list) | 200 | 403 | 403 | |
| `GET /files/{id}/placement` (own file) | 200 | 403 | 403 | |
| `GET /reports/duplicates` | 200 (own files) | 200 (all owners) | 200 (all owners) | |
| `GET /reports/cold-files` | 200 (own files) | 200 (all owners) | 200 (all owners) | |
| `POST /reports/duplicates` (any write method) | 405 | 405 | 405 | |
| any of the above without a token | 401 | 401 | 401 | |

**T3c. Audit events.** After running the integration tests, check what the database recorded:

```sql
SELECT event_type, outcome, COUNT(*) AS n FROM audit_events GROUP BY event_type, outcome ORDER BY event_type, outcome;
```

| Event | Written when | Verified by (unit and Oracle tests) | Your count after the integration run |
|---|---|---|---|
| `USER_REGISTERED` success / failure | account created; duplicate e-mail | `test_api_auth`, `test_oracle_integration` | |
| `LOGIN_OK` / `LOGIN_FAILED` | login | `test_api_auth`, `test_oracle_integration` | |
| `ACCESS_DENIED` | a 403 (role or not the owner) | `test_api_rbac`, `test_files_api`, `test_oracle_files` | |
| `CONFIG_CHANGED` / `PROVIDER_STATUS_CHANGED` | registry changes | `test_api_providers`, `test_oracle_providers` | |
| `FILE_UPLOADED` / `FILE_DELETED` | upload, delete | `test_files_api`, `test_oracle_files` | |

### T4. Chunk 1 database demos

**T4a. Install verification (`install_fresh.sql`, section 1 to 10 of its report).** Source of "recorded": the
log of your run of `install_fresh.sql`, read by the assistant.

| Check | Expected | Recorded at build time | Your run |
|---|---|---|---|
| Tables / views / triggers | 17 / 8 / 1 | matched | |
| Indexes (all kinds) | 43 | matched | |
| Invalid objects, compilation errors | none | matched | |
| Row counts: tiers 4, providers 3, regions 6, pricing 24, weight profiles 4, config 12, probes 18, users 3, files 6, locations 7, access logs 10, all others 0 | as listed | matched | |
| Foreign keys: 27 (8 CASCADE, 1 SET NULL, 18 NO ACTION) | as listed | matched | |
| Constraints by type: C 60, P 17, R 27, U 9 | as listed | matched | |
| Non-constraint indexes: 17 | 17 | matched | |
| `v_unpriced_locations` | no rows | matched | |
| Weight profiles each sum to 1 | 4 rows, total 1 | matched | |

**T4b. Transactions demo (`demo_transactions.sql`).**

| Step | What it shows | Expected | Recorded at build time | Your run |
|---|---|---|---|---|
| T1 | COMMIT makes a change permanent | one row `txn_demo_commit.txt` after the commit | matched | |
| T2 | ROLLBACK discards uncommitted work | 1 visible before, 0 after | matched | |
| T3 | SAVEPOINT and ROLLBACK TO undo part of a transaction | `HIGH, 1` then `LOW, 0`; the file survives the commit | matched | |
| T4 | a failed multi-step upload leaves no orphan metadata | `ORA-02291 FK_LOCATIONS_REGION`, then 0 orphan rows | matched | |
| T5 | ON DELETE CASCADE | location removed with its file | matched | |
| T6 | ON DELETE RESTRICT | `ORA-02292 FK_LOGS_FILE`; file still present | matched | |
| End | cleanup | 0 demo rows left | matched | |

**T4c. Constraint failures (`demo_constraint_failures.sql`).** Every statement is meant to fail except F35.
"Recorded" is from your run of the script on 25 Oct 2026 (log read by the assistant). The first version of the script
had a bug (a trailing `-` swallowed the final `ROLLBACK`, leaving one audit row); the fixed script was rerun and ended with
`audit_rows` 0.

| ID | Expected (from the script) | Recorded at build time | Your run |
|---|---|---|---|
| F01 | ORA-00001 PK_USERS (user_id 1 already exists) | matched | |
| F02 | ORA-00001 UQ_USERS_EMAIL | matched | |
| F03 | ORA-00001 UQ_LOCATIONS_FILE_REGION (same file twice in the same provider-region) | matched | |
| F04 | ORA-00001 UX_LOCATIONS_ONE_PRIMARY (second primary copy for one file) | matched | |
| F05 | ORA-00001 UQ_LOCATIONS_REGION_KEY (same object key in same provider-region, different file) | matched | |
| F06 | ORA-00001 UX_WEIGHTS_ONE_DEFAULT (second default weight profile) | matched | |
| F07 | ORA-01400 cannot insert NULL into USERS.EMAIL | matched | |
| F08 | ORA-01400 cannot insert NULL into FILES.SECURITY_LEVEL | matched | |
| F09 | ORA-01400 cannot insert NULL into PROVIDER_REGIONS.SECURITY_SCORE | matched | |
| F10 | ORA-02290 CK_USERS_EMAIL_LOWER | matched | |
| F11 | ORA-02290 CK_USERS_ROLE | matched | |
| F12 | ORA-02290 CK_USERS_PWD (ACTIVE account without a password hash) | matched | |
| F13 | ORA-02290 CK_FILES_SIZE (negative size) | matched | |
| F14 | ORA-02290 CK_FILES_TYPE (file type outside the allowed list) | matched | |
| F15 | ORA-02290 CK_FILES_SECURITY (invalid security level) | matched | |
| F16 | ORA-02290 CK_FILES_SHA256 (not 64 lowercase hex characters) | matched | |
| F17 | ORA-02290 CK_FILES_DELETED (DELETED status without deleted_at) | matched | |
| F18 | ORA-02290 CK_FILES_NAME (path separator in file name) | matched | |
| F19 | ORA-02290 CK_LOGS_ACTION | matched | |
| F20 | ORA-02290 CK_LOGS_OUTCOME | matched | |
| F21 | ORA-02290 CK_LOGS_LATENCY (negative latency) | matched | |
| F22 | ORA-02290 CK_REGIONS_RELIABILITY (score above 10) | matched | |
| F23 | ORA-02290 CK_WEIGHTS_SUM (weights sum to 1.5, not 1) | matched | |
| F24 | ORA-02290 CK_DECISIONS_WEIGHTS_JSON (not valid JSON) | matched | |
| F25 | ORA-02290 CK_REC_SHAPE (DEDUPLICATE needs related_file_id) | matched | |
| F26 | ORA-02290 CK_PERF_OK_HAS_LATENCY (successful probe must have a latency) | matched | |
| F27 | ORA-02290 CK_AUDIT_TYPE (unknown event type) | matched | |
| F28 | ORA-02291 FK_FILES_OWNER (parent key not found) | matched | |
| F29 | ORA-02291 FK_LOCATIONS_TIER (unknown storage tier) | matched | |
| F30 | ORA-02292 child record found (one of FK_FILES_OWNER, FK_LOGS_USER, ...) - users are never hard-deleted | matched (Oracle named `FK_LOGS_USER`; any child key is a valid answer) | |
| F31 | ORA-02292 FK_LOGS_FILE - a file with access history cannot be hard-deleted | matched | |
| F32 | ORA-02292 child record found (FK_REGIONS_PROVIDER) - a provider with regions cannot be deleted | matched | |
| F33 | ORA-12899 value too large for column FILES.FILE_NAME | matched | |
| F34 | ORA-01438 value larger than specified precision (NUMBER(4,2) holds up to 99.99) | matched | |
| F35 | (setup) the insert succeeds: `1 row created.` (rolled back at the end) | matched | |
| F36 | ORA-20001 audit_events is append-only (UPDATE) | matched | |
| F37 | ORA-20001 audit_events is append-only (DELETE) | matched | |
| end | `Rollback complete.`, then `audit_rows` 0 (users 3 and files 6 on a fresh install without synthetic data) | audit_rows 0 after the rollback fix; at that time users 103 and files 5,006 because the synthetic data was loaded | |

**T4d. Migration test (section 2.8).** Not run during development: there is no recorded result.

| Check | Expected | Recorded at build time | Your run |
|---|---|---|---|
| Legacy state after `legacy_schema.sql`: users 3, providers 3, files 5 (ids 1, 2, 3, 4, 6), locations 3, logs 3 | as listed | not run | |
| Row counts legacy vs new: users 3/3, files 5/5, locations 3/3, logs 3/3 | as listed | not run | |
| Legacy provider rows found as provider-region | 3/3 | not run | |
| Counter vs real downloads: project_report 12 vs 1, presentation 7 vs 1, dataset 2 vs 0, notes 1 vs 0, admin_draft 0 vs 0 | as listed | not run | |
| Mismatched legacy bucket names; NULL security levels; unpriced locations | none | not run | |
| New file id after migration | 7 | not run | |

### T5. EXPLAIN PLAN before and after indexing

Queries: **Q2** history of one file newest first; **Q3** latest 50 actions of one user; **Q4** control (last
download per file; the indexes are not expected to help). "Recorded" values are from your 100,010-row run
(`sql\explain_100k.log`, file_id 34 with 18 log rows, user_id 41 with 1,027 rows).

| Query | Rows | Phase | Operation on `access_logs` | Cost | Buffers | A-Time | Sort? | Source / your run |
|---|---|---|---|---|---|---|---|---|
| Q2 | 10,000 | before | | | | | | **blank: your 10k run** |
| Q2 | 10,000 | after | | | | | | |
| Q3 | 10,000 | before | | | | | | |
| Q3 | 10,000 | after | | | | | | |
| Q4 control | 10,000 | before | | | | | | |
| Q4 control | 10,000 | after | | | | | | |
| Q2 | 100,010 | before | TABLE ACCESS FULL | 207 | 752 | 00:00:00.01 | yes (SORT ORDER BY) | recorded, `explain_100k.log` |
| Q2 | 100,010 | after | INDEX RANGE SCAN DESCENDING IX_LOGS_FILE_TIME, TABLE ACCESS BY INDEX ROWID | 22 | 21 | 00:00:00.01 | no | recorded |
| Q3 | 100,010 | before | TABLE ACCESS FULL | 207 | 752 | 00:00:00.01 | yes (SORT ORDER BY STOPKEY) | recorded |
| Q3 | 100,010 | after | INDEX RANGE SCAN DESCENDING IX_LOGS_USER_TIME, TABLE ACCESS BY INDEX ROWID | 52 | 53 | 00:00:00.01 | no (COUNT STOPKEY) | recorded |
| Q4 control | 100,010 | before | TABLE ACCESS FULL | 212 (whole statement) | 752 | 00:00:00.03 | no (HASH GROUP BY) | recorded |
| Q4 control | 100,010 | after | TABLE ACCESS FULL (unchanged) | 212 | 752 | 00:00:00.02 | no | recorded |
| Q2, Q3, Q4 | 100,010 | before and after | | | | | | **blank: your optional re-run** |

Elapsed times of 0.01 to 0.03 seconds are too small to compare; the operation and the buffer count are the evidence.

### T6. Selection scenarios

"Expected" comes from the hand calculation in `docs\03-selection-engine.md` and the assertions in the tests.
Run section 2.9 and fill "Actual". Prices, scores and latencies are **illustrative**.

| Scenario | Inputs | Expected winner | Expected composite | Cheapest-only baseline | Actual winner (your run) | Actual composite | Match? |
|---|---|---|---|---|---|---|---|
| Worked example, BALANCED | 10 GiB, MEDIUM, HOT, 12 months, 4 reads a month | AWS (ap-south-1) | 0.786364 (Azure 0.675, Google Cloud 0.1) | Azure (Central India), $44.16002662 | | | |
| Worked example, COST_SAVER | same | Azure (Central India) | 0.8 | Azure | | | |
| Worked example, PERFORMANCE | same | AWS (ap-south-1) | 0.890909 | Azure | | | |
| Worked example, SECURE | same | Azure (Central India) | 0.775 | Azure | | | |
| **Cheapest does not win** | worked example, BALANCED | AWS wins although Azure is cheapest | see row 1 | Azure | | | |
| Three-specialist set, COST_SAVER | CheapCo, FastCo, SecureCo; 1 GiB, MEDIUM | CheapCo | 0.600000 | CheapCo | | | |
| Three-specialist set, PERFORMANCE | same | FastCo | 0.650000 | CheapCo | | | |
| Three-specialist set, SECURE | same | SecureCo | 0.827105 | CheapCo | | | |
| Three-specialist set, BALANCED (cheapest not chosen) | same | SecureCo | 0.576711 | CheapCo | | | |
| Cheapest excluded by a hard constraint | same set, HIGH file | CheapCo rejected (reliability 9.2 below the HIGH minimum 9.4) | n/a | not CheapCo | | | |
| Rejected providers do not change the others' scores | worked example plus a DOWN provider and an oversize/low-security one | same ranking, composites and winner as without them | identical | n/a | | | |

### T7. Upload, download and delete

All of these exist as automated tests. "Recorded" means the test passed in the assistant's runs; the real-HTTP row
is a manual run.

| Behaviour | Expected | Unit test (in `test_files_api.py` unless noted) | Oracle test (`test_oracle_files.py`) | Recorded at build time | Your run |
|---|---|---|---|---|---|
| Round trip returns identical bytes (1 byte up to 1 MiB) | bytes equal, SHA-256 equal | `test_round_trip_returns_identical_bytes` | `test_round_trip_and_every_metadata_row` | pass, pass | |
| Stored bytes do not contain the plaintext | no marker; size = plaintext + 16 | `test_stored_bytes_do_not_contain_the_plaintext` | same Oracle test | pass, pass | |
| Database holds only wrapped key and nonce | 60-byte wrapped key, 12-byte nonce | `test_database_holds_only_the_wrapped_key_and_nonce` | same Oracle test | pass, pass | |
| Flip one stored byte | HTTP 502, access log `FAILURE` | `test_flipping_one_stored_byte...` (3 positions) | `test_flipping_one_stored_byte_fails_and_is_logged` | pass, pass | |
| Truncated or extended object | 502 | `test_truncated_or_extended_objects_are_detected` | n/a | pass | |
| Hash rewritten as well, bytes still changed | 502 "could not be decrypted" | `test_an_attacker_who_also_rewrites_the_stored_hash...` | n/a | pass | |
| Wrong recorded plaintext hash | 502 | `test_a_wrong_recorded_plaintext_hash...` | n/a | pass | |
| Stored object missing | 404 | `test_a_missing_stored_object_is_a_clear_404` | `test_a_missing_object_is_a_clear_404` | pass, pass | |
| Oversize file | 413, nothing stored | `test_oversize_file_is_rejected...` and the streaming variant | `test_oversize_file_is_rejected_before_anything_is_stored` | pass, pass | |
| Empty file, unsafe or over-long names | 422, nothing stored | `test_empty_file...`, `test_unsafe_file_names...`, byte-length test | `test_unicode_name_and_the_255_byte_limit` | pass, pass | |
| Wrong user (download, delete) | 403, `ACCESS_DENIED`, access log `DENIED` | `test_another_user_cannot_download_or_delete...` | `test_wrong_user_is_denied_and_the_denial_is_recorded_in_oracle` | pass, pass | |
| ADMIN and AUDITOR cannot read or write file contents | 403 | `test_admin_and_auditor_cannot...` | n/a | pass | |
| **Forced storage failure** | 503, **no metadata** | `test_forced_storage_failure_leaves_no_metadata` | `test_forced_storage_failure_leaves_no_rows_in_oracle` | pass, pass | |
| **Forced database failure after storing** | 500, **no stored object** (put then delete) | `test_forced_database_failure_leaves_no_stored_object`, `..._late_in_the_transaction...` | `test_forced_database_failure_after_storing_removes_the_object` | pass, pass | |
| Cleanup delete also fails | original error returned; `ORPHANED OBJECT` logged | `test_if_the_cleanup_delete_also_fails...` | n/a | pass | |
| Selection recorded with every candidate | 1 decision; rejected candidates carry reasons | `test_the_decision_and_every_candidate_are_stored...` | included in the round-trip test | pass, pass | |
| No provider qualifies | 422 with reasons, nothing stored | `test_when_no_provider_qualifies...` | n/a | pass | |
| Delete needs `confirm=true`, then erases key and object | 400 without; 200 with; then 410 | `test_delete_requires_the_confirm_flag`, `test_confirmed_delete...` | `test_delete_needs_confirm_then_erases...` | pass, pass | |
| Real HTTP, end to end (server, Oracle, temporary storage folder) | upload 201; object on disk = plaintext + 16, no `%PDF`, no marker; download identical; one flipped byte 502; delete 400 then 200; then 410 | n/a | n/a | observed by the assistant, twice: (1) from Python over HTTP, a 20,027-byte file stored as 20,043 bytes, all outcomes as expected; (2) by running the PowerShell commands of `07-report-outline-and-demo.md` exactly as written: a 47-byte file stored as 63 bytes, the marker not found in the stored object, hashes equal, a flipped byte gave the 502 integrity message, a second user got 403, delete without confirm was refused, delete with confirm gave `crypto_erased` true, then download gave 410 and no object was left | |

### T9. Duplicate detection against the seeded data (chunk 6)
Seed with `python -m app.reports.sample_data seed` (section 2.11). "Expected" is the answer key in
`app/reports/sample_data.py` plus the hand calculation shown; it is not a result. Prices are illustrative
(`sql/seed_data.sql`): Azure Central India HOT 0.0240, Google Cloud asia-south1 HOT 0.0230, AWS us-east-1 HOT
0.0250 x 0.92 = 0.0230, per GiB-month.

**T9a. Groups that must be found**

| Group (owner) | Files, original first | Expected copies | Expected wasted bytes | Expected est. monthly waste (hand calculation) | Exp. unpriced extras | Found: copies | wasted bytes | est. monthly waste | unpriced | Match? |
|---|---|---|---|---|---|---|---|---|---|---|
| D1 (A) | `SYN_RPT_dup1_a.bin`, `_b`, `_c` (1 GiB each; AWS, Azure, Google Cloud) | 3 | 2,147,483,648 | 1 GiB x 0.024 + 1 GiB x 0.023 = 0.047000 | 0 | | | | | |
| D2 (A) | `SYN_RPT_dup2_a.csv`, `_b` (1 MiB each, AWS us-east-1) | 2 | 1,048,576 | (1/1024) x 0.023 = 0.0000224609, rounded 0.000022 | 0 | | | | | |
| D3 (B) | `SYN_RPT_dup3_a.bin`, `_b` (512 MiB each; the extra has no location) | 2 | 536,870,912 | 0.000000 (no price for the extra) | 1 | | | | | |

**T9b. Files that must NOT be reported (false-positive traps)**

| Trap | Files | Why it is not a duplicate group | In a reported group? (yes / no; expected no) |
|---|---|---|---|
| near-duplicate | `SYN_RPT_near_a.bin`, `SYN_RPT_near_b.bin` | same size, different content (different SHA-256) | |
| one copy deleted | `SYN_RPT_deldup_a.bin`, `SYN_RPT_deldup_b.bin` | only one live copy remains | |
| same content, two owners | `SYN_RPT_cross_a.bin` (A), `SYN_RPT_cross_b.bin` (B) | duplicates are searched within one owner | |
| no hash | `SYN_RPT_nohash_a.bin`, `SYN_RPT_nohash_b.bin` | a legacy row without a hash cannot be grouped | |

**T9c. Totals**

| Measure | Expected | Your run |
|---|---|---|
| Sample groups found (owners A and B) | 3 | |
| Expected groups missing | 0 | |
| False positives (sample files in a group that should not be) | 0 | |
| Owner A's report: groups / Owner B's report: groups | 2 / 1 | |
| `GET /reports/duplicates` as a new USER who owns no files | 0 groups | |
| `sql\reports.sql` first query: rows for the three sample owners' groups | 3 | |

### T10. Cold-file thresholds against the seeded data (chunk 6)
A file is cold when it has been idle **more than** `idle_days` AND has **fewer than** `max_accesses` successful
downloads in the last `window_days` days (defaults 90, 3, 365). Idle is counted from the last successful download,
or from upload if it was never downloaded. File names below drop the `SYN_RPT_` prefix. Expected sets are from the
answer key and the boundary design (`app/reports/sample_data.py`), not results.

| Setting | Expected, owner A | Expected, owner B | Your run, A | Your run, B | Match? |
|---|---|---|---|---|---|
| defaults (90, 3, 365) | cold1.pdf, cold2.pdf, cold3.pdf, edge91.bin | cold4.zip | | | |
| `max_accesses=10` | defaults + warm.xlsx | cold4.zip | | | |
| `idle_days=60` | defaults + edge90.bin | cold4.zip | | | |
| `idle_days=89` | defaults + edge90.bin | cold4.zip | | | |
| `idle_days=90` | defaults (edge90.bin is idle exactly 90, not more) | cold4.zip | | | |
| `idle_days=250` | cold1.pdf | none | | | |
| `max_accesses=1` | cold2.pdf (a file with one download is not "fewer than 1") | none | | | |
| `window_days=90` | defaults + warm.xlsx | cold4.zip | | | |

Evidence fields to spot-check for `cold1.pdf` (owner A): idle days 300, downloads in the last 30 days 0, downloads in
the window 1, AWS / ap-south-1, class COOL (provider's own name STANDARD_IA), label `candidate for archival review`.
`cold2.pdf`: never downloaded, idle 200 counted from upload. `cold3.pdf`: a failed and a denied download yesterday do
not count, idle 220.

| Evidence field (expected) | Your run |
|---|---|
| cold1: idle 300, 30-day 0, window 1, AWS/ap-south-1 COOL (STANDARD_IA) | |
| cold2: last access empty, idle 200 | |
| cold3: idle 220, 30-day 0 | |
| cold4 (owner B): window downloads 1 (the one 400 days ago is outside 365) | |
| Not listed in any run: hot1.docx, new.txt, delcold.pdf (deleted); warm.xlsx and edge90.bin not in the default run | |

### T11. Tests added in chunks 6 and 7
"Collected" is filled from the section 2.5 command, not typed.

| Behaviour proved | Test file | Collected | Your run: passed / failed |
|---|---|---|---|
| Report arithmetic by hand, rounding, unpriced extras, thresholds precedence, wording, limits stated | `tests/unit/test_reports_logic.py` | | |
| Report endpoints: who sees what, thresholds reach the query, 422 on bad input, read-only (405, no data change) | `tests/unit/test_api_reports.py` | | |
| File list and stored placement endpoints (own files only, deleted hidden, 403 for another user, 410 deleted, read-only) | `tests/unit/test_api_file_views.py` | | |
| Demo helpers (PDF is valid, tables, checks, role change refused for non-demo accounts, http-only client) | `tests/unit/test_demo_helpers.py` | | |
| Report SQL on real Oracle against the synthetic sample (below) | `tests/integration/test_oracle_reports.py` | | |
| File list and placement SQL on real Oracle | `tests/integration/test_oracle_file_views.py` | | |

**Oracle integration tests for the report SQL** (`pytest tests/integration/test_oracle_reports.py -v`; each must pass).

| Test | What it proves | Your run |
|---|---|---|
| `test_every_seeded_duplicate_group_is_found_with_the_right_members` | D1, D2, D3 found; members and wasted bytes as in T9a | |
| `test_no_false_positives_in_duplicates` | exactly D1, D2 and D3; none of the T9b traps | |
| `test_all_owners_view_never_merges_the_same_content_of_two_owners` | the all-owners view still groups per owner | |
| `test_the_first_copy_is_the_oldest_one_and_is_marked_original` | original = oldest upload | |
| `test_duplicate_money_matches_a_hand_calculation_from_the_price_table` | waste = bytes / 2^30 x price of each extra | |
| `test_an_extra_copy_with_no_location_is_counted_but_flagged_unpriced` | bytes counted, $0, flagged | |
| `test_every_seeded_cold_file_is_found_and_no_other` | exact cold sets for owners A and B | |
| `test_cold_evidence_is_correct_for_known_files` | idle days, windows, provider, class, never-downloaded | |
| `test_thresholds_change_the_result_as_expected` | the T10 rows | |
| `test_the_idle_boundary_is_strict_more_than` | idle 91 listed, idle 90 not | |
| `test_a_shorter_window_forgets_older_downloads` | `window_days=90` adds warm.xlsx | |
| `test_deleted_and_young_and_busy_files_are_never_cold` | the cold traps | |
| `test_running_the_reports_changes_no_data` | row counts unchanged | |
| `test_http_user_sees_only_own_files_and_auditor_sees_the_sample` | scope per role over HTTP | |

### T12. One run of `scripts\demo.py` (chunk 7)
Run section 2.12 once on a clean start and fill the header, then the check table from the printed `[PASS]` / `[FAIL]` lines.

| Item | Your value |
|---|---|
| Date and time | |
| Git commit (`git rev-parse --short HEAD`) | |
| Command | `.\.venv\Scripts\python.exe scripts\demo.py` |
| API already running, or started by the script? | |
| Final line `RESULT: N of N checks passed` | |
| Exit code (`$LASTEXITCODE`) | |
| Output saved as | `test-results\demo.txt` |

| # | Check printed by the script (expected PASS with the default options) | Your run |
|---|---|---|
| 1 | GET /health reports the database is up | |
| 2 | demo user logs in with role USER | |
| 3 | demo auditor logs in with role AUDITOR | |
| 4 | selection endpoint answers | |
| 5 | upload accepted (HTTP 201) | |
| 6 | upload chose the same winner as the dry run | |
| 7 | GET /files/{id}/placement returns the explanation saved in the database | |
| 8 | stored object is NOT the PDF (no %PDF header, no marker text) | |
| 9 | download returns exactly the uploaded bytes | |
| 10 | flipping ONE byte of the stored object is detected (HTTP 502) | |
| 11 | after restoring the byte the download works again | |
| 12 | duplicate report finds the demo duplicate | |
| 13 | cold report lists exactly the known cold sample files | |
| 14 | a stricter threshold (idle_days=250) shrinks the list | |
| 15 | USER tries to register a provider: refused (403) | |
| 16 | USER tries to read the audit trail: refused (403) | |
| 17 | AUDITOR tries to register a provider: refused (403) | |
| 18 | AUDITOR tries to download the user's file: refused (403) | |
| 19 | AUDITOR may read the audit trail (allowed, 200) | |
| 20 | no token at all: 401 | |
| 21 | delete the first demo file (key erased, object removed) | |
| 22 | delete the second demo file | |

With `--no-sample` checks 13 and 14 are not printed; with `--keep` checks 21 and 22 are not printed. After the run, in SQL*Plus,
`@sql\demo_queries.sql` (read-only) shows the database side of each section (Q1 to Q10).

### T13. Plans of the report queries (optional; section 2.13)

| Query | Rows in `files` / `access_logs` | Operation on `files` | Operation on `access_logs` | Cost | Buffers | Notes |
|---|---|---|---|---|---|---|
| R1 duplicates | | | n/a | | | |
| R2 cold files | | | | | | |

What changed in the schema, indexes and data in chunks 6 and 7: **nothing in the tables, columns, constraints,
indexes, views or pricing data.** `git diff afc53b3 -- sql/schema.sql sql/indexes.sql sql/indexes_access_logs.sql sql/views.sql sql/seed_data.sql`
prints nothing (rerun it to check). New SQL files only: `sql/reports.sql`, `sql/seed_reports_config.sql` (optional rows
`cold.max_accesses` and `cold.window_days` in `system_config`; the existing `cold.idle_days` is read too),
`sql/demo_queries.sql` and `sql/explain_reports.sql`. The report queries use the existing indexes
(`ix_files_sha256`, `ix_files_owner_time`, `ix_logs_file_time`) only if the optimiser chooses to; whether it does is
exactly what T13 will show.

### T8. Unresolved items (fill after your runs)

| ID | Description | Evidence (file and line of output) | Status |
|---|---|---|---|
| | | | |

## 4. Deliberate-breakage ("mutation") checks recorded at build time

These were run by the assistant during development: a bug is introduced on purpose and the tests must fail. They are
repeatable but not part of the normal suite.

| Area | Bugs introduced | Caught |
|---|---|---|
| Auth and RBAC (chunk 2) | 5 (roles always allowed, SQL built by string, JWT signature unchecked, expiry unchecked, denial not audited) | 5 of 5 |
| Selection engine (chunk 3) | 8 (zero-range guard, direction, boundary, tie-break order twice, a cost term, size filter, weight normalisation) | 8 of 8 (one escaped the first time and its test was strengthened) |
| Files and encryption (chunk 4) | 16 (see `04-storage-encryption.md`) | 16 of 16 (two escaped the first time and tests were added) |
| Report SQL (chunk 6) | 6 (a single file counted as a duplicate group; deleted files counted; owners merged; idle `>=` instead of `>`; failed downloads counted as access; access limit `<=` instead of `<`) | 6 of 6 (one escaped the first time because the tests only ran per owner; an all-owners test was added) |

## 5. Evidence files to keep with the submission

`sql\install_fresh.log`, `sql\demo_transactions.log`, `sql\demo_constraint_failures.log`, `sql\explain_100k.log`,
`sql\explain_10k.log` (your run), `sql\explain_reports.log` (optional), `test-results\*.txt` (including `demo.txt`),
`docs\03-review-package.md`.
