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


class FakeSession:
    def __init__(self, state: dict[str, Any], database: FakeDatabase):
        self.users = _FakeUsers(state)
        self.audit = _FakeAudit(state, database)
        self.providers = _FakeProviders(state)


class FakeDatabase:
    """In-memory database with real commit/rollback behaviour (snapshot and restore)."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {
            "users": {}, "audit": [], "next_id": 0, "last_login": {},
            "providers": {}, "regions": {}, "pricing": {}, "latencies": {},
            "next_provider_id": 0, "next_region_id": 0,
            "profiles": dict(PRESETS), "default_profile": "BALANCED", "policy": DEFAULT_POLICY,
        }  # fmt: skip
        self.available = True
        self.fail_audit_with: Exception | None = None
        self.fail_create_with: Exception | None = None

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
