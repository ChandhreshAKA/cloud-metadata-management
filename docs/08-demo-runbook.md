# Demo runbook: every command, in order

All commands in this file were run against the real project while writing it; the "you should see" results are
what came out. Prices, scores and latencies are **illustrative**; the cloud storage is **simulated**.

**You need four things open:** (1) PowerShell window 1 for the server, (2) PowerShell window 2 for the commands,
(3) SQL\*Plus as `cloudmeta`, (4) a browser. About 15 minutes for everything, 10 for the fast path at the end.

---

## Phase 0. Preparation (do this 30 minutes before)

**0.1 Is Oracle running?** (window 2)
```powershell
cd C:\dbmsproject
Get-Service | Where-Object Name -like "Oracle*"          # both should say Running
```

**0.2 Start the server** (window 1; leave it open)
```powershell
cd C:\dbmsproject
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --port 8000
```
Wait for `Application startup complete`.

**0.3 Check it answers** (window 2)
```powershell
Invoke-RestMethod http://127.0.0.1:8000/health            # status ok, database up
Start-Process "http://127.0.0.1:8000/docs"                # opens the interactive page in your browser
```
The page must be opened as `http://` (not `https://`). If it will not open, see Troubleshooting at the end.

**0.4 Paste the helper block** (window 2, once; keep this window open all demo). Replace the two passwords first.
```powershell
cd C:\dbmsproject
$base = "http://127.0.0.1:8000"; $pw = "a-long-test-password-1"
function Get-Token($email, $password) { (Invoke-RestMethod -Method Post -Uri "$base/auth/login" -ContentType "application/json" -Body (@{email=$email;password=$password} | ConvertTo-Json)).access_token }
function New-User($prefix) { $e = "$prefix$(Get-Random)@example.com"; Invoke-RestMethod -Method Post -Uri "$base/auth/register" -ContentType "application/json" -Body (@{full_name=$prefix;email=$e;password=$pw} | ConvertTo-Json) | Out-Null; $e }
function Auth($email, $password = $pw) { @{ Authorization = "Bearer $(Get-Token $email $password)" } }
function Try-Call([scriptblock]$sb) { try { $null = & $sb; "HTTP 2xx OK" } catch { "HTTP " + $_.Exception.Response.StatusCode.value__ + "  " + $_.ErrorDetails.Message } }
function Json($o) { $o | ConvertTo-Json -Depth 5 }
function Sel($o) { Invoke-RestMethod -Method Post -Uri "$base/selection/select" -Headers $hu -ContentType "application/json" -Body (Json $o) }
function Show-Pick($r) { "winner: $($r.selected.provider_code) / $($r.selected.region_code)   score $($r.selected.composite)" }
$hu = Auth (New-User "demo")                                   # a normal user
$ha = Auth "admin@example.com" "YOUR-ADMIN-PASSWORD"           # admin
$hr = Auth "auditor@example.com" "YOUR-AUDITOR-PASSWORD"       # auditor
```
Check it worked: `Invoke-RestMethod "$base/auth/me" -Headers $hu` should show your demo user with role `USER`.

**0.5 SQL\*Plus** (window 3)
```powershell
sqlplus cloudmeta@localhost:1521/FREEPDB1
```

**0.6 Files:** `"TOP-SECRET-MARKER my private document" | Out-File -Encoding ascii .\secret.txt`

---

## Phase 1. The database (objective 1): 4 minutes, in SQL\*Plus

**1.1 The design.**
```sql
SELECT table_name FROM user_tables ORDER BY 1;
SELECT constraint_type, COUNT(*) FROM user_constraints WHERE constraint_name NOT LIKE 'SYS\_C%' ESCAPE '\' GROUP BY constraint_type;
SELECT COUNT(*) FROM user_indexes;
```
You should see 17 tables; constraints C 60, P 17, R 27, U 9; 43 indexes.
Open `docs\01-schema-audit.md`. **Say:** "I audited the original five tables for normal forms. For example the access counter could be worked out from the log, and my own data already disagreed with it, so I replaced it with a view."

