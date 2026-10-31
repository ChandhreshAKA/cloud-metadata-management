# Multi-Cloud Metadata Manager

Course project (DBMS): a central Oracle metadata store plus an API for authentication, role-based
access control and auditing of multi-cloud file storage. Providers are **simulated** (see Status).

## Status (chunks 1 to 4, 6 and 7 built; documentation set complete; chunk 5 not built)

| Area | State |
|---|---|
| Oracle schema, migration, demos, indexes (`sql/`) | Done and executed on Oracle 26ai Free (chunk 1). Not yet run: the 10,000-row EXPLAIN comparison and the legacy-migration test |
| Config, DB layer, register, login (Argon2 + JWT), RBAC, audit, health (chunk 2) | **Built and tested.** The `users` table now goes through the SQLAlchemy ORM (`app/db/orm.py`, `app/db/repositories/users.py`); the other repositories still use raw SQL and are converted one at a time |
| Provider registry (admin-only writes), selection engine, dry-run endpoints, worked example, baselines (chunk 3) | **Built and tested.** See `docs/03-selection-engine.md` |
| Encrypted upload, download and delete to **SIMULATED** local-folder storage (AES-256-GCM; the selection engine runs on every upload) (chunk 4) | **Built and tested.** See `docs/04-storage-encryption.md` |
| Read-only **duplicate-file report** and **cold-file report**, plus a seeded synthetic sample (chunk 6) | **Built and tested** against Oracle. Exact duplicates within one owner; cold files listed as "candidates for archival review". See `docs/09-reports.md` |
| `scripts/demo.py` (scripted demo), `sql/demo_queries.sql` (live SQL), `GET /files` and `GET /files/{id}/placement`, optional one-page Streamlit page `dashboard.py` (chunk 7) | **Built.** The demo passed against the real API and Oracle on the machine where it was written; the Streamlit page was tested headlessly, not in a browser window |
| Test report, limitations, objective map, report outline, demo checklist (chunk 8) | **Written** (`docs/05` to `docs/09`). The results tables are **blank for you to fill from your own runs** |
| **Objective 4 is partially covered.** Implemented: duplicate-file report, cold-file report, file list, stored placement explanation | See `docs/06-limitations-and-objectives.md` |
| **Not built:** replication and failover; migration recommendations and saving or payback estimates; storage usage and cost reports; provider performance reports; monitoring pages and charts (the Streamlit page is one optional page, no charts); real cloud adapters; TLS; key rotation; orphan sweep | Designed in `docs/00-design.md` where noted, not part of the project |
| Account lockout, password reset, token revocation, rate limiting | Deliberately not built |

Test counts and coverage are not stated here: they change with every chunk. Run the commands in "Run the checks" and fill
the tables in `docs/05-test-report.md`.

## Prerequisites

* Windows, PowerShell, Python 3.12 (the version this was developed and tested with; other versions are untested)
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
| `DEMO_PASSWORD` | no | `demo-password-123` | password for the synthetic demo accounts; read only by `scripts/demo.py` (from the environment or `.env`) |
| `API_URL` | no | `http://127.0.0.1:8000` | where `dashboard.py` finds the API; set it in the shell (`$env:API_URL = ...`), it is **not** read from `.env` |

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

(`.env` must contain valid `ORACLE_*`, `JWT_SECRET` and `MASTER_KEY` values; the tests load it.)

**Coverage, and the commands that fill the results tables.** Unit tests alone leave the Oracle repository code
uncovered (only the integration tests run that SQL), so the combined run is the fair figure:

```powershell
New-Item -ItemType Directory -Force test-results | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/unit -v --cov=app --cov-report=term-missing | Tee-Object -FilePath test-results\unit.txt
$env:RUN_INTEGRATION = "1"
.\.venv\Scripts\python.exe -m pytest tests/integration -v --cov=app --cov-report=term-missing | Tee-Object -FilePath test-results\integration.txt
.\.venv\Scripts\python.exe -m pytest --cov=app --cov-report=term-missing --cov-report=html | Tee-Object -FilePath test-results\combined.txt
Remove-Item Env:RUN_INTEGRATION
```

