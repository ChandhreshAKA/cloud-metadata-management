r"""SYNTHETIC sample data for the duplicate and cold-file reports. Reproducible and removable.

    python -m app.reports.sample_data seed       # (re)create the sample, print what to expect
    python -m app.reports.sample_data cleanup    # remove it again

Everything here is made up. Two sample owners (rptsyn_a@example.test, rptsyn_b@example.test, no
password, so nobody can log in as them) own files named SYN_RPT_*. Every timestamp is "N days
before the moment you run seed", so the same expectations hold on any day. The files have no real
content: only metadata rows exist (and no encryption row), which is all the reports read.

The sample contains KNOWN duplicate groups, KNOWN cold files, and files that look like duplicates
or cold files but must NOT be reported (the false-positive traps). `EXPECTED_*` below is the
answer key that tests/integration/test_oracle_reports.py checks the real SQL against.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field
from typing import Any

OWNER_A = "rptsyn_a@example.test"
OWNER_B = "rptsyn_b@example.test"
PREFIX = "SYN_RPT_"
GIB = 2**30
MIB = 2**20

_NOW = "CAST(SYSTIMESTAMP AS TIMESTAMP)"


def sha(label: str) -> str:
    """Deterministic fake content hash: two files share a hash exactly when they share a label."""
    return hashlib.sha256(f"synthetic report content: {label}".encode()).hexdigest()


@dataclass(frozen=True)
class Spec:
    name: str
    owner: str
    size: int
    content: str | None  # label that decides the hash; None = no hash (legacy row)
    uploaded_days_ago: int
    place: tuple[str, str, str] | None  # (provider_code, region_code, tier) or None = no location
    downloads: tuple[tuple[int, str], ...] = field(default_factory=tuple)  # (days ago, outcome)
    deleted: bool = False


_RECENT = ((1, "SUCCESS"),)  # one download yesterday keeps a file from being cold

AWS_IN = ("AWS", "ap-south-1")
AWS_US = ("AWS", "us-east-1")
AZ_IN = ("AZURE", "Central India")
AZ_US = ("AZURE", "East US")
GCP_IN = ("GCP", "asia-south1")

SPECS: tuple[Spec, ...] = (
    # ---- known duplicate groups -------------------------------------------------------------
    # D1: 3 copies of 1 GiB in three different clouds. Original = _a (oldest).
    Spec("SYN_RPT_dup1_a.bin", OWNER_A, GIB, "D1", 300, (*AWS_IN, "HOT"), _RECENT),
    Spec("SYN_RPT_dup1_b.bin", OWNER_A, GIB, "D1", 200, (*AZ_IN, "HOT"), _RECENT),
    Spec("SYN_RPT_dup1_c.bin", OWNER_A, GIB, "D1", 100, (*GCP_IN, "HOT"), _RECENT),
    # D2: 2 copies of 1 MiB in the same region.
    Spec("SYN_RPT_dup2_a.csv", OWNER_A, MIB, "D2", 50, (*AWS_US, "HOT"), _RECENT),
    Spec("SYN_RPT_dup2_b.csv", OWNER_A, MIB, "D2", 40, (*AWS_US, "HOT"), _RECENT),
    # D3 (owner B): 2 copies of 512 MiB; the extra copy has NO location, so it cannot be priced.
    Spec("SYN_RPT_dup3_a.bin", OWNER_B, GIB // 2, "D3", 60, (*AZ_US, "HOT"), _RECENT),
    Spec("SYN_RPT_dup3_b.bin", OWNER_B, GIB // 2, "D3", 30, None, _RECENT),
    # ---- traps: must NOT be reported as duplicates ------------------------------------------
    # same size, different content ("near-duplicates"): different hashes
    Spec("SYN_RPT_near_a.bin", OWNER_A, GIB, "N1a", 70, (*AWS_IN, "HOT"), _RECENT),
    Spec("SYN_RPT_near_b.bin", OWNER_A, GIB, "N1b", 70, (*AWS_IN, "HOT"), _RECENT),
    # same content but one copy is deleted: only one live copy remains
    Spec("SYN_RPT_deldup_a.bin", OWNER_A, MIB, "N2", 80, (*AWS_IN, "HOT"), _RECENT),
    Spec("SYN_RPT_deldup_b.bin", OWNER_A, MIB, "N2", 75, (*AWS_IN, "HOT"), (), True),
    # same content, DIFFERENT owners: not reported
    Spec("SYN_RPT_cross_a.bin", OWNER_A, MIB, "N3", 20, (*AWS_IN, "HOT"), _RECENT),
    Spec("SYN_RPT_cross_b.bin", OWNER_B, MIB, "N3", 20, (*AWS_IN, "HOT"), _RECENT),
    # two legacy rows without a hash: cannot be grouped
    Spec("SYN_RPT_nohash_a.bin", OWNER_A, 1000, None, 15, (*AWS_IN, "HOT"), _RECENT),
    Spec("SYN_RPT_nohash_b.bin", OWNER_A, 1000, None, 15, (*AWS_IN, "HOT"), _RECENT),
    # ---- known cold files (default thresholds: idle > 90 days, fewer than 3 downloads/365 d) --
    Spec("SYN_RPT_cold1.pdf", OWNER_A, 2 * GIB, "C1", 400, (*AWS_IN, "COOL"), ((300, "SUCCESS"),)),
    Spec("SYN_RPT_cold2.pdf", OWNER_A, GIB, "C2", 200, (*AZ_US, "HOT")),  # never downloaded
    Spec(  # a failed and a denied attempt recently do NOT count as access
        "SYN_RPT_cold3.pdf", OWNER_A, MIB, "C3", 250, (*GCP_IN, "HOT"),
        ((220, "SUCCESS"), (2, "FAILURE"), (1, "DENIED")),
    ),
    Spec(  # the 400-day-old download is outside the 365-day window, the 120-day-old one is inside
        "SYN_RPT_cold4.zip", OWNER_B, 4 * MIB, "C4", 500, (*AZ_IN, "COLD"),
        ((400, "SUCCESS"), (120, "SUCCESS")),
    ),
    Spec("SYN_RPT_edge91.bin", OWNER_A, MIB, "E91", 200, (*AWS_IN, "HOT"), ((91, "SUCCESS"),)),
    # ---- traps: must NOT be reported as cold (with the default thresholds) ------------------
    Spec(  # downloaded 5 times recently
        "SYN_RPT_hot1.docx", OWNER_A, MIB, "H1", 400, (*AWS_IN, "HOT"),
        ((1, "SUCCESS"), (3, "SUCCESS"), (10, "SUCCESS"), (20, "SUCCESS"), (25, "SUCCESS")),
    ),
    Spec(  # idle 100 days BUT 5 downloads in the window, so not "few accesses"
        "SYN_RPT_warm.xlsx", OWNER_A, MIB, "W1", 300, (*AWS_IN, "HOT"),
        ((100, "SUCCESS"), (110, "SUCCESS"), (120, "SUCCESS"), (130, "SUCCESS"), (140, "SUCCESS")),
    ),
    Spec("SYN_RPT_edge90.bin", OWNER_A, MIB, "E90", 200, (*AWS_IN, "HOT"), ((90, "SUCCESS"),)),
    Spec("SYN_RPT_new.txt", OWNER_A, 1000, "NEW", 10, (*AWS_IN, "HOT")),  # too young to be idle
    Spec("SYN_RPT_delcold.pdf", OWNER_A, MIB, "DC", 500, (*AWS_IN, "HOT"), (), True),  # deleted
)

# Answer key, default thresholds (idle_days 90, max_accesses 3, window_days 365).
EXPECTED_DUPLICATE_GROUPS: dict[str, dict[str, Any]] = {
    # label: files in upload order (first = original), wasted bytes
    "D1": {"owner": OWNER_A, "wasted_bytes": 2 * GIB,
           "files": ["SYN_RPT_dup1_a.bin", "SYN_RPT_dup1_b.bin", "SYN_RPT_dup1_c.bin"]},
    "D2": {"owner": OWNER_A, "files": ["SYN_RPT_dup2_a.csv", "SYN_RPT_dup2_b.csv"],
           "wasted_bytes": MIB},
    "D3": {"owner": OWNER_B, "files": ["SYN_RPT_dup3_a.bin", "SYN_RPT_dup3_b.bin"],
           "wasted_bytes": GIB // 2},
}  # fmt: skip
EXPECTED_COLD = {
    OWNER_A: {"SYN_RPT_cold1.pdf", "SYN_RPT_cold2.pdf", "SYN_RPT_cold3.pdf", "SYN_RPT_edge91.bin"},
    OWNER_B: {"SYN_RPT_cold4.zip"},
}
# How the answer changes with thresholds (owner A unless noted); used by the tests and the demo.
EXPECTED_COLD_MAX_ACCESSES_10_EXTRA = {"SYN_RPT_warm.xlsx"}  # a looser access limit adds it
EXPECTED_COLD_IDLE_60_EXTRA = {"SYN_RPT_edge90.bin"}  # a lower idle limit adds it
EXPECTED_COLD_IDLE_250 = {"SYN_RPT_cold1.pdf"}  # a higher idle limit leaves only the oldest

_DELETE_LOGS = """
    DELETE FROM access_logs WHERE file_id IN
        (SELECT file_id FROM files WHERE file_name LIKE 'SYN\\_RPT\\_%' ESCAPE '\\')