**1.2 Bad data is rejected.** Run these one at a time:
```sql
INSERT INTO users (full_name, email, role) VALUES ('x', 'x@x.com', 'SUPERUSER');
INSERT INTO users (full_name, email, role) VALUES ('x', 'harini@example.com', 'USER');
DELETE FROM files WHERE file_name = 'project_report.pdf';
```
You should see `ORA-02290` (role check), `ORA-00001` (duplicate e-mail), `ORA-02292` (the file has history).
Then show all 37 in `sql\demo_constraint_failures.log`: `Get-Content sql\demo_constraint_failures.log | Select-String "ORA-"`.

**1.3 Transactions.**
```sql
INSERT INTO files (owner_id, file_name, file_size_bytes, file_type, security_level) VALUES ((SELECT user_id FROM users WHERE email='harini@example.com'), 'live_demo.txt', 10, 'TXT', 'LOW');
SELECT COUNT(*) FROM files WHERE file_name = 'live_demo.txt';
ROLLBACK;
SELECT COUNT(*) FROM files WHERE file_name = 'live_demo.txt';
```
You should see 1, then 0. **Say:** "Nothing is permanent until COMMIT."

**1.4 Indexes make queries fast.** Open `sql\explain_100k.log` (or `docs\05-test-report.md`, table T5).
You should see a full scan with a sort and 752 reads become an index scan with 21 reads on 100,010 rows; the control query does not improve.

**1.5 Views.** `SELECT * FROM v_file_metadata;`

---

## Phase 2. Login and attacks: 2 minutes, in window 2

```powershell
# duplicate registration
Try-Call { Invoke-RestMethod -Method Post -Uri "$base/auth/register" -ContentType "application/json" -Body (Json @{full_name="X";email="admin@example.com";password=$pw}) }
# wrong password
Try-Call { Invoke-RestMethod -Method Post -Uri "$base/auth/login" -ContentType "application/json" -Body (Json @{email="admin@example.com";password="wrong-password-123"}) }
# SQL injection in the e-mail field, then in the password field
Try-Call { Invoke-RestMethod -Method Post -Uri "$base/auth/login" -ContentType "application/json" -Body (Json @{email="' OR '1'='1' --";password="whatever-123"}) }
Try-Call { Invoke-RestMethod -Method Post -Uri "$base/auth/login" -ContentType "application/json" -Body (Json @{email="admin@example.com";password="' OR '1'='1"}) }
# no token / fake token
Try-Call { Invoke-RestMethod -Uri "$base/providers" }
Try-Call { Invoke-RestMethod -Uri "$base/providers" -Headers @{Authorization="Bearer abc.def.ghi"} }
```
You should see `409 That e-mail address is already registered`; `401 Invalid e-mail or password`; `422 Invalid request`; `401 Invalid e-mail or password`; `401 Not authenticated`; `401 Invalid token`.
**Say:** "Passwords are hashed with Argon2. Wrong password and unknown user give the same message, so nothing leaks. SQL is always parameterised, so injection strings are just data."

---

## Phase 3. Roles (objective 3): 2 minutes

```powershell
(Invoke-RestMethod -Uri "$base/providers" -Headers $hu).provider_code                       # USER can read: AWS AZURE GCP (and any provider you added)
Try-Call { Invoke-RestMethod -Method Post -Uri "$base/providers" -Headers $hu -ContentType "application/json" -Body (Json @{provider_code="NEWCO";provider_name="NewCo";max_object_bytes=1000}) }   # USER blocked
Try-Call { Invoke-RestMethod -Uri "$base/protected/audit" -Headers $hu }                      # USER cannot read audit
Try-Call { Invoke-RestMethod -Uri "$base/protected/audit" -Headers $ha }                      # ADMIN can
Try-Call { Invoke-RestMethod -Uri "$base/protected/files" -Headers $ha }                      # ADMIN cannot manage files
Try-Call { Invoke-RestMethod -Uri "$base/protected/audit" -Headers $hr }                      # AUDITOR can read
Try-Call { Invoke-RestMethod -Method Post -Uri "$base/providers" -Headers $hr -ContentType "application/json" -Body (Json @{provider_code="NEWCO";provider_name="NewCo";max_object_bytes=1000}) }   # AUDITOR cannot change
```
You should see: `AWS AZURE GCP`, then `403`, `403`, `2xx OK`, `403`, `2xx OK`, `403`.
**Say:** "Three roles. Admin manages providers but cannot read files. The auditor can read the audit trail but change nothing. Every refusal is logged."

