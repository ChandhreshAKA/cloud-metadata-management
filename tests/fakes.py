"""Test doubles. NOTHING in this file talks to Oracle.

FakeDatabase: an in-memory implementation of app.db.base.Database. Tests that use it prove the
APPLICATION LOGIC (auth flow, RBAC, error mapping, audit decisions). They do NOT prove that the
SQL is valid on Oracle or that Oracle enforces the constraints; tests/integration does that.

RecordingConnection: records (sql, binds) passed to the REAL repository classes, to prove that
user input only ever travels as bind values.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from statistics import median
from typing import Any

from app.db.base import UserRecord
from app.db.errors import (
    CheckViolationError,
    DatabaseError,
    DatabaseUnavailableError,
    ReferenceViolationError,
    UniqueViolationError,
)
from app.files.models import StoredFile
from app.providers.models import PricingRow, ProviderRow, RegionRow
from app.selection.models import Candidate, Policy, TierPricing, Weights
from app.selection.presets import DEFAULT_POLICY, PRESETS


class _FakeUsers:
    def __init__(self, state: dict[str, Any]):
        self._s = state

    def create(self, full_name: str, email: str, password_hash: str) -> int:
        if any(u.email == email for u in self._s["users"].values()):
            raise UniqueViolationError(
                "ORA-00001: unique constraint (CLOUDMETA.UQ_USERS_EMAIL) violated", "UQ_USERS_EMAIL"
            )
        self._s["next_id"] += 1
        user_id = self._s["next_id"]
        self._s["users"][user_id] = UserRecord(
            user_id, full_name, email, "USER", password_hash, "ACTIVE"
        )
        return user_id

    def get_by_email(self, email: str) -> UserRecord | None:
        return next((u for u in self._s["users"].values() if u.email == email), None)

    def get_by_id(self, user_id: int) -> UserRecord | None:
        return self._s["users"].get(user_id)

    def touch_last_login(self, user_id: int) -> None:
        self._s["last_login"][user_id] = self._s["last_login"].get(user_id, 0) + 1


class _FakeAudit:
    def __init__(self, state: dict[str, Any], database: FakeDatabase):
        self._s = state
        self._db = database

    def record(
        self,
        event_type: str,
        outcome: str,
        *,
        actor_user_id: int | None = None,
        target_type: str | None = None,
        target_id: int | None = None,
        ip_address: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self._db.fail_audit_with is not None:
            raise self._db.fail_audit_with
        self._s["audit"].append(
            {
                "event_type": event_type,
                "outcome": outcome,
                "actor_user_id": actor_user_id,
                "target_type": target_type,
                "target_id": target_id,
                "ip_address": ip_address,
                "details": details,
            }
        )


class _FakeProviders:
    """In-memory provider registry. Mimics the constraints that matter (unique codes, score range,
    tier foreign key) by raising the same domain errors the Oracle layer raises."""

    TIERS = ("HOT", "COOL", "COLD", "ARCHIVE")

    def __init__(self, state: dict[str, Any]):
        self._s = state

    def _next(self, key: str) -> int:
        self._s[key] += 1
        return self._s[key]

    def list_providers(self) -> list[ProviderRow]:
        return sorted(self._s["providers"].values(), key=lambda p: p.provider_id)

    def get_provider(self, provider_id: int) -> ProviderRow | None:
        return self._s["providers"].get(provider_id)

    def create_provider(self, code: str, name: str, max_object_bytes: int) -> int:
        for p in self._s["providers"].values():
            if p.provider_code == code:
                raise UniqueViolationError("ORA-00001 ...", "UQ_PROVIDERS_CODE")
            if p.provider_name == name:
                raise UniqueViolationError("ORA-00001 ...", "UQ_PROVIDERS_NAME")
        provider_id = self._next("next_provider_id")
        self._s["providers"][provider_id] = ProviderRow(
            provider_id, code, name, max_object_bytes, True
        )
        return provider_id

    def update_provider(self, row: ProviderRow) -> None:
        self._s["providers"][row.provider_id] = row

    def list_regions(self, provider_id: int | None = None) -> list[RegionRow]:
        rows = sorted(self._s["regions"].values(), key=lambda r: r.region_id)
        return [r for r in rows if provider_id is None or r.provider_id == provider_id]

    def get_region(self, region_id: int) -> RegionRow | None:
        return self._s["regions"].get(region_id)

    def create_region(self, provider_id, region_code, bucket_name, reliability_score,
                      security_score, egress_usd_per_gb, is_illustrative) -> int:  # fmt: skip
        for r in self._s["regions"].values():
            if r.provider_id == provider_id and r.region_code == region_code:
                raise UniqueViolationError("ORA-00001 ...", "UQ_REGIONS_PROVIDER_CODE")
            if r.provider_id == provider_id and r.bucket_name == bucket_name:
                raise UniqueViolationError("ORA-00001 ...", "UQ_REGIONS_PROVIDER_BUCKET")
        if not (0 <= reliability_score <= 10):
            raise CheckViolationError("ORA-02290 ...", "CK_REGIONS_RELIABILITY")
        region_id = self._next("next_region_id")
        self._s["regions"][region_id] = RegionRow(
            region_id, provider_id, region_code, bucket_name, reliability_score, security_score,
            egress_usd_per_gb, "UP", None, True, is_illustrative,
        )  # fmt: skip
        return region_id

    def update_region(self, row: RegionRow) -> None:
        self._s["regions"][row.region_id] = row

    def list_pricing(self, region_id: int | None = None) -> list[PricingRow]:
        rows = sorted(self._s["pricing"].values(), key=lambda p: (p.region_id, p.tier_code))
        return [p for p in rows if region_id is None or p.region_id == region_id]

    def upsert_pricing(self, row: PricingRow) -> None:
        if row.tier_code not in self.TIERS:
            raise ReferenceViolationError("ORA-02291 ...", "FK_PRICING_TIER")
        for (region_id, tier), other in self._s["pricing"].items():
            if region_id == row.region_id and tier != row.tier_code:
                if other.native_class_name == row.native_class_name:
                    raise UniqueViolationError("ORA-00001 ...", "UQ_PRICING_NATIVE")
        self._s["pricing"][(row.region_id, row.tier_code)] = row

    def candidates(self, tier: str) -> list[Candidate]:
        result = []
        for r in self.list_regions():
            p = self._s["providers"][r.provider_id]
            price = self._s["pricing"].get((r.region_id, tier))
            samples = self._s["latencies"].get(r.region_id, [])
            result.append(
                Candidate(
                    region_id=r.region_id, provider_id=r.provider_id, provider_code=p.provider_code,
                    provider_name=p.provider_name, region_code=r.region_code,
                    provider_active=p.is_active, region_active=r.is_active,
                    health_status=r.health_status, reliability_score=r.reliability_score,
                    security_score=r.security_score, max_object_bytes=p.max_object_bytes,
                    egress_usd_per_gb=r.egress_usd_per_gb,
                    pricing=None if price is None else TierPricing(
                        price.tier_code, price.storage_usd_per_gb_month, price.retrieval_usd_per_gb,
                        price.put_usd_per_1k, price.get_usd_per_1k, price.min_storage_days),
                    latency_ms=median(samples) if samples else None,
                    is_illustrative=r.is_illustrative,
                )
            )  # fmt: skip
        return result

    def weight_profiles(self) -> tuple[dict[str, Weights], str | None]:
        return dict(self._s["profiles"]), self._s["default_profile"]

    def policy(self) -> Policy:
        return self._s["policy"]


class _FakeFiles:
    """In-memory files, encryption records, locations, decisions and access logs."""

    def __init__(self, state: dict[str, Any]):
        self._s = state

    def _next(self, key: str) -> int:
        self._s[key] += 1
        return self._s[key]

    def insert_file(self, owner_id, file_name, size_bytes, file_type, security_level,
                    content_sha256, expected_accesses) -> int:  # fmt: skip
        file_id = self._next("next_file_id")
        self._s["files"][file_id] = {
            "file_id": file_id, "owner_id": owner_id, "file_name": file_name,
            "file_size_bytes": size_bytes, "file_type": file_type,
            "security_level": security_level, "status": "ACTIVE",
            "content_sha256": content_sha256, "expected_accesses": expected_accesses,
        }  # fmt: skip
        return file_id

    def insert_encryption(self, file_id, algorithm, wrapped_dek, nonce, master_key_version,
                          ciphertext_sha256) -> None:  # fmt: skip
        self._s["encryption"][file_id] = {
            "algorithm": algorithm, "wrapped_dek": wrapped_dek, "nonce": nonce,
            "master_key_version": master_key_version, "ciphertext_sha256": ciphertext_sha256,
        }  # fmt: skip

    def insert_location(self, file_id, region_id, tier_code, object_key) -> int:
        location_id = self._next("next_location_id")
        self._s["locations"][location_id] = {
            "location_id": location_id, "file_id": file_id, "region_id": region_id,
            "tier_code": tier_code, "object_key": object_key, "replica_status": "OK",
        }  # fmt: skip
        return location_id

    def insert_decision(self, file_id, profile_name, weights, chosen_region_id, explanation) -> int:
        decision_id = self._next("next_decision_id")
        self._s["decisions"][decision_id] = {
            "decision_id": decision_id, "file_id": file_id, "profile_name": profile_name,
            "weights": weights, "chosen_region_id": chosen_region_id, "explanation": explanation,
        }  # fmt: skip
        return decision_id

    def insert_candidate(self, decision_id, region_id, excluded_reason, raw, normalised,
                         total_score) -> None:  # fmt: skip
        self._s["candidates"].append({
            "decision_id": decision_id, "region_id": region_id,
            "excluded_reason": excluded_reason, "raw": raw, "normalised": normalised,
            "total_score": total_score,
        })  # fmt: skip

    def insert_access_log(self, file_id, user_id, action, outcome, latency_ms, region_id) -> None:
        self._s["access_logs"].append({
            "file_id": file_id, "user_id": user_id, "action": action, "outcome": outcome,
            "latency_ms": latency_ms, "region_id": region_id,
        })  # fmt: skip

    def get_for_access(self, file_id: int) -> StoredFile | None:
        f = self._s["files"].get(file_id)
        if f is None:
            return None
        loc = next((x for x in self._s["locations"].values() if x["file_id"] == file_id), None)
        enc = self._s["encryption"].get(file_id)
        region = self._s["regions"].get(loc["region_id"]) if loc else None
        provider = self._s["providers"].get(region.provider_id) if region else None
        return StoredFile(
            file_id=file_id, owner_id=f["owner_id"], file_name=f["file_name"],
            file_size_bytes=f["file_size_bytes"], file_type=f["file_type"],
            security_level=f["security_level"], status=f["status"],
            content_sha256=f["content_sha256"],
            location_id=loc["location_id"] if loc else None,
            region_id=loc["region_id"] if loc else None,
            provider_code=provider.provider_code if provider else None,
            region_code=region.region_code if region else None,
            object_key=loc["object_key"] if loc else None,
            tier_code=loc["tier_code"] if loc else None,
            wrapped_dek=enc["wrapped_dek"] if enc else None,
            nonce=enc["nonce"] if enc else None,
            master_key_version=enc["master_key_version"] if enc else None,
            ciphertext_sha256=enc["ciphertext_sha256"] if enc else None,
        )  # fmt: skip

    def mark_deleted(self, file_id: int) -> bool:
        f = self._s["files"].get(file_id)
        if f is None or f["status"] == "DELETED":
            return False
        f["status"] = "DELETED"
        return True

    def delete_encryption(self, file_id: int) -> None:
        self._s["encryption"].pop(file_id, None)

    def set_location_status(self, location_id: int, status: str) -> None:
        self._s["locations"][location_id]["replica_status"] = status

    def delete_location(self, location_id: int) -> None:
        self._s["locations"].pop(location_id, None)


class _FakeReports:
    """Canned report rows. The grouping and threshold SQL is NOT reimplemented here: whether the
    real SQL finds the right files is proved against Oracle (tests/integration/test_oracle_reports.py).
    What this fake lets the unit tests prove is the application layer: scoping by owner, passing the
    thresholds through, the money arithmetic and the response shape."""

    def __init__(self, state: dict[str, Any]):
        self._s = state

    def db_now(self):
        return self._s["report_now"]

    def cold_config(self) -> dict[str, str]:
        return dict(self._s["report_config"])

    def duplicate_rows(self, owner_id):
        rows = self._s["report_duplicate_rows"]
        return [r for r in rows if owner_id is None or r.owner_id == owner_id]

    def cold_rows(self, owner_id, as_of, thresholds):
        self._s["report_cold_calls"].append((owner_id, as_of, thresholds))
        return [
            r for r in self._s["report_cold_rows"]
            if (owner_id is None or r.owner_id == owner_id)
            and r.idle_days > thresholds.idle_days
            and r.downloads_window < thresholds.max_accesses
        ]  # fmt: skip


class FakeSession:
    def __init__(self, state: dict[str, Any], database: FakeDatabase):
        self.users = _FakeUsers(state)
        self.audit = _FakeAudit(state, database)
        self.providers = _FakeProviders(state)
        self.files = _FakeFiles(state)
        self.reports = _FakeReports(state)


class FakeDatabase:
    """In-memory database with real commit/rollback behaviour (snapshot and restore)."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {
            "users": {}, "audit": [], "next_id": 0, "last_login": {},
            "providers": {}, "regions": {}, "pricing": {}, "latencies": {},
            "next_provider_id": 0, "next_region_id": 0,
            "files": {}, "encryption": {}, "locations": {}, "decisions": {}, "candidates": [],
            "access_logs": [], "next_file_id": 0, "next_location_id": 0, "next_decision_id": 0,
            "profiles": dict(PRESETS), "default_profile": "BALANCED", "policy": DEFAULT_POLICY,
            "report_now": datetime(2026, 6, 1, 12, 0, 0), "report_config": {},
            "report_duplicate_rows": [], "report_cold_rows": [], "report_cold_calls": [],
        }  # fmt: skip
        self.available = True
        self.fail_audit_with: Exception | None = None
        self.fail_create_with: Exception | None = None
        self.fail_insert_file_with: Exception | None = None

    @contextmanager
    def transaction(self) -> Iterator[FakeSession]:
        if not self.available:
            raise DatabaseUnavailableError("fake database is down")
        snapshot = copy.deepcopy(self.state)
        session = FakeSession(self.state, self)
        if self.fail_create_with is not None:
            error = self.fail_create_with

            def boom(*_args: Any, **_kwargs: Any) -> int:
                raise error

            session.users.create = boom  # type: ignore[method-assign]
        if self.fail_insert_file_with is not None:
            file_error = self.fail_insert_file_with

            def file_boom(*_args: Any, **_kwargs: Any) -> int:
                raise file_error

            session.files.insert_file = file_boom  # type: ignore[method-assign]
        try:
            yield session
        except BaseException:
            self.state.clear()
            self.state.update(snapshot)
            raise

    def ping(self) -> None:
        if not self.available:
            raise DatabaseUnavailableError("fake database is down")

    def close(self) -> None:
        pass

    # --- helpers for tests -------------------------------------------------------------
    def add_user(
        self,
        email: str,
        password_hash: str | None,
        role: str = "USER",
        status: str = "ACTIVE",
        full_name: str = "Test User",
    ) -> int:
        self.state["next_id"] += 1
        user_id = self.state["next_id"]
        self.state["users"][user_id] = UserRecord(
            user_id, full_name, email, role, password_hash, status
        )
        return user_id

    def set_role(self, user_id: int, role: str) -> None:
        u = self.state["users"][user_id]
        self.state["users"][user_id] = UserRecord(
            u.user_id, u.full_name, u.email, role, u.password_hash, u.account_status
        )

    def set_status(self, user_id: int, status: str) -> None:
        u = self.state["users"][user_id]
        self.state["users"][user_id] = UserRecord(
            u.user_id, u.full_name, u.email, u.role, u.password_hash, status
        )

    @property
    def audit(self) -> list[dict[str, Any]]:
        return self.state["audit"]

    def audit_of(self, event_type: str) -> list[dict[str, Any]]:
        return [e for e in self.state["audit"] if e["event_type"] == event_type]