"""
_DELETE_FILES = r"DELETE FROM files WHERE file_name LIKE 'SYN\_RPT\_%' ESCAPE '\'"
_DELETE_USERS = r"DELETE FROM users WHERE email LIKE 'rptsyn\_%@example.test' ESCAPE '\'"
_INSERT_USER = """
    INSERT INTO users (full_name, email, role, account_status)
    VALUES (:name, :email, 'USER', 'PENDING_RESET')
"""
_USER_ID = "SELECT user_id FROM users WHERE email = :email"
_REGION_ID = """
    SELECT r.region_id FROM provider_regions r
      JOIN cloud_providers p ON p.provider_id = r.provider_id
     WHERE p.provider_code = :provider AND r.region_code = :region
"""
_INSERT_FILE = f"""
    INSERT INTO files (owner_id, file_name, file_size_bytes, file_type, security_level, status,
                       content_sha256, upload_time, deleted_at)
    VALUES (:owner_id, :file_name, :size_bytes, 'OTHER', 'LOW', :status, :sha,
            {_NOW} - NUMTODSINTERVAL(:up_days, 'DAY'),
            CASE WHEN :status = 'DELETED' THEN {_NOW} END)
    RETURNING file_id INTO :new_id
"""  # noqa: S608  (only the constant _NOW is interpolated)
_INSERT_LOCATION = """
    INSERT INTO storage_locations
        (file_id, region_id, tier_code, object_key, is_primary, replica_status)
    VALUES (:file_id, :region_id, :tier, :object_key, 'Y', 'OK')
