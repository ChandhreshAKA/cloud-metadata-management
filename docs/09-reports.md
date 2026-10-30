# Chunk 6: duplicate-file and cold-file reports

**Status: BUILT and verified** against Oracle 26ai Free with a seeded **synthetic** sample. Both reports are
**read-only**: they list findings and never move, merge, archive or delete anything. All prices come from
`provider_pricing` and are **illustrative**, not real cloud quotes. GB means GiB (2^30 bytes) everywhere.

Skipped on purpose (as the chunk prompt said): saving estimates, migration cost, payback, a recommendation
table, an approval workflow, usage and latency dashboards. Replication and failover (chunk 5) are still not built.

## 1. What each report does

### Duplicate files
* Groups a user's live files (status not `DELETED`, hash present) by **plaintext SHA-256**.
* A group needs 2 or more files. The **oldest** upload is the original (kept); the rest are **extra copies**.
* Per group it reports: copies, extra copies, **wasted bytes** (sum of the extras' sizes), **estimated monthly
  waste** (for each extra: `bytes / 2^30 x storage price per GiB-month` of that copy's primary location, summed and
  rounded half-up to 6 decimals), and `unpriced_extra_copies` (an extra with no priced location adds bytes but
  adds **$0**, and is flagged, so the money is a floor, never a guess).
* **Near-duplicates are NOT detected** (a file with one byte changed has a different hash). The report says so.
* **Duplicates are searched within one owner's files.** The same content owned by two users is not reported:
  each file has its own key and owner, so it cannot be merged safely, and reporting it would reveal what other
  users store.

### Cold files ("candidate for archival review")
A file is listed when **both** are true (both strict):

| Rule | Default | Where it is set |
|---|---|---|
| idle for **more than** `idle_days` days | 90 | request `?idle_days=`, else `system_config` `cold.idle_days` (already seeded as 90), else 90 |
| **fewer than** `max_accesses` successful downloads in the last `window_days` days | 3 | `?max_accesses=`, else `cold.max_accesses`, else 3 |
| window for counting downloads | 365 | `?window_days=`, else `cold.window_days`, else 365 |

* "Access" means a **successful download**. Uploads, failed and denied attempts do not count (the same definition
  as the view `v_file_access_stats`).
* Idle days = whole days since the last successful download, or since **upload** if it was never downloaded.
* Each row shows: last access, idle days, downloads in the last 30 days, downloads in the window, current provider,
  region, storage class (and the provider's own class name), the label `candidate for archival review`, and a
  sentence of evidence. Nothing is changed.
* The window is separate from the idle limit on purpose: with only "idle > 90 days", a download count over a
  shorter window would always be 0 and the second threshold would do nothing. With a 365-day window, a file that
  was used heavily 4 months ago is not called cold just because it is quiet this quarter.

## 2. Who can run it

| Role | What the endpoints return |
|---|---|
| USER | only their own files |
| ADMIN, AUDITOR | every owner's files (metadata only; contents stay unreadable to them) |

`GET /reports/duplicates` and `GET /reports/cold-files`. POST/PUT/PATCH/DELETE return 405. Bad thresholds return
422. No token returns 401.

## 3. Where the code is

| What | File |
|---|---|
| the SQL (bind variables only, SELECT only) | `app/db/repositories/reports.py` |
| the same logic for SQL*Plus with editable thresholds | `sql/reports.sql` |
| money arithmetic, thresholds, labels, notes | `app/reports/service.py` |
| endpoints and per-role scope | `app/api/routers/reports.py`, `report_scope` in `app/auth/deps.py` |
| synthetic sample (seed, answer key, cleanup) | `app/reports/sample_data.py` |
| optional config rows `cold.max_accesses`, `cold.window_days` | `sql/seed_reports_config.sql` |
| tests | `tests/unit/test_reports_logic.py`, `test_api_reports.py`, `tests/integration/test_oracle_reports.py` |

## 4. The synthetic sample (all made up)

`python -m app.reports.sample_data seed` creates two sample owners (`rptsyn_a@example.test`,
`rptsyn_b@example.test`, no password, so nobody can log in as them) and 25 files named `SYN_RPT_*`. Every
timestamp is "N days before the moment you ran seed", so the same answers hold on any day. It is safe to run again
(it removes the old sample first) and `cleanup` removes it.

| Group | Files | Expected |
|---|---|---|
| D1 (owner A) | `dup1_a/b/c`, 1 GiB each, in AWS, Azure and GCP | 3 copies, 2,147,483,648 bytes wasted |
| D2 (owner A) | `dup2_a/b`, 1 MiB each | 2 copies, 1,048,576 bytes wasted |
| D3 (owner B) | `dup3_a/b`, 512 MiB; the extra has **no location** | 2 copies, 536,870,912 bytes wasted, 1 unpriced extra |
| must NOT be reported | `near_a/b` (same size, different content); `deldup_a/b` (one deleted); `cross_a/b` (same content, two owners); `nohash_a/b` (no hash) | not in any group |
| cold (owner A) | `cold1.pdf` (idle 300), `cold2.pdf` (never downloaded, 200), `cold3.pdf` (a failed and a denied attempt yesterday, still idle 220), `edge91.bin` (91) | listed |
| cold (owner B) | `cold4.zip` (a 400-day-old download is outside the window; one 120 days ago is inside) | listed |
| must NOT be cold | `hot1.docx` (5 recent downloads), `warm.xlsx` (idle 100 but 5 downloads), `edge90.bin` (exactly 90, not more than 90), `new.txt` (10 days old), `delcold.pdf` (deleted) | not listed |

How the answer changes with thresholds (owner A): `max_accesses=10` adds `warm.xlsx`; `idle_days=60` adds
`edge90.bin`; `idle_days=250` leaves only `cold1.pdf`; `max_accesses=1` leaves only `cold2.pdf` (a file with one
download is not "fewer than 1").

## 5. Commands (PowerShell, from `C:\dbmsproject`)

```powershell
# 1. create the synthetic sample (prints what to expect)
.\.venv\Scripts\python.exe -m app.reports.sample_data seed

# 2. the SQL by hand (read-only). Edit the three DEFINE numbers at the top to try thresholds
sqlplus cloudmeta@localhost:1521/FREEPDB1
SQL> @sql/reports.sql

# 3. tests
.\.venv\Scripts\python.exe -m pytest tests/unit -q
$env:RUN_INTEGRATION = "1"
.\.venv\Scripts\python.exe -m pytest tests/integration/test_oracle_reports.py -v

# 4. through the API as ADMIN (sees the sample owners too)
$base = "http://127.0.0.1:8000"
$tok = (Invoke-RestMethod -Method Post -Uri "$base/auth/login" -ContentType "application/json" -Body (@{email="admin@example.com";password="YOUR-ADMIN-PASSWORD"} | ConvertTo-Json)).access_token
$h = @{ Authorization = "Bearer $tok" }
$d = Invoke-RestMethod -Uri "$base/reports/duplicates" -Headers $h
$d.groups | Where-Object { $_.files[0].file_name -like "SYN_RPT_*" } | ForEach-Object { "{0} copies, {1} wasted bytes, ${2}/month, unpriced extras {3}" -f $_.copies, $_.wasted_bytes, $_.estimated_monthly_waste_usd, $_.unpriced_extra_copies }
$c = Invoke-RestMethod -Uri "$base/reports/cold-files" -Headers $h
$c.files | Where-Object { $_.file_name -like "SYN_RPT_*" } | Select-Object file_name, idle_days, downloads_in_window, provider, region, storage_class, label
Invoke-RestMethod -Uri "$base/reports/cold-files?idle_days=250" -Headers $h | Select-Object -ExpandProperty files | Where-Object { $_.file_name -like "SYN_RPT_*" } | Select-Object file_name, idle_days

# 5. remove the sample
.\.venv\Scripts\python.exe -m app.reports.sample_data cleanup
```

Optional: `@sql/seed_reports_config.sql` adds `cold.max_accesses` and `cold.window_days` to `system_config`
(without it the same values are built-in defaults).

## 6. Results of my verification run (real Oracle 26ai Free, real code)

These came from commands I ran while building this chunk. **Re-run them yourself for your own report.**

| Check | Result |
|---|---|
| unit tests (whole suite) | **402 passed** (370 before, 32 new for this chunk) |
| Oracle integration tests (whole folder) | **54 passed** (40 before, 14 new) |
| lint (`ruff check .`) | all checks passed |
| `sql/reports.sql` executed on the seeded sample | group D1: 3 copies, 2147483648 bytes, **0.047** USD/month; D3: 536870912 bytes, no price, 1 unpriced extra; D2: 1048576 bytes, **0.000022**; cold list: `cold1` 300 idle, `cold3` 220, `cold2` 200, `cold4` 120, `edge91` 91 |
| hand check of D1 | extras are Azure Central India HOT (0.024) and GCP asia-south1 HOT (0.023), 1 GiB each: 0.024 + 0.023 = **0.047** |
| deliberate-bug check (6 deliberate SQL mistakes, one at a time) | **6 of 6 caught** by the integration tests: single file counted as a group; deleted files counted; owners merged; idle `>=` instead of `>`; failed downloads counted; access `<=` instead of `<` |

The unit tests prove the arithmetic (worked by hand in the test comments), the thresholds' precedence, the role
scoping, the wording and that the endpoints are read-only. They use canned rows and do **not** prove the SQL; the
Oracle integration tests do that against the answer key above.

## 7. Limits (also written into every report response)

* Exact duplicates only, within one owner. Near-duplicates and cross-owner copies are not reported.
* The hash is a plaintext hash stored in the database (limitation L-1 in `06-limitations-and-objectives.md`).
* Prices are illustrative; an extra copy with no priced location adds bytes but no money.
* "Cold" is a rule on download history, not a prediction. It is a candidate for a human to review.
* A file at an exact boundary is classified by whole days (a file idle 90 days and 23 hours is idle 90, not more
  than 90).
* Not built: recommendations, savings and payback estimates, migration, a report history, a dashboard.

## 8. Found while building

* A Python `datetime` bound to an Oracle query loses its fractions of a second (it binds as `DATE`). That could
  place a file at an exact day boundary in the wrong day. The query now binds `as_of` as a full `TIMESTAMP`.
* `:size` is a reserved word in Oracle (ORA-01745); the sample seeder uses `:size_bytes`.