`test-results\` and `htmlcov\` are git-ignored. The integration tests leave `itest_*@example.test` users and audit rows;
`sql\cleanup_integration_tests.sql` removes them (it truncates `audit_events`: development database only). The full command list and
the blank results tables are in `docs/05-test-report.md`.

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

## Run the demo (no manual steps)

One command runs the whole story against the real API and the real Oracle database. It starts the API
itself if none is listening, and stops it at the end. Output is labelled **SIMULATION MODE**.

Prerequisites: Oracle running with the schema and seed data installed (`sql/install_fresh.sql`), and `.env` with `ORACLE_*`,
`JWT_SECRET` and `MASTER_KEY`. If your own API is already running on port 8000 the demo uses it.

```powershell
cd C:\dbmsproject
.\.venv\Scripts\python.exe scripts\demo.py
```

| Section | What it does |
|---|---|
| 1 Setup | health check, mode banner |
| 2 Accounts | registers or logs in `demo_user@example.test` and `demo_auditor@example.test` (synthetic) |
| 3 Selection | ranked providers, rejected regions and the explanation (dry run) |
| 4 Upload | uploads a generated PDF; shows the chosen provider, hashes, encryption and the saved placement |
| 5 Database | the stored `files`, `file_encryption`, location and decision rows; the encrypted object's path, size and first bytes |
| 6 Download | downloads, checks the SHA-256, flips one stored byte to prove tampering is caught (502), restores it |
| 7 Access log | the access-log rows those steps wrote |
| 8 Reports | uploads the same PDF again (a real duplicate), runs the duplicate and cold-file reports, shows a synthetic cold-file sample |
| 9 RBAC | a USER and an AUDITOR attempt actions they may not do (403), and the audit rows that recorded it |
| 10 Clean up | deletes the demo files (key erased) and the synthetic sample |

Options: `--pause` (wait for Enter between sections, useful when running the SQL below), `--keep` (leave
the demo files and the sample in place), `--no-sample` (skip the synthetic sample), `--cleanup-only`
(remove what `--keep` left), `--base-url http://127.0.0.1:8001` (another port). It exits with a non-zero
code if any check fails and prints `RESULT: N of N checks passed`.

The script performs every action through the HTTP API. It reads the database directly only with `SELECT`
(to show what changed). Its only direct writes are labelled in the output: it sets the role of its own
synthetic `demo_*` accounts, and it seeds and removes the synthetic report sample
(`app/reports/sample_data.py`).

### Live SQL for the review

`sql/demo_queries.sql` has one read-only block per section (Q1 to Q10). Run the demo with
`--pause --keep`, and after each section run the matching block:

```powershell
sqlplus cloudmeta@localhost:1521/FREEPDB1
SQL> @sql/demo_queries.sql
```

## Optional dashboard (Streamlit, one page)

