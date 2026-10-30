# Multi-Cloud Metadata Manager

Course project (DBMS): a central Oracle metadata store plus an API for authentication, role-based
access control and auditing of multi-cloud file storage. Providers are **simulated** (see Status).

## Status (chunks 1 to 4 and 6 built; documentation set complete; chunks 5 and 7 not built)

| Area | State |
|---|---|
| Oracle schema, migration, demos, indexes (`sql/`) | Done and executed on Oracle 26ai Free (Chunk 1) |
| Config, DB layer, register, login (Argon2 + JWT), RBAC, audit, health | **Built and verified.** 127 unit tests pass (fake database); 13 integration tests pass against Oracle 26ai Free (run by the project owner, 1.93 s) |
| Provider registry (admin-only writes) and the selection engine, dry-run endpoints, worked example, baselines | **Built and verified.** 275 unit tests; 29 integration tests pass against Oracle (run at build time). See `docs/03-selection-engine.md` |
| Encrypted upload, download and delete to **SIMULATED** local-folder storage (AES-256-GCM, selection engine called on every upload) | **Built and verified.** 370 unit tests; 40 integration tests pass against Oracle; real-HTTP end-to-end run done. See `docs/04-storage-encryption.md` |
| Read-only duplicate-file and cold-file reports (Chunk 6) with a seeded **synthetic** sample | **Built and verified.** 402 unit tests; 54 integration tests pass against Oracle; six deliberate SQL bugs all caught. See `docs/09-reports.md` |
| Test report, limitations, objective map, report outline, demo checklist | **Written** (`docs/05` to `07`); the results tables are for you to fill from your own runs |
| Monitoring, recommendations, savings and migration (objective 4), dashboard, scripted demo | **Not built** |
| Replication, failover, migration workflow, real cloud adapters, TLS, key rotation, orphan sweep | **Not built** |
| Account lockout, password reset, token revocation, rate limiting | Deliberately not built |

## Prerequisites

* Windows, PowerShell, Python 3.12 (3.11+ should work)
* Oracle Database Free installed and `sql/install_fresh.sql` already run (see `docs/01-run-guide.md`)
* **No Oracle Instant Client is needed.** The `oracledb` driver runs in *thin mode* (pure Python).
  Instant Client would only be needed for "thick mode" features, which this project does not use.

## Setup

```powershell
cd C:\dbmsproject
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Edit `.env` (it is git-ignored). Set at least `ORACLE_PASSWORD`, a real `JWT_SECRET` and a `MASTER_KEY`
(the app will not start without a valid `MASTER_KEY`; **back it up: losing it makes every stored file unreadable**):

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

### Environment variables

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `ORACLE_DSN` | yes | none | e.g. `localhost:1521/FREEPDB1` |
| `ORACLE_USER` | yes | none | `cloudmeta` |
| `ORACLE_PASSWORD` | yes | none | the schema user's password |
| `JWT_SECRET` | yes | none | at least 32 characters; refused in `production` if it still contains the example placeholder |
| `JWT_ALGORITHM` | no | `HS256` | `HS256`, `HS384` or `HS512` |
| `JWT_EXPIRY_MINUTES` | no | `30` | token lifetime |
| `DB_POOL_MIN` / `DB_POOL_MAX` | no | `1` / `5` | connection pool size |
| `MASTER_KEY` | yes | none | 32 random bytes as base64; wraps each file's data key (stands in for a cloud KMS) |
| `MASTER_KEY_VERSION` | no | `1` | recorded with each file; only this version can decrypt |
| `MAX_UPLOAD_BYTES` | no | `52428800` | uploads larger than this are refused (50 MiB) |
| `STORAGE_ROOT` | no | `storage_sim` | folder for the SIMULATED provider storage (git-ignored) |
| `APP_ENV` | no | `dev` | `dev`, `test` or `production` |
| `RUN_INTEGRATION` | no | `0` | set to `1` to run `tests/integration` |

The application reads **environment variables only**. `python-dotenv` merely loads `.env` into the
environment when the server starts.

## Run the checks

```powershell
# Unit tests (no Oracle needed; use an in-memory fake database)
.\.venv\Scripts\python.exe -m pytest tests/unit -q

# Lint and format check
.\.venv\Scripts\python.exe -m ruff check .

# Unit tests with coverage
.\.venv\Scripts\python.exe -m pytest tests/unit -q --cov=app --cov-report=term-missing
```

**What the unit tests do and do not prove.** They exercise the application logic against a fake
database. They do **not** prove the SQL runs on Oracle or that Oracle enforces constraints. For that,
run the integration tests yourself (they create `itest_*@example.test` users):

```powershell
$env:RUN_INTEGRATION = "1"
.\.venv\Scripts\python.exe -m pytest tests/integration -v
Remove-Item Env:RUN_INTEGRATION
```

(`.env` must contain valid `ORACLE_*` and `JWT_SECRET` values; the tests load it.)

## Start the API

First activate the seeded demo accounts (one time; the password is typed at a hidden prompt):

```powershell
.\.venv\Scripts\python.exe scripts\set_password.py admin@example.com
.\.venv\Scripts\python.exe scripts\set_password.py auditor@example.com
.\.venv\Scripts\python.exe scripts\set_password.py harini@example.com
```

Then:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --port 8000
```