---

## Phase 4. The selection engine (objective 2): 4 minutes

**4.1 The worked example with every number** (window 2):
```powershell
.\.venv\Scripts\python.exe scripts\worked_example.py
```
Point at `RESULT (BALANCED)`: it says "It is NOT the cheapest eligible option". AWS wins (0.786364) although Azure is cheaper. Under COST_SAVER and SECURE, Azure wins.

**4.2 Live, on the real database:**
```powershell
$r = Sel @{file_size_bytes=1073741824;security_level="MEDIUM"}; Show-Pick $r; $r.explanation
$r = Sel @{file_size_bytes=1073741824;security_level="HIGH"};   Show-Pick $r
$r.rejected | ForEach-Object { "rejected: $($_.provider_code) $($_.region_code): $($_.reasons[0])" }
foreach ($p in "BALANCED","COST_SAVER","PERFORMANCE","SECURE") { $r = Sel @{file_size_bytes=1073741824;security_level="MEDIUM";preset=$p}; "$($p.PadRight(12)) " + (Show-Pick $r) }
$r = Sel @{file_size_bytes=1073741824;security_level="MEDIUM";weights=@{latency=0.5}}; $r.weights_note
$r = Sel @{file_size_bytes=6597069766656;security_level="LOW"}; ($r.ranking | ForEach-Object { $_.provider_code }) | Sort-Object -Unique
Try-Call { Sel @{file_size_bytes=1000;security_level="LOW";weights=@{latency=-1}} }
```
You should see:
- MEDIUM: `AZURE / Central India` score 0.643032, with an explanation.
- HIGH: `AZURE / East US`, and both Google Cloud regions rejected: "security score 9.1 is below the HIGH minimum 9.2".
- Presets: BALANCED and PERFORMANCE and SECURE give `AZURE / Central India`; COST_SAVER gives `AZURE / East US`.
- Custom weight: "weights summed to 1.3, so each was divided by 1.3 to sum to 1".
- 6 TiB file: only `AZURE` (the others exceed their size limit).
- Negative weight: `422 weight for latency is negative`.

**4.3 Everything side by side:**
```powershell
$c = Invoke-RestMethod -Method Post -Uri "$base/selection/compare" -Headers $hu -ContentType "application/json" -Body (Json @{file_size_bytes=1073741824;security_level="MEDIUM"})
$c.candidates | ForEach-Object { "{0,-6} {1,-14} eligible={2}  total cost {3}" -f $_.provider_code, $_.region_code, $_.eligible, $_.cost.total }
```
**Say:** "Hard rules first, then a weighted score. The winner changes with the weights. For a very small file the differences are tiny, so cost can outweigh speed; that is a documented limitation."

---

## Phase 5. Admin changes and the engine reacts: 2 minutes

```powershell
Invoke-RestMethod -Method Patch -Uri "$base/providers/1" -Headers $ha -ContentType "application/json" -Body (Json @{is_active=$false}) | Select-Object provider_code, is_active
$r = Sel @{file_size_bytes=1073741824;security_level="MEDIUM"}; Show-Pick $r
($r.rejected | Where-Object { $_.provider_code -eq "AWS" })[0].reasons
Invoke-RestMethod -Method Patch -Uri "$base/providers/1" -Headers $ha -ContentType "application/json" -Body (Json @{is_active=$true}) | Select-Object provider_code, is_active   # PUT IT BACK

Invoke-RestMethod -Method Patch -Uri "$base/regions/6" -Headers $ha -ContentType "application/json" -Body (Json @{health_status="DOWN"}) | Select-Object region_code, health_status
$r = Sel @{file_size_bytes=1073741824;security_level="LOW"}
($r.rejected | Where-Object { $_.region_id -eq 6 })[0].reasons
Invoke-RestMethod -Method Patch -Uri "$base/regions/6" -Headers $ha -ContentType "application/json" -Body (Json @{health_status="UP"}) | Select-Object region_code, health_status    # PUT IT BACK
```
You should see AWS `is_active False`, AWS rejected with "provider is disabled"; then region 6 `DOWN`, rejected with "provider-region is currently DOWN". **Always run the two "PUT IT BACK" lines.**
**Say:** "Marking a provider disabled or down stops new files going there. Replication, which would keep files available during an outage, is designed but not built."

