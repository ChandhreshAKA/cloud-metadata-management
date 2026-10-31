r"""End-to-end command-line demo. No manual steps.

    .\.venv\Scripts\python.exe scripts\demo.py            # run everything
    .\.venv\Scripts\python.exe scripts\demo.py --pause    # wait for Enter between sections
    .\.venv\Scripts\python.exe scripts\demo.py --keep     # leave the demo files and sample behind
    .\.venv\Scripts\python.exe scripts\demo.py --cleanup-only   # remove what --keep left behind

SIMULATION MODE: cloud storage is local folders, and prices, latencies, reliability and security
scores are illustrative. The database, the selection engine, encryption, role checks and the audit
trail are real.

How it works: every ACTION goes through the HTTP API (register, login, upload, download, reports,
the unauthorized attempts). The script reads the database directly only with SELECT, to show what
changed after each step. Its only direct writes are labelled: the demo accounts' role, and the
synthetic report sample (app/reports/sample_data.py). If no API is listening, the script starts one
and stops it at the end.

Live SQL for each section is in sql/demo_queries.sql (Q1 ... Q9).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import oracledb  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from app.reports import sample_data  # noqa: E402

USER_EMAIL = "demo_user@example.test"
AUDITOR_EMAIL = "demo_auditor@example.test"
MARKER = "DEMO-PDF-MARKER"
WIDTH = 78

CHECKS: list[tuple[str, bool]] = []


# ---------------------------------------------------------------------------- output helpers
def banner() -> None:
    print("=" * WIDTH)
    print(" MULTI-CLOUD METADATA MANAGER: COMMAND-LINE DEMO")
    print(" *** SIMULATION MODE ***")
    print(" Cloud storage = local folders. Prices, latencies and scores are ILLUSTRATIVE.")
    print(" Real: Oracle database, selection engine, AES-256-GCM encryption, roles, audit trail.")
    print("=" * WIDTH)


def section(number: int, title: str, sql: str, pause: bool) -> None:
    if pause and number > 1:
        input("\n[press Enter to continue] ")
    print("\n" + "=" * WIDTH)
    print(f" {number}. {title}")
    print(f"    live SQL for this step: sql/demo_queries.sql, {sql}")
    print("=" * WIDTH)


def note(text: str) -> None:
    print(f"   {text}")


def check(label: str, passed: bool, detail: str = "") -> None:
    CHECKS.append((label, passed))
    print(f"   [{'PASS' if passed else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def table(headers: list[str], rows: list[list[Any]], indent: str = "   ") -> None:
    text = [[("" if v is None else str(v)) for v in row] for row in rows]
    widths = [max(len(h), *(len(r[i]) for r in text)) if text else len(h) for i, h in enumerate(headers)]
    widths = [min(w, 60) for w in widths]
    line = lambda cells: indent + "  ".join(c[:w].ljust(w) for c, w in zip(cells, widths, strict=True))  # noqa: E731
    print(line(headers))
    print(indent + "  ".join("-" * w for w in widths))
    for row in text:
        print(line(row))
    if not text:
        print(indent + "(no rows)")


# ---------------------------------------------------------------------------------- HTTP
class Api:
    """A tiny HTTP client on the standard library (no extra dependency)."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, token: str | None = None, body: Any = None,
             params: dict[str, Any] | None = None, raw: bytes | None = None) -> tuple[int, Any, bytes]:  # fmt: skip
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        headers = {}
        data = None
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        if raw is not None:
            data, headers["Content-Type"] = raw, "application/octet-stream"
        if not url.startswith("http://"):  # the demo only talks to the local API
            raise ValueError(f"refusing non-http URL {url}")
        request = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                status, payload = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, payload = exc.code, exc.read()
        try:
            return status, json.loads(payload), payload
        except ValueError:
            return status, None, payload

    def healthy(self) -> bool:
        try:
            return self.call("GET", "/health")[0] == 200
        except OSError:
            return False


