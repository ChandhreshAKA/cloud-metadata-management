# Deployment guide: Render (API + dashboard) with Oracle Autonomous Database (Always Free)

Why this option: it keeps the code that is already tested on Oracle (434 unit + 56 integration tests) and puts
both web parts on Render, which is on the recommended platform list. Nothing here has been run for you: tick
each check as you go.

```
Browser -> Render: dashboard (Streamlit) -> Render: API (FastAPI) -> Oracle Autonomous Database (TLS)
```

## 0. Files added for deployment
| File | Purpose |
|---|---|
| `Dockerfile` | API image (binds to `$PORT`, non-root user) |
| `Dockerfile.dashboard` | Streamlit dashboard image (needs `API_URL`) |
| `.dockerignore` | keeps `.env`, `.git`, tests and docs out of images |
| `.github/workflows/ci.yml` | ruff + unit tests on every push / pull request (CI bonus) |
| `.github/workflows/docker-publish.yml` | pushes both images to Docker Hub when `main` changes |
| `sql/prereq_autonomous.sql` | creates the `cloudmeta` user on Autonomous (cloud twin of `prereq_as_sysdba.sql`) |

## 1. Put the files in the repo (feature branch, pull request into `dev`)
```
git checkout dev
git pull
git checkout -b feature/deployment
Add-Content .gitignore ".venv-dash/"
git add -A
git status          # .env must NOT be listed
git commit -m "Add Dockerfiles, CI workflows and Autonomous DB setup script"
git push -u origin feature/deployment
```
Open the pull request on GitHub with base **dev**, wait for the CI check to turn green, merge. Later merge `dev`
into `main` through a second pull request.

## 2. Docker Hub (rubric: Docker Hub)
1. Create a free Docker Hub account. Account settings -> Security -> **New access token** (read/write).
2. GitHub repo -> Settings -> Secrets and variables -> Actions -> add `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`.
3. The images are pushed when `main` changes, or run it by hand: Actions -> *Publish Docker images* -> Run workflow.
4. Screenshot the two repositories on Docker Hub (`cloud-metadata-api`, `cloud-metadata-dashboard`).

## 3. Database: Oracle Autonomous Database, Always Free
1. Create an Oracle Cloud Free Tier account (Oracle may ask for a card for identity checks).
2. Create an **Autonomous AI Database**, tick **Always Free**, workload *Transaction Processing*, set an ADMIN password.
3. Network: access type **Secure access from everywhere** (Render has no fixed IP). Then open the database ->
   *Mutual TLS (mTLS) authentication* -> Edit -> **untick Require**. This allows the wallet-less connection the app uses.
4. **Database connection** -> *TLS authentication: TLS* -> copy the connection string of the `_low` service.
   That whole `(description=...)` text is your `ORACLE_DSN`.
5. Create the schema. Easiest robust route is Oracle SQLcl (free; needs Java 11+) with the wallet:
   * Database connection -> **Download wallet** (set a wallet password).
   * In PowerShell, from the `sql` folder:
```
sql /nolog
set cloudconfig C:\path\Wallet_yourdb.zip
connect admin@yourdb_low
@prereq_autonomous.sql
connect cloudmeta@yourdb_low
@install_fresh.sql
```
   `install_fresh.sql` ends with `verify_schema.sql`: you want 17 tables, 8 views and no invalid objects.
   (Alternative: Database Actions -> SQL, run `schema.sql`, `indexes.sql`, `views.sql`, `seed_data.sql`,
   `seed_demo.sql` one by one. If it rejects a `SET` line at the top of a script, delete that line.)
6. Activate the seeded demo accounts from your PC, pointing at the cloud database only for this window:
```
$env:ORACLE_DSN = "<the TLS connection string>"
$env:ORACLE_USER = "cloudmeta"
$env:ORACLE_PASSWORD = "<the CLOUDMETA password>"
.\.venv\Scripts\python.exe scripts\set_password.py admin@example.com
.\.venv\Scripts\python.exe scripts\set_password.py auditor@example.com
Remove-Item Env:ORACLE_DSN, Env:ORACLE_USER, Env:ORACLE_PASSWORD
```
Limits: an Always Free database is stopped after 7 days without activity (start it from the console; data is
kept) and reclaimed if it stays stopped for 90 days. Max 30 sessions. No manual backups to object storage:
show your `expdp`/`impdp` backup and restore on the local database instead.

## 4. Render: the API
1. render.com -> sign up with GitHub (a card may be requested to verify the account) and let Render read your
   (private) repository.
2. **New -> Web Service** -> choose the repo -> Language **Docker** -> Branch `main` -> Dockerfile path `./Dockerfile`
   -> Instance type **Free**.
3. Environment variables (type them in the Render page; never in the repo):

| Name | Value |
|---|---|
| `ORACLE_DSN` | the TLS connection string from step 3.4 |
| `ORACLE_USER` | `cloudmeta` |
| `ORACLE_PASSWORD` | the CLOUDMETA password |
| `JWT_SECRET` | 48+ random characters: `.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `MASTER_KEY` | `.\.venv\Scripts\python.exe -c "import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"` (back it up) |
| `APP_ENV` | `production` |
| `DB_POOL_MAX` | `3` |

4. Advanced -> **Health Check Path** `/health` -> Create Web Service.
5. When it is live: open `https://<name>.onrender.com/health` (database `up`) and `/docs`.

## 5. Render: the dashboard
**New -> Web Service**, same repo, Docker, Dockerfile path `./Dockerfile.dashboard`, Free, one variable:
`API_URL` = `https://<your-api-name>.onrender.com` (no trailing slash). Open the dashboard URL: that is your
**Live URL**. Also submit the API's `/docs` link.

## 6. Check it works (take screenshots here)
* `/health` shows the database up; `/docs` loads.
* Log in on the dashboard, upload a file, open the placement explanation.
* Render dashboard: both services *Live*; GitHub: green CI run; Docker Hub: both images.

## 7. Known limits (say them in the report)
* Free Render services sleep after ~15 minutes idle; the first request can take 30 to 60 seconds. Free instance
  hours are limited: check Render's current terms.
* Simulated storage lives on the container disk, which is wiped on restart: uploaded file bytes disappear,
  metadata stays in Oracle, and a later download gives a clean 404. Upload right before a demo.
* Autonomous stops after 7 idle days: start it before the demo.
* The app is served over HTTPS by Render; the database link uses TLS.

## 8. Demo-day checklist
1. Start the Autonomous database in the Oracle console. 2. Open the API `/health` until it says `up`.
3. Open the dashboard. 4. Upload a fresh file, then show placement, duplicates and cold files.

## 9. Optional: custom domain (+1 bonus), do it last
DuckDNS only offers A records, and Render normally wants a CNAME. It may work with the A record Render gives for
its load balancer, but a certificate can stay "pending". Try it only after everything above works.