Open <http://127.0.0.1:8000/docs> for the interactive documentation (use the **Authorize** button and
paste the `access_token` from `/auth/login`).

Try it from PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health

$body = @{ full_name = "Ada"; email = "ada@example.com"; password = "a-fine-password-1" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/auth/register -ContentType "application/json" -Body $body

$login = @{ email = "ada@example.com"; password = "a-fine-password-1" } | ConvertTo-Json
$token = (Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/auth/login -ContentType "application/json" -Body $login).access_token
$auth = @{ Authorization = "Bearer $token" }

Invoke-RestMethod -Uri http://127.0.0.1:8000/auth/me -Headers $auth                  # works
Invoke-RestMethod -Uri http://127.0.0.1:8000/protected/files -Headers $auth          # USER: works
Invoke-RestMethod -Uri http://127.0.0.1:8000/protected/providers -Headers $auth      # USER: 403 and an ACCESS_DENIED audit row
```

## Endpoints

| Method and path | Who | Purpose |
|---|---|---|
| `GET /health` | anyone | 200 if the database answers, 503 if not (no error details) |
| `POST /auth/register` | anyone | creates a USER account (roles are never self-assigned) |
| `POST /auth/login` | anyone | returns a signed JWT with expiry |
| `GET /auth/me` | any logged-in user | current user |
| `GET /protected/files` | USER | RBAC demo: "manage own files" |
| `GET /protected/providers` | ADMIN | RBAC demo: "manage providers" |
| `GET /protected/audit` | ADMIN, AUDITOR | RBAC demo: "read the audit log" (read-only) |
| `GET /providers` | any logged-in role | providers, regions, availability, scores, pricing |
| `POST /providers`, `PATCH /providers/{id}` | ADMIN | register, rename, resize limit, enable/disable |
| `POST /providers/{id}/regions`, `PATCH /regions/{id}` | ADMIN | register region, change scores, egress, availability |
| `PUT /regions/{id}/pricing/{tier}` | ADMIN | create or replace a tier's prices |
| `POST /selection/compare` | any logged-in role | all regions side by side + winner under each preset (dry run) |
| `POST /selection/select` | any logged-in role | rank, explain, baselines (dry run, writes nothing) |
| `POST /files?file_name=..&security_level=..` | USER | upload (raw body): select provider, encrypt, store, record metadata |
| `GET /files/{id}/download` | USER (owner) | fetch, decrypt, verify SHA-256, log latency |
| `DELETE /files/{id}?confirm=true` | USER (owner) | delete with explicit confirmation; erases the key |
| `GET /reports/duplicates` | any logged-in role | read-only: groups of identical files, wasted bytes, estimated monthly waste (USER: own files; ADMIN, AUDITOR: all) |
| `GET /reports/cold-files?idle_days=..&max_accesses=..&window_days=..` | any logged-in role | read-only: "candidate for archival review" list with evidence |

## Permission table (single source of truth: `app/auth/rbac.py`)

| Permission | USER | ADMIN | AUDITOR |
|---|:-:|:-:|:-:|
| `provider:read` | yes | yes | yes |
| `provider:manage` | | yes | |
| `file:read_own`, `file:write_own`, `file:delete_own` | yes | | |
| `file:read_any_metadata` | | yes | yes |
| `user:manage`, `config:manage` | | yes | |
| `config:read`, `audit:read`, `report:read` | | yes | yes |

AUDITOR holds only read permissions (a test enforces that it holds no modifying permission).
ADMIN has no own-file permissions in this chunk; add them to the table if admins should upload.
Every request re-reads the user's **role and status from the database**, so a role change or a
disabled account takes effect immediately, and a forged `role` claim in a token has no effect.

## Provider selection (Chunk 3)

Print the fully worked numeric example (illustrative AWS / Azure / Google Cloud numbers, every intermediate value):

```powershell
.\.venv\Scripts\python.exe scripts\worked_example.py
```

Optional: store the reliability minimums used by the hard filter in the database (the code has the same values as defaults):

```powershell
cd sql
sqlplus cloudmeta@localhost:1521/FREEPDB1 '@seed_selection_config.sql'
cd ..
```

Try a dry run (after logging in as in the example above, `$auth` holds the token header):

```powershell
$body = @{ file_size_bytes = 1073741824; security_level = "HIGH"; preset = "SECURE" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/selection/select -Headers $auth -ContentType "application/json" -Body $body
```

All prices, scores and latencies in the seed data are **illustrative**. The full method, formulas, tie-break order and limitations are in `docs/03-selection-engine.md`.

## Encrypted upload and download (Chunk 4; storage is SIMULATED)

Generate a master key and put it in `.env` as `MASTER_KEY=...`:

```powershell
.\.venv\Scripts\python.exe -c "import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

With the server running and `$auth` set (see above):

```powershell
$up = Invoke-RestMethod -Method Post -Headers $auth -InFile .\report.pdf -ContentType "application/octet-stream" `
    -Uri "http://127.0.0.1:8000/files?file_name=report.pdf&security_level=HIGH"
$up | Format-List file_id, provider, region, storage, content_sha256, ciphertext_sha256
Invoke-WebRequest -Headers $auth -OutFile .\back.pdf -Uri "http://127.0.0.1:8000/files/$($up.file_id)/download"
(Get-FileHash .\report.pdf).Hash -eq (Get-FileHash .\back.pdf).Hash     # True
Get-ChildItem -Recurse storage_sim -File                                   # the encrypted object
```

Open the object in `storage_sim`: it is ciphertext (no `%PDF` header, your text is not in it). Details, failure rules and limits are in `docs/04-storage-encryption.md`.

## Audit events written

`USER_REGISTERED` (success and duplicate-email failure), `LOGIN_OK`, `LOGIN_FAILED` (the reason is
stored internally; the client always gets the same 401), `ACCESS_DENIED` (403), and for registry
changes `CONFIG_CHANGED` and `PROVIDER_STATUS_CHANGED` (old and new values in `details_json`), and for files
`FILE_UPLOADED`, `FILE_DELETED` and `ACCESS_DENIED` (not the owner). Downloads are recorded in `access_logs`. Audit rows are
committed in their own transaction so they survive the failed request, and if an audit write fails
the request fails too. Requests with a missing or invalid token (401) are not audited.

## Troubleshooting

**1. Oracle connection fails.** The server still starts (the driver connects lazily), but `/health`
returns 503 (it can take about 8 seconds to give up) and login/register return 503 "Database
unavailable". The server log shows `DPY-6005` / `ORA-12541` (nothing listening) or `ORA-01017` (wrong user or password).
* Check the services: `Get-Service | Where-Object Name -like "Oracle*"` (all `Running`)
* Check the port: `Test-NetConnection localhost -Port 1521`
* Check `.env`: `ORACLE_DSN=localhost:1521/FREEPDB1`, `ORACLE_USER=cloudmeta`, and that the password
  works in `sqlplus cloudmeta@localhost:1521/FREEPDB1`.
* Repeated wrong passwords can lock the Oracle account (ORA-28000); unlock as SYS with
  `ALTER USER cloudmeta ACCOUNT UNLOCK;`

**2. Missing environment variable.** Start-up stops with
`ConfigError: Environment variable X is not set (see .env.example)`. Create `.env` from
`.env.example` in the project root and fill in `X`. If it says `JWT_SECRET must be at least 32
characters`, generate one with the command in Setup.

**3. Port already in use** (`[WinError 10048]` when starting uvicorn). Use another port
(`--port 8001`) or find the process: `Get-NetTCPConnection -LocalPort 8000 | Select OwningProcess`
then `Stop-Process -Id <id>`.

## Documentation map

| File | What it is |
|---|---|
| `docs/00-design.md` | system design, refreshed: every module and workflow marked built, partly built, simulated or **designed, not built** |
| `docs/01-schema-audit.md`, `01-data-dictionary.md`, `01-run-guide.md` | database audit, dictionary, how to run the SQL scripts |
| `docs/03-selection-engine.md` (and `03-review-package.md`) | selection engine, worked example, review material |
| `docs/04-storage-encryption.md` | simulated storage, encryption, upload, download, delete |
| `docs/05-test-report.md` | exact commands and **results tables for you to fill from your own runs** |
| `docs/06-limitations-and-objectives.md` | what is simulated, what is not implemented, objective-to-evidence map |
| `docs/07-report-outline-and-demo.md` | one-page report outline and the demo checklist |
| `docs/08-demo-runbook.md` | every demo command in order |
| `docs/09-reports.md` | Chunk 6: duplicate and cold-file reports, synthetic sample, commands, verification results |

**Project status in one line:** database, auth and RBAC, provider selection, and encrypted upload/download to simulated storage are built and tested. Read-only duplicate and cold-file reports are built too. Replication and failover, monitoring and recommendations (objective 4) and the dashboard are **not built**.

## Folder structure (Chunk 0 layout)

```
app/
  main.py               app factory, error handlers
  config.py             settings from environment variables
  db/                   base.py (interfaces), pool.py (pool + transaction helper),
                        errors.py (Oracle error mapping), repositories/ (all SQL, bind variables only)
  auth/                 passwords.py (Argon2), tokens.py (JWT), rbac.py (permission table),
                        service.py (register/login), deps.py (current user, require_permission)
  audit/service.py      audit events
  providers/            module 3: registry service and row types
  selection/            module 4: engine.py (pure), models, presets, explain, baselines, example, service
  crypto/               AES-256-GCM envelope encryption (master key from the environment)
  storage/              storage interface + the one SIMULATED local-folder adapter
  files/                upload / download / delete service, errors, row types
  api/                  schemas*.py, routers/ (auth, health, protected, providers, selection)
scripts/set_password.py activate seeded demo users
sql/                    Chunk 1 database scripts (schema, seed, demos, migration)
docs/                   design, schema audit, run guide, results
tests/unit/             fake-database tests (no Oracle)
tests/integration/      tests that need Oracle (you run them)
```
