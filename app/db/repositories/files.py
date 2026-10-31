"""SQL for files, file_encryption, storage_locations, selection decisions and access logs.

Names follow sql/schema.sql (Chunk 1). Every statement is a module-level constant with bind
variables; nothing from a request is concatenated into SQL.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from app.files.models import StoredFile

_MAX_EXPLANATION = 2000  # selection_decisions.explanation is VARCHAR2(2000)

_INSERT_FILE = """
    INSERT INTO files (owner_id, file_name, file_size_bytes, file_type, security_level, status,
                       content_sha256, desired_replicas, expected_accesses_per_month)
    VALUES (:owner_id, :file_name, :size_bytes, :file_type, :security_level, 'ACTIVE',
            :content_sha256, 1, :expected_accesses)
    RETURNING file_id INTO :new_id
"""
_INSERT_ENCRYPTION = """
    INSERT INTO file_encryption (file_id, algorithm, wrapped_dek, nonce, master_key_version,
                                 ciphertext_sha256)
    VALUES (:file_id, :algorithm, :wrapped_dek, :nonce, :key_version, :ciphertext_sha256)
"""
_INSERT_LOCATION = """
    INSERT INTO storage_locations (file_id, region_id, tier_code, object_key, is_primary,
                                   replica_status)
    VALUES (:file_id, :region_id, :tier_code, :object_key, 'Y', 'OK')
    RETURNING location_id INTO :new_id
"""
_INSERT_DECISION = """
    INSERT INTO selection_decisions (file_id, profile_name, weights_json, chosen_region_id,
                                     explanation)
    VALUES (:file_id, :profile_name, :weights_json, :chosen_region_id, :explanation)
    RETURNING decision_id INTO :new_id
"""
_INSERT_CANDIDATE = """
    INSERT INTO selection_candidates
        (decision_id, region_id, excluded_reason, raw_json, norm_storage_cost, norm_transfer_cost,
         norm_latency, norm_reliability, norm_security, total_score)
    VALUES
        (:decision_id, :region_id, :excluded_reason, :raw_json, :norm_storage_cost,
         :norm_transfer_cost, :norm_latency, :norm_reliability, :norm_security, :total_score)
"""
_INSERT_ACCESS_LOG = """
    INSERT INTO access_logs (file_id, user_id, action, outcome, latency_ms, served_by_region_id)
    VALUES (:file_id, :user_id, :action, :outcome, :latency_ms, :region_id)
"""
_GET_FOR_ACCESS = """
    SELECT f.file_id, f.owner_id, f.file_name, f.file_size_bytes, f.file_type, f.security_level,
           f.status, f.content_sha256,
           sl.location_id, sl.region_id, p.provider_code, r.region_code, sl.object_key,
           sl.tier_code,
           e.wrapped_dek, e.nonce, e.master_key_version, e.ciphertext_sha256
      FROM files f
      LEFT JOIN storage_locations sl ON sl.file_id = f.file_id AND sl.is_primary = 'Y'
      LEFT JOIN provider_regions r ON r.region_id = sl.region_id
      LEFT JOIN cloud_providers p ON p.provider_id = r.provider_id
      LEFT JOIN file_encryption e ON e.file_id = f.file_id
     WHERE f.file_id = :file_id
"""
_LIST_FOR_OWNER = """
    SELECT f.file_id, f.file_name, f.file_size_bytes, f.file_type, f.security_level, f.status,
           f.content_sha256, f.upload_time, p.provider_code, r.region_code, sl.tier_code
      FROM files f
      LEFT JOIN storage_locations sl ON sl.file_id = f.file_id AND sl.is_primary = 'Y'
      LEFT JOIN provider_regions r ON r.region_id = sl.region_id
      LEFT JOIN cloud_providers p ON p.provider_id = r.provider_id
     WHERE f.owner_id = :owner_id AND f.status <> 'DELETED'
     ORDER BY f.file_id DESC
     FETCH FIRST 500 ROWS ONLY