"""
_INSERT_LOG = f"""
    INSERT INTO access_logs (file_id, user_id, action, outcome, access_time)
    VALUES (:file_id, :user_id, 'DOWNLOAD', :outcome, {_NOW} - NUMTODSINTERVAL(:days, 'DAY'))
"""  # noqa: S608  (only the constant _NOW is interpolated)


def cleanup(connection: Any) -> None:
    """Delete only rows this module created (file names SYN_RPT_*, users rptsyn_*@example.test)."""
    cursor = connection.cursor()
    cursor.execute(_DELETE_LOGS)
    cursor.execute(_DELETE_FILES)  # locations cascade
    cursor.execute(_DELETE_USERS)
    connection.commit()


def seed(connection: Any) -> dict[str, int]:
    """Recreate the sample. Returns {owner email: user_id}. Safe to run repeatedly."""
    cleanup(connection)
    cursor = connection.cursor()
    user_ids: dict[str, int] = {}
    owners = ((OWNER_A, "Report Sample A (synthetic)"), (OWNER_B, "Report Sample B (synthetic)"))
    for email, name in owners:
        cursor.execute(_INSERT_USER, {"name": name, "email": email})
        cursor.execute(_USER_ID, {"email": email})
        user_ids[email] = int(cursor.fetchone()[0])

    for spec in SPECS:
        new_id = cursor.var(int)
        cursor.execute(
            _INSERT_FILE,
            {
                "owner_id": user_ids[spec.owner], "file_name": spec.name, "size_bytes": spec.size,
                "status": "DELETED" if spec.deleted else "ACTIVE",
                "sha": None if spec.content is None else sha(spec.content),
                "up_days": spec.uploaded_days_ago, "new_id": new_id,
            },
        )  # fmt: skip
        file_id = int(new_id.getvalue()[0])
        if spec.place is not None:
            provider, region, tier = spec.place
            cursor.execute(_REGION_ID, {"provider": provider, "region": region})
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError(f"region {provider}/{region} missing: run sql/seed_data.sql")
            cursor.execute(
                _INSERT_LOCATION,
                {"file_id": file_id, "region_id": int(row[0]), "tier": tier,
                 "object_key": f"synthetic/{spec.name}"},
            )  # fmt: skip
        for days_ago, outcome in spec.downloads:
            cursor.execute(
                _INSERT_LOG,
                {"file_id": file_id, "user_id": user_ids[spec.owner], "outcome": outcome,
                 "days": days_ago},
            )  # fmt: skip
    connection.commit()
    return user_ids


def _connect() -> Any:
    import oracledb
    from dotenv import dotenv_values

    env = dotenv_values()
    return oracledb.connect(
        user=env["ORACLE_USER"], password=env["ORACLE_PASSWORD"], dsn=env["ORACLE_DSN"]
    )


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in {"seed", "cleanup"}:
        print(__doc__)
        return 2
    connection = _connect()
    try:
        if argv[0] == "cleanup":
            cleanup(connection)
            print("synthetic report sample removed")
            return 0
        ids = seed(connection)
        print("SYNTHETIC report sample created (made-up data, safe to delete).")
        print(f"  owners: {OWNER_A} (user_id {ids[OWNER_A]}), {OWNER_B} (user_id {ids[OWNER_B]})")
        print(f"  files: {len(SPECS)} rows named {PREFIX}*")
        print("Expected, default thresholds (idle > 90 days, < 3 downloads in 365 days):")
        for label, group in EXPECTED_DUPLICATE_GROUPS.items():
            names = ", ".join(group["files"])
            print(f"  duplicate group {label}: {len(group['files'])} copies, "
                  f"{group['wasted_bytes']} wasted bytes  ({names})")
        for owner, names in EXPECTED_COLD.items():
            print(f"  cold files of {owner}: {', '.join(sorted(names))}")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
