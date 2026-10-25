"""Interfaces between the application logic and the database.

The auth and audit code depends only on these Protocols. `app.db.pool.OracleDatabase`
implements them with Oracle; tests/fakes.py implements them in memory. A test that uses the
fake proves application logic, NOT that the SQL is valid on Oracle (the integration tests do that).
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from app.providers.models import PricingRow, ProviderRow, RegionRow
from app.selection.models import Candidate, Policy, Weights


@dataclass(frozen=True)
class UserRecord:
    user_id: int
    full_name: str
    email: str
    role: str
    password_hash: str | None
    account_status: str


class UserRepository(Protocol):
    def create(self, full_name: str, email: str, password_hash: str) -> int:
        """Insert an ACTIVE user with role USER and return the new user_id."""
        ...

    def get_by_email(self, email: str) -> UserRecord | None: ...

    def get_by_id(self, user_id: int) -> UserRecord | None: ...

    def touch_last_login(self, user_id: int) -> None: ...


class AuditRepository(Protocol):
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
    ) -> None: ...


class ProviderRepository(Protocol):
    """Provider registry (module 3) plus the read model the selection engine needs."""

    def list_providers(self) -> list[ProviderRow]: ...

    def get_provider(self, provider_id: int) -> ProviderRow | None: ...

    def create_provider(self, code: str, name: str, max_object_bytes: int) -> int: ...

    def update_provider(self, row: ProviderRow) -> None:
        """Write name, max_object_bytes and is_active of `row` (read-modify-write by the caller)."""
        ...

    def list_regions(self, provider_id: int | None = None) -> list[RegionRow]: ...

    def get_region(self, region_id: int) -> RegionRow | None: ...

    def create_region(
        self,
        provider_id: int,
        region_code: str,
        bucket_name: str,
        reliability_score: Decimal,
        security_score: Decimal,
        egress_usd_per_gb: Decimal,
        is_illustrative: bool,
    ) -> int: ...

    def update_region(self, row: RegionRow) -> None:
        """Write bucket, scores, egress, health_status and is_active of `row`."""
        ...

    def list_pricing(self, region_id: int | None = None) -> list[PricingRow]: ...

    def upsert_pricing(self, row: PricingRow) -> None: ...

    # --- read model for the selection engine -------------------------------------------------
    def candidates(self, tier: str) -> list[Candidate]:
        """Every provider-region as a Candidate, priced for `tier` (pricing None if unpriced)."""
        ...

    def weight_profiles(self) -> tuple[dict[str, Weights], str | None]:
        """(profiles by name, name of the default profile or None)."""
        ...

    def policy(self) -> Policy:
        """Hard-constraint minimums from system_config, with documented defaults."""
        ...


class Session(Protocol):
    users: UserRepository
    audit: AuditRepository
    providers: ProviderRepository


class Database(Protocol):
    def transaction(self) -> AbstractContextManager[Session]:
        """Commit when the block ends normally; roll back when it raises."""
        ...

    def ping(self) -> None:
        """Raise DatabaseUnavailableError if the database cannot answer a trivial query."""
        ...

    def close(self) -> None: ...
