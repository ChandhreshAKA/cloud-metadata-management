# Report outline (one page) and demo checklist

## Part 1. One-page report outline

**Title:** Centralised metadata management and intelligent provider selection for multi-cloud storage.
**Scope statement (put it on page 1):** the database, authentication and access control, the selection engine and
encrypted upload/download are built and tested against a real Oracle database. Cloud storage, prices, latency and
the key-management service are **simulated**. Replication and failover, and monitoring and recommendations (objective 4),
are **designed but not built**. The demo is scripted (`scripts/demo.py`) and there is one optional Streamlit page.

### 1. Problem statement (about 5 lines)
Files kept across AWS, Azure and Google Cloud have fragmented metadata; providers are hard to compare; placement is
opaque (habit or "cheapest"); usage and access are invisible; access control is per provider; one copy is lost when
its provider fails; and duplicate or cold data wastes money. Detail: `00-design.md` section A.

### 2. Gaps and the solution (one compact table)

| Gap in conventional practice | This project's answer | Status |
|---|---|---|
| fragmented metadata | one 3NF Oracle schema (17 tables, 27 foreign keys, 60 CHECK constraints) | built |
| slow queries on large logs | indexes tied to queries; measured before/after at 100,010 rows | built (10k run is yours) |
| cheapest-only or opaque placement | hard filters plus a five-metric weighted score; stored explanation per upload | built |
| weak access control | JWT, USER / ADMIN / AUDITOR permission table, ownership checks, audit trail | built |
| data exposure | AES-256-GCM per-file key, wrapped by a master key | built (KMS simulated) |
| outages | replication and failover | **designed, not built** |
| duplicate and cold-data waste | duplicate and cold-file reports, recommendations | reports **built** (chunk 6, read-only); recommendations **designed, not built** |

### 3. Requirements and how they were met
Functional: register/login, roles, provider registry, upload with explanation, weighted selection, download with
integrity check, soft delete with key destruction, transactions, duplicate-file and cold-file reports (read-only, chunk 6): **met**. Replication, recommendations,
monitoring pages: **not built**. Non-functional: integrity, security (parameterised SQL, secrets
outside the repository), performance (indexes), explainability, testability, honesty about simulated data: **met**;
TLS, lockout and rate limiting: **not built**. Table: `00-design.md` section G.

### 4. System design
Architecture diagram with built / simulated / not-built shading, the upload sequence, module status table, the stack
and the simulated-versus-real table: `00-design.md` sections C to F. Frameworks: FastAPI, `oracledb` (thin mode),
Pydantic, `argon2-cffi`, `PyJWT`, `cryptography`, pytest, ruff. Dependencies are pinned in `requirements.txt`
(`requirements-lock.txt` pins everything).

### 5. Database design (the core of a DBMS report)
Audit of the original 5 tables (candidate keys, functional dependencies, anomalies); the 8 suspected problems and
verdicts; added tables and their 3NF argument; ER diagram; data dictionary; ON DELETE policy; index strategy:
`01-schema-audit.md`, `01-data-dictionary.md`. Demonstrations: constraint failures, transactions, EXPLAIN PLAN.

### 6. Selection engine
Hard constraints, cost formulas, min-max normalisation (all-equal case), direction, weights, tie-break; the worked
example; baselines; caveats (rank reversal, small files): `03-selection-engine.md`, `03-review-package.md`.

### 7. Security and storage
Permission table, audit events, encryption and key wrapping, upload failure rules and the compensating delete,
download verification layers, crypto-erase on delete: `04-storage-encryption.md`.

### 8. Testing and results
Commands and the filled-in results tables: `05-test-report.md` (unit, integration, coverage, database demos, EXPLAIN,
selection scenarios, upload and download). Deliberate-breakage checks per chunk.

### 9. Limitations and objective map
What is simulated, what is not implemented, the plaintext-hash caveat, and the four objectives marked fully,
partially or not covered (objective 4 **not covered**): `06-limitations-and-objectives.md`.

### 10. Conclusion and future work
Built a verified metadata core with explainable selection and encrypted storage. Future work, in order of value:
replication and failover; migration recommendations with saving and payback built on the chunk 6 reports;
real cloud adapters and a real KMS; TLS; key rotation; monitoring pages and charts.

**Evidence to attach:** `sql/install_fresh.log`, `sql/demo_constraint_failures.log`, `sql/demo_transactions.log`,
`sql/explain_100k.log` (and `explain_10k.log` once you have run it), `test-results/*.txt`, `docs/03-review-package.md`.

---

## Part 2. Demo checklist (about 15 minutes)

Use the interactive page at <http://127.0.0.1:8000/docs> for the API steps. Upload uses PowerShell: the raw-body
upload was tested from PowerShell and Python, **not** by clicking in the `/docs` page.