`dashboard.py` (83 lines) is a thin screen over the API: log in, your files, the selected provider with its
saved explanation and ranking, and the duplicate and cold-file tables. It computes nothing itself. It is
**optional**: the API, the tests and the demo do not need it. Install Streamlit in a separate virtual
environment (do not add it to the project's pinned requirements unless you want it there):

```powershell
python -m venv .venv-dash
.\.venv-dash\Scripts\python.exe -m pip install -r requirements-optional.txt
# API running in another window, then:
.\.venv-dash\Scripts\python.exe -m streamlit run dashboard.py
```

It uses the shell variable `API_URL` (default `http://127.0.0.1:8000`; for example `$env:API_URL = "http://127.0.0.1:8001"`). Log in as a normal USER (for example
`demo_user@example.test` after the demo created it; the password is `demo-password-123` unless you set
`DEMO_PASSWORD`). ADMIN and AUDITOR accounts have no own files, so the file tabs show a message for them.

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
| `GET /files` | USER | your own files (metadata only), newest first |
| `GET /files/{id}/placement` | USER (owner) | why the file is where it is: the saved ranking and explanation |
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
The report endpoints use two rules: `report:read` (ADMIN, AUDITOR) shows every owner's files; a USER, who holds `file:read_own`, sees only their own.
ADMIN has no own-file permissions in this chunk; add them to the table if admins should upload.
Every request re-reads the user's **role and status from the database**, so a role change or a
disabled account takes effect immediately, and a forged `role` claim in a token has no effect.

## Provider selection 

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

## Encrypted upload and download ( 4; storage is SIMULATED)

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
`FILE_UPLOADED`, `FILE_DELETED` and `ACCESS_DENIED` (not the owner, including `GET /files/{id}/placement`). Downloads are recorded in `access_logs`. The report endpoints and `GET /files` write nothing
(a refused request is still an `ACCESS_DENIED` row). Audit rows are
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
then `Stop-Process -Id <id>`. For the demo, point it at the other port with
`scripts\demo.py --base-url http://127.0.0.1:8001`; if the demo says the API did not start, read
`demo_server.log` in the project folder.

If the demo says `login failed for demo_user@example.test`, that account was created earlier with a different
`DEMO_PASSWORD`; set the same `DEMO_PASSWORD` again.

The demo reports the same three problems in its own words: `Cannot connect to Oracle (...)` (item 1),
`Environment variable ORACLE_... is not set` (item 2) and `The API did not start` (item 3).

## Documentation map

| File | What it is |
|---|---|
| `docs/00-design.md` | system design, refreshed: every module and workflow marked built, partly built, simulated or **designed, not built** |
| `docs/01-schema-audit.md`, `01-data-dictionary.md`, `01-run-guide.md`, `01-explain-results.md` | database audit, dictionary, how to run the SQL scripts, EXPLAIN PLAN results |
| `docs/03-selection-engine.md` (and `03-review-package.md`) | selection engine, worked example, review material |
| `docs/04-storage-encryption.md` | simulated storage, encryption, upload, download, delete |
| `docs/05-test-report.md` | exact commands and **results tables for you to fill from your own runs** |
| `docs/06-limitations-and-objectives.md` | what is simulated, what is not implemented, objective-to-evidence map |
| `docs/07-report-outline-and-demo.md` | one-page report outline and the demo checklist |
| `docs/08-demo-runbook.md` | every manual demo command in order (the automated demo is `scripts/demo.py`) |
| `sql/demo_queries.sql` | read-only SQL to run live after each demo step (Q1 to Q10) |
| `sql/reports.sql`, `sql/seed_reports_config.sql`, `sql/explain_reports.sql` | the two reports in SQL (read-only), optional report settings, plans of the report queries |
| `docs/09-reports.md` | Chunk 6: duplicate and cold-file reports, synthetic sample, commands, verification results |

**Project status in one line:** database, auth and RBAC, provider selection, encrypted upload/download to simulated storage, read-only duplicate and cold-file reports and a scripted demo are built and tested. Replication and failover, migration recommendations and savings, usage, cost and performance reports, and monitoring pages are **not built** (objective 4 is partially covered).

## Folder structure

```
app/
  main.py               app factory, error handlers
  config.py             settings from environment variables
  db/                   base.py (interfaces), pool.py (pool + transaction helper), errors.py (Oracle error mapping),
                        repositories/ (users, audit, providers, files, reports: all SQL, bind variables only)
  auth/                 passwords.py (Argon2), tokens.py (JWT), rbac.py (permission table),
                        service.py (register/login), deps.py (current user, require_permission, report scope)
  audit/service.py      audit events
  providers/            module 3: registry service and row types
  selection/            module 4: engine.py (pure), models, presets, explain, baselines, example, serialize, service
  crypto/               AES-256-GCM envelope encryption (master key from the environment)
  storage/              storage interface + the one SIMULATED local-folder adapter
  files/                upload / download / delete / list / placement service, errors, row types
  reports/              chunk 6: models, service (arithmetic, thresholds, labels), sample_data (synthetic sample)
  api/                  schemas*.py, routers/ (auth, health, protected, providers, selection, files, reports)
scripts/                demo.py (scripted demo), set_password.py, worked_example.py, make_review_package.py
dashboard.py            optional one-page Streamlit view (calls the API only)
sql/                    database scripts: schema, indexes, views, seeds, migration, demos, synthetic data, cleanup,
                        reports.sql, seed_reports_config.sql, demo_queries.sql, explain_reports.sql
schema.sql              the ORIGINAL five-table schema as first uploaded (the revised one is sql/schema.sql)
docs/                   design, schema audit, run guide, selection, storage, test report, limitations, outline, demo, reports
tests/unit/             fake-database tests (no Oracle)
tests/integration/      tests that need Oracle (you run them)
requirements.txt        runtime dependencies          requirements-dev.txt   + test and lint tools
requirements-lock.txt   every installed package pinned   requirements-optional.txt   Streamlit, for dashboard.py only
.env.example            copy to .env (git-ignored)     pyproject.toml   pytest and ruff settings
```

Generated and git-ignored: `.env`, `.venv/`, `storage_sim/` (the simulated provider folders), `test-results/`, `htmlcov/`, `*.log`
(including `demo_server.log`).