---

## Phase 6. Files: encrypt, store, verify, tamper, delete: 4 minutes

**6.1 Upload.**
```powershell
$up = Invoke-RestMethod -Method Post -Headers $hu -InFile .\secret.txt -ContentType "application/octet-stream" -Uri "$base/files?file_name=secret.txt&security_level=HIGH"
$up | Format-List file_id, provider, region, storage
$up.selection.explanation
```
You should see a provider and region chosen, and a `storage` line starting `SIMULATED local folder`.

**6.2 The stored file is unreadable.**
```powershell
$obj = (Get-ChildItem -Recurse storage_sim -File | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
(Get-Item $obj).Length                                                    # the text length + 16
Select-String -Path $obj -Pattern "TOP-SECRET" -Encoding ascii            # prints NOTHING
Format-Hex $obj | Select-Object -First 3                                  # random-looking bytes
```
**Say:** "The provider only ever receives ciphertext."

**6.3 Download is identical.**
```powershell
Invoke-WebRequest -UseBasicParsing -Headers $hu -OutFile .\back.txt -Uri "$base/files/$($up.file_id)/download" | Out-Null
(Get-FileHash .\secret.txt).Hash -eq (Get-FileHash .\back.txt).Hash       # True
```

**6.4 Tampering is caught.**
```powershell
$b = [System.IO.File]::ReadAllBytes($obj); $orig = $b.Clone(); $b[5] = $b[5] -bxor 1; [System.IO.File]::WriteAllBytes($obj, $b)
Try-Call { Invoke-WebRequest -UseBasicParsing -Headers $hu -Uri "$base/files/$($up.file_id)/download" }
[System.IO.File]::WriteAllBytes($obj, $orig)                              # restore it
```
You should see `502 integrity check failed: the stored object was modified (ciphertext hash mismatch)`.

**6.5 Other users are blocked.**
```powershell
$h2 = Auth (New-User "other")
Try-Call { Invoke-WebRequest -UseBasicParsing -Headers $h2 -Uri "$base/files/$($up.file_id)/download" }     # another user
Try-Call { Invoke-WebRequest -UseBasicParsing -Headers $ha -Uri "$base/files/$($up.file_id)/download" }     # even the admin
```
You should see `403 you do not have access to this file`, then `403 You do not have permission to do this`.

**6.6 Bad uploads are refused.**
```powershell
"" | Out-File -Encoding ascii -NoNewline .\empty.txt
Try-Call { Invoke-RestMethod -Method Post -Headers $hu -InFile .\empty.txt -ContentType "application/octet-stream" -Uri "$base/files?file_name=empty.txt&security_level=LOW" }
Try-Call { Invoke-RestMethod -Method Post -Headers $hu -InFile .\secret.txt -ContentType "application/octet-stream" -Uri "$base/files?file_name=..%2Fevil.txt&security_level=LOW" }
Try-Call { Invoke-RestMethod -Method Post -Headers $hu -InFile .\secret.txt -ContentType "application/octet-stream" -Uri "$base/files?file_name=x.txt" }
```
You should see `422 file is empty`, `422 file name must not contain / or \`, `422 Invalid request` (security level missing).
For "too large", a 50 MiB upload cannot be shown live in PowerShell (the server refuses early and closes the connection). Show it with the automated test instead: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_files_api.py -v -k "oversize"`.

**6.6b Failures leave no mess.**
```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_files_api.py -v -k "forced or cleanup_delete"
```
**Say:** "If storing fails, nothing is written to the database. If the database write fails after storing, the stored file is deleted again."