def start_server(base: str) -> subprocess.Popen:
    port = urllib.parse.urlparse(base).port or 8000
    log = open(ROOT / "demo_server.log", "w")  # noqa: SIM115  (closed with the process)
    process = subprocess.Popen(  # noqa: S603  (fixed argument list, no user input)
        [sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory", "--port", str(port)],
        cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
    )  # fmt: skip
    api = Api(base)
    for _ in range(60):
        if api.healthy():
            return process
        if process.poll() is not None:
            break
        time.sleep(0.5)
    process.terminate()
    tail = (ROOT / "demo_server.log").read_text(errors="replace")[-1500:]
    sys.exit(f"The API did not start. Last lines of demo_server.log:\n{tail}")


# ------------------------------------------------------------------------------ database
class Db:
    """Direct Oracle connection used for SELECTs and two labelled setup writes."""

    def __init__(self) -> None:
        self.conn = oracledb.connect(
            user=os.environ["ORACLE_USER"], password=os.environ["ORACLE_PASSWORD"],
            dsn=os.environ["ORACLE_DSN"],
        )  # fmt: skip

    def rows(self, sql: str, binds: dict[str, Any] | None = None) -> tuple[list[str], list[list[Any]]]:
        cursor = self.conn.cursor()
        cursor.execute(sql, binds or {})
        names = [d[0].lower() for d in cursor.description]
        return names, [list(r) for r in cursor.fetchall()]

    def show(self, sql: str, binds: dict[str, Any] | None = None) -> list[list[Any]]:
        names, rows = self.rows(sql, binds)
        table(names, rows)
        return rows

    def set_role(self, email: str, role: str) -> None:
        assert email.startswith("demo_"), "refusing to change the role of a non-demo account"
        self.conn.cursor().execute(
            "UPDATE users SET role = :role, account_status = 'ACTIVE' WHERE email = :email",
            {"role": role, "email": email},
        )
        self.conn.commit()


def make_pdf(run_id: str) -> bytes:
    """A tiny but valid one-page PDF whose text contains a marker and the run id."""
    stream = f"BT /F1 18 Tf 72 720 Td ({MARKER} run {run_id}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------------------- the demo
def get_token(api: Api, db: Db, email: str, password: str, role: str) -> str:
    """Register (or find) the demo account, set its role, log in. Returns the JWT."""
    body = {"full_name": email.split("@")[0], "email": email, "password": password}
    status, _, _ = api.call("POST", "/auth/register", body=body)
    note(f"register {email}: HTTP {status} ({'created' if status == 201 else 'already exists'})")
    db.set_role(email, role)  # DEMO SETUP: makes sure the account is ACTIVE and has this role
    status, body, _ = api.call("POST", "/auth/login", body={"email": email, "password": password})
    if status != 200:
        sys.exit(f"login failed for {email}: HTTP {status} {body}. Was it created with another password?")
    return body["access_token"]


def run(args: argparse.Namespace) -> int:
    load_dotenv(ROOT / ".env")
    for name in ("ORACLE_USER", "ORACLE_PASSWORD", "ORACLE_DSN"):
        if not os.environ.get(name):
            sys.exit(f"Environment variable {name} is not set. Copy .env.example to .env and fill it in.")  # fmt: skip
    password = os.environ.get("DEMO_PASSWORD", "demo-password-123")
    storage_root = (ROOT / os.environ.get("STORAGE_ROOT", "storage_sim")).resolve()
    run_id = uuid.uuid4().hex[:8]

    banner()
    api, server = Api(args.base_url), None
    try:
        db = Db()
    except oracledb.Error as exc:
        sys.exit(f"Cannot connect to Oracle ({exc}). Is the database service running?")

    # ---- 1. setup ---------------------------------------------------------------------------
    section(1, "SETUP: API, database and mode", "Q1", args.pause)
    if api.healthy():
        note(f"API already running at {args.base_url}")
    else:
        note(f"no API at {args.base_url}: starting one (stopped again at the end)")
        server = start_server(args.base_url)
    try:
        status, body, _ = api.call("GET", "/health")
        check("GET /health reports the database is up", status == 200 and body["database"] == "up", str(body))  # fmt: skip
        note(f"mode: SIMULATION (storage root {storage_root})")
        if args.cleanup_only:
            return _cleanup_only(api, db, password)
        return _demo(args, api, db, password, storage_root, run_id)
    finally:
        db.conn.close()
        if server:
            server.terminate()
            note("demo API stopped")


def _cleanup_only(api: Api, db: Db, password: str) -> int:
    """Delete the demo user's files through the API (key erased, object removed) and the sample."""
    token = get_token(api, db, USER_EMAIL, password, "USER")
    _, listing, _ = api.call("GET", "/files", token)
    for item in listing["files"]:
        status, body, _ = api.call("DELETE", f"/files/{item['file_id']}", token, params={"confirm": "true"})  # fmt: skip
        note(f"delete {item['file_name']}: HTTP {status}")
    sample_data.cleanup(db.conn)
    note("synthetic report sample removed; demo accounts remain (synthetic)")
    return 0


def _demo(args, api: Api, db: Db, password: str, storage_root: Path, run_id: str) -> int:  # noqa: C901, PLR0915
    pause = args.pause
    created: list[tuple[str, int]] = []  # (token owner label, file_id) to clean up

    # ---- 2. accounts ------------------------------------------------------------------------
    section(2, "ACCOUNTS AND LOGIN (passwords are hashed with Argon2id, tokens are JWT)", "Q2", pause)
    note("DEMO SETUP (direct database write, synthetic accounts only): roles cannot be self-assigned")
    note("through the API, so the script sets demo_auditor@example.test to AUDITOR directly.")
    user_token = get_token(api, db, USER_EMAIL, password, "USER")
    auditor_token = get_token(api, db, AUDITOR_EMAIL, password, "AUDITOR")
    _, me, _ = api.call("GET", "/auth/me", user_token)
    _, aud, _ = api.call("GET", "/auth/me", auditor_token)
    check("demo user logs in with role USER", me["role"] == "USER", me["email"])
    check("demo auditor logs in with role AUDITOR", aud["role"] == "AUDITOR", aud["email"])
    user_id = me["user_id"]

    # ---- 3. selection (dry run) -------------------------------------------------------------
    pdf = make_pdf(run_id)
    section(3, "PROVIDER SELECTION: ranked providers and the explanation (dry run)", "Q3", pause)
    ask = {"file_size_bytes": len(pdf), "security_level": "HIGH"}
    status, plan, _ = api.call("POST", "/selection/select", user_token, ask)
    check("selection endpoint answers", status == 200)
    table(["rank", "provider", "region", "score", "total cost (illustrative USD)"],
          [[r["rank"], r["provider_code"], r["region_code"], r["composite"], r["cost"]["total"]] for r in plan["ranking"]])  # fmt: skip
    note("rejected before scoring (hard rules):")
    for r in plan["rejected"]:
        note(f"  - {r['provider_code']}/{r['region_code']}: {r['reasons'][0]}")
    note("explanation: " + plan["explanation"])

    # ---- 4. upload --------------------------------------------------------------------------
    section(4, f"UPLOAD a PDF ({len(pdf)} bytes, security HIGH)", "Q4", pause)
    status, up, _ = api.call("POST", "/files", user_token, raw=pdf,
                             params={"file_name": f"demo_report_{run_id}.pdf", "security_level": "HIGH"})  # fmt: skip
    check("upload accepted (HTTP 201)", status == 201, str(up if status != 201 else ""))
    created.append(("user", up["file_id"]))
    file_id = up["file_id"]
    for key in ("file_id", "provider_code", "region", "tier", "storage", "content_sha256", "ciphertext_sha256"):
        note(f"{key:18}: {up[key]}")
    note(f"{'encryption':18}: {up['encryption']['algorithm']} (key version {up['encryption']['master_key_version']})")
    check("upload chose the same winner as the dry run",
          (up["provider_code"], up["region"]) == (plan["selected"]["provider_code"], plan["selected"]["region_code"]))  # fmt: skip
    status, placed, _ = api.call("GET", f"/files/{file_id}/placement", user_token)
    check("GET /files/{id}/placement returns the explanation saved in the database",
          status == 200 and placed["explanation"] == up["selection"]["explanation"])

    # ---- 5. database state ------------------------------------------------------------------
    section(5, "DATABASE after the upload (read-only SELECTs) and the encrypted object on disk", "Q5", pause)  # fmt: skip
    note("files row + location:")
    db.show("SELECT f.file_id, f.file_name, f.file_size_bytes AS bytes, f.file_type, f.security_level, f.status, "
            "SUBSTR(f.content_sha256,1,16) AS sha256_prefix, p.provider_code, r.region_code, sl.tier_code "
            "FROM files f JOIN storage_locations sl ON sl.file_id = f.file_id AND sl.is_primary = 'Y' "
            "JOIN provider_regions r ON r.region_id = sl.region_id JOIN cloud_providers p ON p.provider_id = r.provider_id "
            "WHERE f.file_id = :f", {"f": file_id})  # fmt: skip
    note("encryption record (the key itself is wrapped; the master key is not in the database):")
    db.show("SELECT algorithm, LENGTH(wrapped_dek) AS wrapped_dek_bytes, LENGTH(nonce) AS nonce_bytes, "
            "master_key_version AS key_version, SUBSTR(ciphertext_sha256,1,16) AS ciphertext_sha256_prefix "
            "FROM file_encryption WHERE file_id = :f", {"f": file_id})  # fmt: skip
    note("selection decision saved with its candidates:")
    db.show("SELECT d.decision_id, d.profile_name, COUNT(c.region_id) AS candidates, "
            "COUNT(c.total_score) AS scored, COUNT(c.excluded_reason) AS rejected "
            "FROM selection_decisions d JOIN selection_candidates c ON c.decision_id = d.decision_id "
            "WHERE d.file_id = :f GROUP BY d.decision_id, d.profile_name", {"f": file_id})  # fmt: skip
    _, loc = db.rows("SELECT sl.object_key FROM storage_locations sl WHERE sl.file_id = :f", {"f": file_id})
    object_path = storage_root / up["provider_code"] / up["region"] / loc[0][0]
    note(f"encrypted object path (SIMULATED cloud): {object_path}")
    if object_path.is_file():
        stored = object_path.read_bytes()
        note(f"object size: {len(stored)} bytes (PDF {len(pdf)} + 16-byte GCM tag)")
        note("first 32 bytes: " + stored[:32].hex(" "))
        check("stored object is NOT the PDF (no %PDF header, no marker text)",
              not stored.startswith(b"%PDF") and MARKER.encode() not in stored)
    else:
        note("object not found at that path: the API uses a different STORAGE_ROOT, so this check and the tamper test are skipped")  # fmt: skip

    # ---- 6. download and integrity ----------------------------------------------------------
    section(6, "DOWNLOAD and integrity verification", "Q6", pause)
    status, _, content = api.call("GET", f"/files/{file_id}/download", user_token)
    check("download returns exactly the uploaded bytes", status == 200 and sha(content) == sha(pdf),
          f"sha256 {sha(content)[:16]}...")  # fmt: skip
    if object_path.is_file():
        original = object_path.read_bytes()
        flipped = bytearray(original)
        flipped[5] ^= 1
        object_path.write_bytes(bytes(flipped))
        try:
            status, body, _ = api.call("GET", f"/files/{file_id}/download", user_token)
        finally:
            object_path.write_bytes(original)  # always put the stored object back
        check("flipping ONE byte of the stored object is detected (HTTP 502)", status == 502, body.get("detail", "") if body else "")  # fmt: skip
        status, _, content = api.call("GET", f"/files/{file_id}/download", user_token)
        check("after restoring the byte the download works again", status == 200 and sha(content) == sha(pdf))

    # ---- 7. access log ----------------------------------------------------------------------
    section(7, "ACCESS LOG rows written by those steps", "Q7", pause)
    db.show("SELECT a.log_id, a.action, a.outcome, a.latency_ms, r.region_code AS served_by, "
            "TO_CHAR(a.access_time,'HH24:MI:SS') AS at FROM access_logs a "
            "LEFT JOIN provider_regions r ON r.region_id = a.served_by_region_id "
            "WHERE a.file_id = :f ORDER BY a.log_id", {"f": file_id})  # fmt: skip

    # ---- 8. reports -------------------------------------------------------------------------
    section(8, "REPORTS: duplicate files and cold files (read-only)", "Q8", pause)
    status, copy, _ = api.call("POST", "/files", user_token, raw=pdf,
                               params={"file_name": f"demo_report_{run_id}_copy.pdf", "security_level": "HIGH"})  # fmt: skip
    created.append(("user", copy["file_id"]))
    note(f"uploaded the SAME PDF again as {copy['file_name']} to create a duplicate")
    status, dups, _ = api.call("GET", "/reports/duplicates", user_token)
    mine = [g for g in dups["groups"] if any(f["file_name"].startswith(f"demo_report_{run_id}") for f in g["files"])]  # fmt: skip
    check("duplicate report finds the demo duplicate", status == 200 and len(mine) == 1 and mine[0]["copies"] == 2)  # fmt: skip
    for g in mine:
        note(f"group {g['content_sha256'][:16]}...: {g['copies']} copies, {g['wasted_bytes']} wasted bytes, "
             f"est. ${g['estimated_monthly_waste_usd']}/month (illustrative prices)")  # fmt: skip
        table(["file", "role", "provider", "region", "class"],
              [[f["file_name"], f["role"], f["provider"], f["region"], f["storage_class"]] for f in g["files"]])  # fmt: skip
    note("(a 603-byte file wastes far less than one millionth of a dollar a month; the money only matters for big files)")
    note("limits: " + dups["notes"][1])
    status, cold, _ = api.call("GET", "/reports/cold-files", user_token)
    note(f"cold files in the demo user's account: {cold['files_found']} (the files were just uploaded)")
    if args.no_sample:
        note("--no-sample: skipping the synthetic cold-file sample")
    else:
        note("SYNTHETIC SAMPLE (direct database write, made-up files SYN_RPT_*, removed at the end):")
        sample_data.seed(db.conn)
        status, cold, _ = api.call("GET", "/reports/cold-files", auditor_token)
        shown = [f for f in cold["files"] if f["file_name"].startswith("SYN_RPT_")]
        thresholds = cold["thresholds"]
        note(f"as the AUDITOR (sees all owners). Cold = idle more than {thresholds['idle_days_more_than']} days "
             f"and fewer than {thresholds['downloads_fewer_than']} downloads in {thresholds['download_window_days']} days")  # fmt: skip
        table(["file", "idle days", "dl 30d", "dl window", "provider/region", "class", "label"],
              [[f["file_name"], f["idle_days"], f["downloads_last_30_days"], f["downloads_in_window"],
                f"{f['provider']}/{f['region']}", f["storage_class"], f["label"]] for f in shown])  # fmt: skip
        got = {f["file_name"] for f in shown}
        expected = {n for names in sample_data.EXPECTED_COLD.values() for n in names}
        check("cold report lists exactly the known cold sample files", got == expected)
        note("evidence: " + next(f["evidence"] for f in shown if f["file_name"] == "SYN_RPT_cold1.pdf"))
        status, strict, _ = api.call("GET", "/reports/cold-files", auditor_token, params={"idle_days": 250})
        got = {f["file_name"] for f in strict["files"] if f["file_name"].startswith("SYN_RPT_")}
        check("a stricter threshold (idle_days=250) shrinks the list", got == sample_data.EXPECTED_COLD_IDLE_250, ", ".join(sorted(got)))  # fmt: skip
    note("both reports only READ: nothing was moved, archived or deleted.")

    # ---- 9. RBAC ----------------------------------------------------------------------------
    section(9, "UNAUTHORIZED ACTIONS are denied and audited (RBAC)", "Q9", pause)
    new_provider = {"provider_code": "DEMOCO", "provider_name": "Demo Co", "max_object_bytes": 1000}
    status, body, _ = api.call("POST", "/providers", user_token, new_provider)
    check("USER tries to register a provider: refused (403)", status == 403, body["detail"])
    status, body, _ = api.call("GET", "/protected/audit", user_token)
    check("USER tries to read the audit trail: refused (403)", status == 403)
    status, body, _ = api.call("POST", "/providers", auditor_token, new_provider)
    check("AUDITOR (read-only role) tries to register a provider: refused (403)", status == 403)
    status, body, _ = api.call("GET", "/files/" + str(file_id) + "/download", auditor_token)
    check("AUDITOR tries to download the user's file: refused (403)", status == 403)
    status, body, _ = api.call("GET", "/protected/audit", auditor_token)
    check("AUDITOR may read the audit trail (allowed, 200)", status == 200, f"role {body['role']}")
    status, body, _ = api.call("GET", "/providers", None)
    check("no token at all: 401", status == 401, body["detail"])
    note("the denials are in the audit trail:")
    db.show("SELECT event_id, event_type, outcome, actor_user_id AS actor, details_json FROM audit_events "
            "WHERE actor_user_id IN (SELECT user_id FROM users WHERE email IN (:a, :b)) AND outcome = 'DENIED' "
            "ORDER BY event_id DESC FETCH FIRST 4 ROWS ONLY", {"a": USER_EMAIL, "b": AUDITOR_EMAIL})  # fmt: skip

    # ---- 10. cleanup ------------------------------------------------------------------------
    section(10, "CLEAN UP", "Q10", pause)
    if args.keep:
        note("--keep: the demo files and the synthetic sample were left in place")
    else:
        for _, fid in created:
            status, body, _ = api.call("DELETE", f"/files/{fid}", user_token, params={"confirm": "true"})
            check(f"delete file {fid} (key erased, object removed)", status == 200 and body["crypto_erased"] is True)  # fmt: skip
        if not args.no_sample:
            sample_data.cleanup(db.conn)
            note("synthetic sample removed")
    note(f"demo accounts {USER_EMAIL} and {AUDITOR_EMAIL} remain (synthetic); user_id {user_id}")

    passed = sum(ok for _, ok in CHECKS)
    print("\n" + "=" * WIDTH)
    print(f" RESULT: {passed} of {len(CHECKS)} checks passed   [SIMULATION MODE]")
    for label, ok in CHECKS:
        if not ok:
            print(f"   FAILED: {label}")
    print("=" * WIDTH)
    return 0 if passed == len(CHECKS) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--pause", action="store_true", help="wait for Enter between sections")
    parser.add_argument("--keep", action="store_true", help="do not delete the demo files or sample")
    parser.add_argument("--cleanup-only", action="store_true", help="delete earlier demo files and the sample, then stop")  # fmt: skip
    parser.add_argument("--no-sample", action="store_true", help="skip the synthetic cold-file sample")
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