"""
_LATEST_DECISION = """
    SELECT decision_id, profile_name, weights_json, explanation, decided_at, chosen_region_id
      FROM selection_decisions
     WHERE file_id = :file_id
     ORDER BY decision_id DESC
     FETCH FIRST 1 ROWS ONLY
"""
_DECISION_CANDIDATES = """
    SELECT c.region_id, p.provider_code, r.region_code, c.excluded_reason, c.total_score, c.raw_json
      FROM selection_candidates c
      JOIN provider_regions r ON r.region_id = c.region_id
      JOIN cloud_providers p ON p.provider_id = r.provider_id
     WHERE c.decision_id = :decision_id
     ORDER BY c.total_score DESC NULLS LAST, c.region_id
"""
_MARK_DELETED = """
    UPDATE files SET status = 'DELETED', deleted_at = SYSTIMESTAMP
     WHERE file_id = :file_id AND status <> 'DELETED'
"""
_DELETE_ENCRYPTION = "DELETE FROM file_encryption WHERE file_id = :file_id"
_SET_LOCATION_STATUS = (
    "UPDATE storage_locations SET replica_status = :status WHERE location_id = :location_id"
)
_DELETE_LOCATION = "DELETE FROM storage_locations WHERE location_id = :location_id"


def _to_bytes(value: Any) -> bytes | None:
    return None if value is None else bytes(value)


class OracleFileRepository:
    def __init__(self, connection: Any):
        self._conn = connection

    def _insert_returning(self, sql: str, binds: dict[str, Any]) -> int:
        cursor = self._conn.cursor()
        new_id = cursor.var(int)
        cursor.execute(sql, {**binds, "new_id": new_id})
        return int(new_id.getvalue()[0])

    def insert_file(
        self,
        owner_id: int,
        file_name: str,
        size_bytes: int,
        file_type: str,
        security_level: str,
        content_sha256: str,
        expected_accesses: int,
    ) -> int:
        return self._insert_returning(
            _INSERT_FILE,
            {
                "owner_id": owner_id,
                "file_name": file_name,
                "size_bytes": size_bytes,
                "file_type": file_type,
                "security_level": security_level,
                "content_sha256": content_sha256,
                "expected_accesses": expected_accesses,
            },
        )

    def insert_encryption(
        self,
        file_id: int,
        algorithm: str,
        wrapped_dek: bytes,
        nonce: bytes,
        master_key_version: int,
        ciphertext_sha256: str,
    ) -> None:
        self._conn.cursor().execute(
            _INSERT_ENCRYPTION,
            {
                "file_id": file_id,
                "algorithm": algorithm,
                "wrapped_dek": wrapped_dek,
                "nonce": nonce,
                "key_version": master_key_version,
                "ciphertext_sha256": ciphertext_sha256,
            },
        )

    def insert_location(self, file_id: int, region_id: int, tier_code: str, object_key: str) -> int:
        return self._insert_returning(
            _INSERT_LOCATION,
            {
                "file_id": file_id,
                "region_id": region_id,
                "tier_code": tier_code,
                "object_key": object_key,
            },
        )

    def insert_decision(
        self,
        file_id: int,
        profile_name: str,
        weights: dict[str, str],
        chosen_region_id: int | None,
        explanation: str,
    ) -> int:
        text = explanation if len(explanation) <= _MAX_EXPLANATION else explanation[:1996] + " ..."
        return self._insert_returning(
            _INSERT_DECISION,
            {
                "file_id": file_id,
                "profile_name": profile_name,
                "weights_json": json.dumps(weights),
                "chosen_region_id": chosen_region_id,
                "explanation": text,
            },
        )

    def insert_candidate(
        self,
        decision_id: int,
        region_id: int,
        excluded_reason: str | None,
        raw: dict[str, str] | None,
        normalised: dict[str, Decimal] | None,
        total_score: Decimal | None,
    ) -> None:
        n = normalised or {}
        self._conn.cursor().execute(
            _INSERT_CANDIDATE,
            {
                "decision_id": decision_id,
                "region_id": region_id,
                "excluded_reason": None if excluded_reason is None else excluded_reason[:200],
                "raw_json": None if raw is None else json.dumps(raw),
                "norm_storage_cost": n.get("storage_cost"),
                "norm_transfer_cost": n.get("transfer_cost"),
                "norm_latency": n.get("latency"),
                "norm_reliability": n.get("reliability"),
                "norm_security": n.get("security"),
                "total_score": total_score,
            },
        )

    def insert_access_log(
        self,
        file_id: int,
        user_id: int,
        action: str,
        outcome: str,
        latency_ms: Decimal | None,
        region_id: int | None,
    ) -> None:
        self._conn.cursor().execute(
            _INSERT_ACCESS_LOG,
            {
                "file_id": file_id,
                "user_id": user_id,
                "action": action,
                "outcome": outcome,
                "latency_ms": latency_ms,
                "region_id": region_id,
            },
        )

    def get_for_access(self, file_id: int) -> StoredFile | None:
        cursor = self._conn.cursor()
        cursor.execute(_GET_FOR_ACCESS, {"file_id": file_id})
        row = cursor.fetchone()
        if row is None:
            return None
        return StoredFile(
            file_id=int(row[0]), owner_id=int(row[1]), file_name=row[2],
            file_size_bytes=int(row[3]), file_type=row[4], security_level=row[5], status=row[6],
            content_sha256=row[7],
            location_id=None if row[8] is None else int(row[8]),
            region_id=None if row[9] is None else int(row[9]),
            provider_code=row[10], region_code=row[11], object_key=row[12], tier_code=row[13],
            wrapped_dek=_to_bytes(row[14]), nonce=_to_bytes(row[15]),
            master_key_version=None if row[16] is None else int(row[16]),
            ciphertext_sha256=row[17],
        )  # fmt: skip

    def list_for_owner(self, owner_id: int) -> list[dict[str, Any]]:
        cursor = self._conn.cursor()
        cursor.execute(_LIST_FOR_OWNER, {"owner_id": owner_id})
        return [
            {
                "file_id": int(r[0]), "file_name": r[1], "file_size_bytes": int(r[2]),
                "file_type": r[3], "security_level": r[4], "status": r[5],
                "content_sha256": r[6], "uploaded_at": r[7].isoformat(),
                "provider_code": r[8], "region_code": r[9], "tier_code": r[10],
            }
            for r in cursor.fetchall()
        ]  # fmt: skip

    def get_decision(self, file_id: int) -> dict[str, Any] | None:
        cursor = self._conn.cursor()
        cursor.execute(_LATEST_DECISION, {"file_id": file_id})
        head = cursor.fetchone()
        if head is None:
            return None
        decision_id = int(head[0])
        cursor.execute(_DECISION_CANDIDATES, {"decision_id": decision_id})
        candidates = [
            {
                "region_id": int(c[0]), "provider_code": c[1], "region_code": c[2],
                "excluded_reason": c[3], "composite": None if c[4] is None else str(c[4]),
                "raw": None if c[5] is None else json.loads(c[5]),
            }
            for c in cursor.fetchall()
        ]  # fmt: skip
        return {
            "decision_id": decision_id, "preset": head[1], "weights": json.loads(head[2]),
            "explanation": head[3], "decided_at": head[4].isoformat(),
            "chosen_region_id": None if head[5] is None else int(head[5]),
            "candidates": candidates,
        }  # fmt: skip

    def mark_deleted(self, file_id: int) -> bool:
        cursor = self._conn.cursor()
        cursor.execute(_MARK_DELETED, {"file_id": file_id})
        return cursor.rowcount > 0

    def delete_encryption(self, file_id: int) -> None:
        self._conn.cursor().execute(_DELETE_ENCRYPTION, {"file_id": file_id})

    def set_location_status(self, location_id: int, status: str) -> None:
        self._conn.cursor().execute(
            _SET_LOCATION_STATUS, {"status": status, "location_id": location_id}
        )

    def delete_location(self, location_id: int) -> None:
        self._conn.cursor().execute(_DELETE_LOCATION, {"location_id": location_id})
