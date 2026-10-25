"""Registry rows (what administrators maintain). Names follow sql/schema.sql (Chunk 1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class ProviderRow:
    provider_id: int
    provider_code: str
    provider_name: str
    max_object_bytes: int  # capability: largest object this provider accepts (size-fit hard filter)
    is_active: bool  # enabled / disabled


@dataclass(frozen=True)
class RegionRow:
    region_id: int
    provider_id: int
    region_code: str
    bucket_name: str
    reliability_score: Decimal  # 0..10
    security_score: Decimal  # 0..10
    egress_usd_per_gb: Decimal
    health_status: str  # availability: UP, DEGRADED, DOWN
    status_changed_at: datetime | None
    is_active: bool
    is_illustrative: bool


@dataclass(frozen=True)
class PricingRow:
    region_id: int
    tier_code: str  # the tiers a region is priced for are its storage capabilities
    native_class_name: str
    storage_usd_per_gb_month: Decimal
    retrieval_usd_per_gb: Decimal
    put_usd_per_1k: Decimal
    get_usd_per_1k: Decimal
    min_storage_days: int
    is_illustrative: bool
