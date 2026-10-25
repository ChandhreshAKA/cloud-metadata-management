"""Module 3, the provider registry: register, configure, enable/disable, availability, pricing.

Reads are open to every role with provider:read. Writes need provider:manage (ADMIN only); that
is enforced by the router dependency, and the acting admin's id is passed in here for the audit
trail. Each write and its audit event commit in ONE transaction, so there is never a change
without a record or a record without a change.

Capabilities in this schema: max_object_bytes (largest object), the storage tiers a region is
priced for, health_status (availability), the reliability and security scores. There is no
separate capability-flag table.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from decimal import Decimal
from typing import Any

from app.audit import service as audit
from app.db.base import Database, Session
from app.providers.models import PricingRow, ProviderRow, RegionRow


class NotFoundError(Exception):
    """The referenced provider or region does not exist."""


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    return value


def _diff(old: Any, new: Any, fields: tuple[str, ...]) -> dict[str, list[Any]]:
    before, after = asdict(old), asdict(new)
    return {
        f: [_json_value(before[f]), _json_value(after[f])] for f in fields if before[f] != after[f]
    }


def _record(
    session: Session,
    actor_id: int,
    ip: str | None,
    event_type: str,
    target_type: str,
    target_id: int,
    details: dict[str, Any],
) -> None:
    session.audit.record(
        event_type,
        audit.SUCCESS,
        actor_user_id=actor_id,
        target_type=target_type,
        target_id=target_id,
        ip_address=ip,
        details=details,
    )


# --- reads -----------------------------------------------------------------------------------
def list_registry(database: Database) -> list[dict[str, Any]]:
    with database.transaction() as session:
        providers = session.providers.list_providers()
        regions = session.providers.list_regions()
        pricing = session.providers.list_pricing()
    result = []
    for provider in providers:
        entry = asdict(provider)
        entry["regions"] = []
        for region in (r for r in regions if r.provider_id == provider.provider_id):
            region_entry = asdict(region)
            region_entry["pricing"] = [
                asdict(p) for p in pricing if p.region_id == region.region_id
            ]
            entry["regions"].append(region_entry)
        result.append(entry)
    return result


# --- writes ----------------------------------------------------------------------------------
def register_provider(
    database: Database, actor_id: int, ip: str | None, code: str, name: str, max_object_bytes: int
) -> ProviderRow:
    with database.transaction() as session:
        provider_id = session.providers.create_provider(code, name, max_object_bytes)
        _record(session, actor_id, ip, audit.CONFIG_CHANGED, "CONFIG", provider_id,
                {"entity": "provider", "action": "registered", "code": code, "name": name,
                 "max_object_bytes": max_object_bytes})  # fmt: skip
        row = session.providers.get_provider(provider_id)
    assert row is not None
    return row


def configure_provider(
    database: Database, actor_id: int, ip: str | None, provider_id: int, changes: dict[str, Any]
) -> ProviderRow:
    with database.transaction() as session:
        old = session.providers.get_provider(provider_id)
        if old is None:
            raise NotFoundError(f"provider {provider_id} not found")
        new = replace(old, **changes)
        if new == old:
            return old
        session.providers.update_provider(new)
        diff = _diff(old, new, ("provider_name", "max_object_bytes", "is_active"))
        status = {k: v for k, v in diff.items() if k == "is_active"}
        other = {k: v for k, v in diff.items() if k != "is_active"}
        if status:
            _record(session, actor_id, ip, audit.PROVIDER_STATUS_CHANGED, "CONFIG", provider_id,
                    {"entity": "provider", "changes": status})  # fmt: skip
        if other:
            _record(session, actor_id, ip, audit.CONFIG_CHANGED, "CONFIG", provider_id,
                    {"entity": "provider", "changes": other})  # fmt: skip
        return new


def register_region(
    database: Database,
    actor_id: int,
    ip: str | None,
    provider_id: int,
    region_code: str,
    bucket_name: str,
    reliability_score: Decimal,
    security_score: Decimal,
    egress_usd_per_gb: Decimal,
    is_illustrative: bool,
) -> RegionRow:
    with database.transaction() as session:
        if session.providers.get_provider(provider_id) is None:
            raise NotFoundError(f"provider {provider_id} not found")
        region_id = session.providers.create_region(
            provider_id, region_code, bucket_name, reliability_score, security_score,
            egress_usd_per_gb, is_illustrative,
        )  # fmt: skip
        _record(session, actor_id, ip, audit.CONFIG_CHANGED, "PROVIDER_REGION", region_id,
                {"entity": "region", "action": "registered", "provider_id": provider_id,
                 "region_code": region_code})  # fmt: skip
        row = session.providers.get_region(region_id)
    assert row is not None
    return row


def configure_region(
    database: Database, actor_id: int, ip: str | None, region_id: int, changes: dict[str, Any]
) -> RegionRow:
    with database.transaction() as session:
        old = session.providers.get_region(region_id)
        if old is None:
            raise NotFoundError(f"region {region_id} not found")
        new = replace(old, **changes)
        if new == old:
            return old
        session.providers.update_region(new)
        diff = _diff(
            old, new,
            ("bucket_name", "reliability_score", "security_score", "egress_usd_per_gb",
             "health_status", "is_active"),
        )  # fmt: skip
        status = {k: v for k, v in diff.items() if k in ("health_status", "is_active")}
        other = {k: v for k, v in diff.items() if k not in ("health_status", "is_active")}
        if status:
            _record(session, actor_id, ip, audit.PROVIDER_STATUS_CHANGED, "PROVIDER_REGION",
                    region_id, {"entity": "region", "changes": status})  # fmt: skip
        if other:
            _record(session, actor_id, ip, audit.CONFIG_CHANGED, "PROVIDER_REGION", region_id,
                    {"entity": "region", "changes": other})  # fmt: skip
        updated = session.providers.get_region(region_id)
    assert updated is not None
    return updated


def set_pricing(database: Database, actor_id: int, ip: str | None, row: PricingRow) -> PricingRow:
    with database.transaction() as session:
        if session.providers.get_region(row.region_id) is None:
            raise NotFoundError(f"region {row.region_id} not found")
        existing = next(
            (
                p
                for p in session.providers.list_pricing(row.region_id)
                if p.tier_code == row.tier_code
            ),
            None,
        )
        session.providers.upsert_pricing(row)
        details: dict[str, Any] = {"entity": "pricing", "tier": row.tier_code}
        if existing is None:
            details["action"] = "created"
        else:
            details["changes"] = _diff(
                existing, row,
                ("native_class_name", "storage_usd_per_gb_month", "retrieval_usd_per_gb",
                 "put_usd_per_1k", "get_usd_per_1k", "min_storage_days", "is_illustrative"),
            )  # fmt: skip
        _record(
            session, actor_id, ip, audit.CONFIG_CHANGED, "PROVIDER_REGION", row.region_id, details
        )
    return row