### Before the review (do all of this 30 minutes ahead)
- [ ] Oracle services are running; `sqlplus cloudmeta@localhost:1521/FREEPDB1` connects.
- [ ] `.env` has `ORACLE_*`, `JWT_SECRET` and **`MASTER_KEY`** (the server will not start without it).
- [ ] Your database may still hold the synthetic EXPLAIN data and test users from earlier runs (thousands of extra
      files and 100,000 log rows). For clean counts run, in `C:\dbmsproject\sql`:
      `sqlplus cloudmeta@localhost:1521/FREEPDB1 '@cleanup_synthetic.sql'` and, if you accept losing the audit trail,
      `'@cleanup_integration_tests.sql'` (it truncates `audit_events`; development database only).
- [ ] Activate the demo accounts once (hidden password prompts):
      `.\.venv\Scripts\python.exe scripts\set_password.py admin@example.com` and the same for `auditor@example.com`.
- [ ] `.\.venv\Scripts\python.exe -m pytest tests/unit -q` finishes with all tests passing.
- [ ] Start the server in one window: `.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --port 8000`
- [ ] Keep a SQL*Plus window open as `cloudmeta`, and `docs\05-test-report.md` open with your filled-in tables.
- [ ] Test file ready: `"TOP-SECRET-MARKER this is my private document" | Out-File -Encoding ascii .\secret.txt`

### Steps

| # | Do | Show or say | Expected |
|---|---|---|---|
| 1 | In SQL*Plus: `SELECT table_name FROM user_tables ORDER BY 1;` then `@verify_schema.sql` (from `sql`) | 17 tables, keys, 27 foreign keys with ON DELETE rules, 60 CHECK constraints, 8 views. Open `01-schema-audit.md` for the 3NF reasoning and the `AccessFrequency` finding | no invalid objects; constraint counts as in `05-test-report.md` T4a |
| 2 | Show `sql\demo_constraint_failures.log` and `demo_transactions.log` (or rerun them) | every constraint type rejecting bad data; COMMIT, ROLLBACK, SAVEPOINT | 37 failures with the expected ORA codes |
| 3 | Show `sql\explain_100k.log` (and the 10k log if you made one) | full scan with a sort versus index scan; the control query that does not improve | buffers 752 versus 21 for the file-history query |
| 4 | Open `/docs`; `GET /health` | database up | `{"status":"ok","database":"up"}` |
| 5 | `POST /auth/register`, then `POST /auth/login`; click **Authorize** and paste only the `access_token` | Argon2 hash and JWT; passwords never returned | 201, then a token |
| 6 | As that USER, try `POST /providers` | RBAC: 403, and an `ACCESS_DENIED` row. SQL: `SELECT event_type, outcome FROM audit_events ORDER BY event_id DESC FETCH FIRST 5 ROWS ONLY;` | 403 |
| 7 | Log in as `admin@example.com`, **Authorize**, `GET /providers`; as `auditor@example.com`, `GET /protected/audit` | registry with scores, health and prices; auditor can read but not write | 200 |
| 8 | `POST /selection/select` with `{"file_size_bytes": 1073741824, "security_level": "HIGH"}`; repeat with `"preset": "COST_SAVER"` and with `"weights": {"latency": "0.5"}` | ranked providers, per-metric scores, the explanation, Google Cloud excluded with its reason, `weights_note` when normalised. Stress that prices are **illustrative** | winner changes with the preset |
| 9 | PowerShell upload (commands below) | the response: chosen provider and explanation; **SIMULATED** storage path | 201 |
| 10 | `Get-ChildItem -Recurse storage_sim -File`, `Format-Hex` on the object | ciphertext only: no `TOP-SECRET`, size = plaintext + 16 | no readable text |
| 11 | SQL for this file (below) | file row, key record (wrapped key and nonce only), location, decision and one candidate per provider-region, access log, audit event | rows present |
| 12 | Download and compare hashes (below) | integrity checks pass | `True` |
| 13 | Flip one stored byte, download again | SHA-256 and GCM detect it | HTTP 502 |
| 14 | Register a second user and try to download the file | ownership check | 403, `DENIED` access-log row, `ACCESS_DENIED` audit |
| 15 | `pytest tests/unit/test_files_api.py -v -k "forced"` | forced storage failure leaves no metadata; forced database failure leaves no object | all pass |
| 16 | Delete without, then with `confirm=true`; try to download | crypto-erase: the key row is gone; the object is removed | 400, then 200, then 410 |
| 17 | Close with `06-limitations-and-objectives.md` | what is simulated; replication, monitoring pages and recommendations are **not built**; the objective map | say it before you are asked |