**6.7 Delete needs confirmation and erases the key.**
```powershell
Try-Call { Invoke-RestMethod -Method Delete -Headers $hu -Uri "$base/files/$($up.file_id)" }                  # refused
Invoke-RestMethod -Method Delete -Headers $hu -Uri "$base/files/$($up.file_id)?confirm=true"                  # crypto_erased true
Try-Call { Invoke-WebRequest -UseBasicParsing -Headers $hu -Uri "$base/files/$($up.file_id)/download" }       # gone
@(Get-ChildItem -Recurse storage_sim -File).Count                                                             # 0
```
You should see `400 deleting is permanent: send confirm=true`, then `crypto_erased: true, object_removed: true`, then `410 file ... has been deleted`, then `0`.

---

## Phase 7. The database recorded all of it: 1 minute, in SQL\*Plus

```sql
SELECT action, outcome, latency_ms FROM access_logs ORDER BY log_id DESC FETCH FIRST 8 ROWS ONLY;
SELECT event_type, outcome FROM audit_events ORDER BY event_id DESC FETCH FIRST 12 ROWS ONLY;
SELECT COUNT(*) AS key_rows_left FROM file_encryption WHERE file_id = <file_id>;
SELECT d.profile_name, c.region_id, c.excluded_reason, c.total_score FROM selection_decisions d JOIN selection_candidates c ON c.decision_id = d.decision_id WHERE d.file_id = <file_id> ORDER BY c.total_score DESC NULLS LAST;
```
(`<file_id>` is `$up.file_id`.) You should see `DENIED`, `FAILURE` and `SUCCESS` access-log rows; audit events such as `FILE_UPLOADED`, `ACCESS_DENIED`, `FILE_DELETED`, `PROVIDER_STATUS_CHANGED`, `LOGIN_FAILED`; **0** key rows after the delete; and one candidate row per provider-region with its score or rejection reason.

---

## Phase 8. Close honestly: 1 minute

Open `docs\06-limitations-and-objectives.md` and **say it before you are asked**:
- The cloud is **simulated** (local folders, made-up prices and latencies).
- **Not built:** replication and failover, duplicate and cold-file reports, monitoring (objective 4), a dashboard, TLS.
- Objectives: 1 and 2 fully covered, 3 partly (security yes, availability no), 4 not covered.

---

## The 10-minute fast path

Phase 0 (already done), then: **1.1, 1.2, 1.3, 1.4, 4.1, 4.2 (MEDIUM, HIGH and presets), Phase 3 (first three lines), 6.1, 6.2, 6.3, 6.4, 6.5 (first line), 6.7, then Phase 8.**

## Clean up afterwards

```powershell
Remove-Item .\secret.txt, .\back.txt, .\empty.txt -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force .\storage_sim -ErrorAction SilentlyContinue
```
In SQL\*Plus, remove any provider you added by hand (for example one you registered from the `/docs` page):
```sql
DELETE FROM cloud_providers WHERE provider_code = 'HELLOWORLD';
COMMIT;
```
Check nothing is left switched off after Phase 5:
```sql
SELECT provider_code, is_active FROM cloud_providers;
SELECT region_id, health_status, is_active FROM provider_regions;
```
All should be `Y` and `UP`. To remove the demo users and files, run `sql\cleanup_integration_tests.sql` (it clears the whole audit trail: development database only).

## Troubleshooting

| What you see | Meaning and fix |
|---|---|
| Browser cannot open `http://127.0.0.1:8000/docs` | Use `Start-Process "http://127.0.0.1:8000/docs"`; type `http://` not `https://`; try `http://localhost:8000/docs`; turn off a VPN or proxy; the page only works on the computer running the server |
| `Unable to connect to the remote server` | the server in window 1 is not running; start it again |
| `Environment variable ... is not set` when starting | `.env` is missing that value (`MASTER_KEY` is required) |
| `[WinError 10048]` when starting | port 8000 is in use; use `--port 8001` and set `$base` to match |
| `401 Invalid token` | the token was copied wrongly or expired (30 minutes); log in again and copy only the text after `access_token` |
| `503` or `degraded` | Oracle is not running |
| `The term 'Auth' is not recognized` | paste the helper block (0.4) again; it only lives in the window you pasted it into |
| `Invoke-WebRequest : Object reference not set` | add `-UseBasicParsing` |
