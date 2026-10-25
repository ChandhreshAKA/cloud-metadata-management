# Multi-Cloud Metadata Manager

Course project (DBMS): a central Oracle metadata store plus an API for authentication, role-based
access control and auditing of multi-cloud file storage. Providers are **simulated** (see Status).

## Status (end of Chunk 3)

| Area | State |
|---|---|
| Oracle schema, migration, demos, indexes (`sql/`) | Done and executed on Oracle 26ai Free (Chunk 1) |
| Config, DB layer, register, login (Argon2 + JWT), RBAC, audit, health | **Built and verified.** 127 unit tests pass (fake database); 13 integration tests pass against Oracle 26ai Free (run by the project owner, 1.93 s) |
| Provider registry (admin-only writes) and the selection engine, dry-run endpoints, worked example, baselines | **Built and verified.** 275 unit tests; 29 integration tests pass against Oracle (run at build time). See `docs/03-selection-engine.md` |
| Storage, encryption, upload/download, duplicates and cold files, demo script, test report | Not built yet (later chunks) |
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

Edit `.env` (it is git-ignored). Set at least `ORACLE_PASSWORD` and a real `JWT_SECRET`:

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

## Audit events written

`USER_REGISTERED` (success and duplicate-email failure), `LOGIN_OK`, `LOGIN_FAILED` (the reason is
stored internally; the client always gets the same 401), `ACCESS_DENIED` (403), and for registry
changes `CONFIG_CHANGED` and `PROVIDER_STATUS_CHANGED` (old and new values in `details_json`). Audit rows are
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
  api/                  schemas*.py, routers/ (auth, health, protected, providers, selection)
scripts/set_password.py activate seeded demo users
sql/                    Chunk 1 database scripts (schema, seed, demos, migration)
docs/                   design, schema audit, run guide, results
tests/unit/             fake-database tests (no Oracle)
tests/integration/      tests that need Oracle (you run them)
```