# --- recording connection (for parameterisation tests) --------------------------------------
class _Var:
    def getvalue(self) -> list[int]:
        return [1]


class RecordingCursor:
    def __init__(self, log: list[tuple[str, Any]], row: tuple[Any, ...] | None = None):
        self._log = log
        self._row = row
        self.description = None

    def var(self, _type: Any) -> _Var:
        return _Var()

    def execute(self, sql: str, binds: Any = None) -> None:
        self._log.append((sql, binds))

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


class RecordingConnection:
    def __init__(self, row: tuple[Any, ...] | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.row = row

    def cursor(self) -> RecordingCursor:
        return RecordingCursor(self.calls, self.row)


__all__ = [
    "Decimal",
    "DatabaseError",
    "FakeDatabase",
    "RecordingConnection",
]


# --- storage fault injection (SIMULATED providers only) ----------------------------------------
class SpyStorage:
    """Wraps a real SimulatedStorage; records calls and can be told to fail."""

    def __init__(self, inner, registry):
        self._inner, self._registry = inner, registry

    def put(self, object_key: str, data: bytes) -> None:
        self._registry.calls.append(("put", object_key))
        if self._registry.put_fails:
            from app.storage.base import StorageError

            raise StorageError("injected put failure")
        self._inner.put(object_key, data)

    def get(self, object_key: str) -> bytes:
        self._registry.calls.append(("get", object_key))
        if self._registry.get_fails:
            from app.storage.base import StorageError

            raise StorageError("injected get failure")
        return self._inner.get(object_key)

    def delete(self, object_key: str) -> bool:
        self._registry.calls.append(("delete", object_key))
        if self._registry.delete_fails:
            from app.storage.base import StorageError

            raise StorageError("injected delete failure")
        return self._inner.delete(object_key)

    def exists(self, object_key: str) -> bool:
        return self._inner.exists(object_key)


class FaultyRegistry:
    """A StorageRegistry around the real simulated one, with switches for forced failures."""

    def __init__(self, real):
        self.real = real
        self.calls: list[tuple[str, str]] = []
        self.put_fails = False
        self.get_fails = False
        self.delete_fails = False

    def for_region(self, provider_code: str, region_code: str):
        return SpyStorage(self.real.for_region(provider_code, region_code), self)