### PowerShell for steps 9, 10, 12, 13, 14, 16
```powershell
cd C:\dbmsproject
$base = "http://127.0.0.1:8000"
$email = "demo$(Get-Random)@example.com"; $pw = "a-long-test-password-1"
Invoke-RestMethod -Method Post -Uri "$base/auth/register" -ContentType "application/json" -Body (@{full_name="Demo";email=$email;password=$pw} | ConvertTo-Json) | Out-Null
$token = (Invoke-RestMethod -Method Post -Uri "$base/auth/login" -ContentType "application/json" -Body (@{email=$email;password=$pw} | ConvertTo-Json)).access_token
$auth = @{ Authorization = "Bearer $token" }

# 9. upload
$up = Invoke-RestMethod -Method Post -Headers $auth -InFile .\secret.txt -ContentType "application/octet-stream" -Uri "$base/files?file_name=secret.txt&security_level=HIGH"
$up | Format-List file_id, provider, region, storage, content_sha256, ciphertext_sha256
$up.selection.explanation

# 10. the stored object is ciphertext
$obj = (Get-ChildItem -Recurse storage_sim -File | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
Select-String -Path $obj -Pattern "TOP-SECRET" -Encoding ascii            # prints nothing
Format-Hex $obj | Select-Object -First 3

# 12. download and compare
Invoke-WebRequest -Headers $auth -OutFile .\back.txt -Uri "$base/files/$($up.file_id)/download"
(Get-FileHash .\secret.txt).Hash -eq (Get-FileHash .\back.txt).Hash       # True

# 13. tamper, then download
$b = [System.IO.File]::ReadAllBytes($obj); $orig = $b.Clone(); $b[5] = $b[5] -bxor 1; [System.IO.File]::WriteAllBytes($obj, $b)
try { Invoke-WebRequest -Headers $auth -Uri "$base/files/$($up.file_id)/download" } catch { $_.ErrorDetails.Message }
[System.IO.File]::WriteAllBytes($obj, $orig)                              # restore

# 14. a second user
$e2 = "other$(Get-Random)@example.com"
Invoke-RestMethod -Method Post -Uri "$base/auth/register" -ContentType "application/json" -Body (@{full_name="Other";email=$e2;password=$pw} | ConvertTo-Json) | Out-Null
$t2 = (Invoke-RestMethod -Method Post -Uri "$base/auth/login" -ContentType "application/json" -Body (@{email=$e2;password=$pw} | ConvertTo-Json)).access_token
try { Invoke-WebRequest -Headers @{Authorization="Bearer $t2"} -Uri "$base/files/$($up.file_id)/download" } catch { $_.Exception.Response.StatusCode.value__ }

# 16. delete
try { Invoke-RestMethod -Method Delete -Headers $auth -Uri "$base/files/$($up.file_id)" } catch { $_.ErrorDetails.Message }
Invoke-RestMethod -Method Delete -Headers $auth -Uri "$base/files/$($up.file_id)?confirm=true"
try { Invoke-WebRequest -Headers $auth -Uri "$base/files/$($up.file_id)/download" } catch { $_.Exception.Response.StatusCode.value__ }
```

### SQL to run live (replace `<id>` with `$up.file_id`)
```sql
SELECT file_id, file_name, file_size_bytes, file_type, security_level, status, SUBSTR(content_sha256,1,16) AS sha FROM files WHERE file_id = <id>;
SELECT algorithm, UTL_RAW.LENGTH(wrapped_dek) AS wrapped_bytes, UTL_RAW.LENGTH(nonce) AS nonce_bytes, master_key_version FROM file_encryption WHERE file_id = <id>;
SELECT r.region_code, sl.tier_code, sl.is_primary, sl.replica_status, sl.object_key FROM storage_locations sl JOIN provider_regions r ON r.region_id = sl.region_id WHERE sl.file_id = <id>;
SELECT d.profile_name, c.region_id, c.excluded_reason, c.total_score FROM selection_decisions d JOIN selection_candidates c ON c.decision_id = d.decision_id WHERE d.file_id = <id> ORDER BY c.total_score DESC NULLS LAST;
SELECT action, outcome, latency_ms FROM access_logs WHERE file_id = <id> ORDER BY log_id;
SELECT event_type, outcome FROM audit_events WHERE target_type = 'FILE' AND target_id = <id> ORDER BY event_id;
-- after the delete
SELECT status, deleted_at FROM files WHERE file_id = <id>;
SELECT COUNT(*) AS key_rows_left FROM file_encryption WHERE file_id = <id>;
```

### Do not claim in the review
Replication, failover, migration recommendations, monitoring pages or charts,
real cloud storage, a real KMS, TLS, or that the prices and latencies are real. If asked, point to
`06-limitations-and-objectives.md`.

### If something fails on the day
Oracle will not start: show the logs in `sql\` and the documents; the unit tests need no database. The server will not
start: read the first error line (a missing `MASTER_KEY` or `.env` value names the variable). Port 8000 busy: use
`--port 8001` and adjust `$base`. A step behaves differently from the table: say so and show the log; do not improvise a result.
