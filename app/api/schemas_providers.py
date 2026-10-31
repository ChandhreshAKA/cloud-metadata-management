"""Request and response models for the provider registry and the selection endpoints."""
 
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Tier = Literal["HOT", "COOL", "COLD", "ARCHIVE"]
Level = Literal["LOW", "MEDIUM", "HIGH"]
Health = Literal["UP", "DEGRADED", "DOWN"]

_MAX_PRICE = Decimal(1000)


def _score(default=...):
    return Field(default, ge=0, le=10, max_digits=4, decimal_places=2)  # NUMBER(4,2), 0..10


def _price(default=...):
    return Field(default, ge=0, le=_MAX_PRICE, max_digits=10, decimal_places=6)  # NUMBER(10,6)


class _PartialUpdate(BaseModel):
    """Optional fields; an explicit null means 'not provided'. At least one field is required."""

    @model_validator(mode="after")
    def _something_to_change(self):
        if not self.changes():
            raise ValueError("send at least one field to change")
        return self

    def changes(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class ProviderCreate(BaseModel):
    provider_code: str = Field(pattern=r"^[A-Z0-9_]{2,20}$")
    provider_name: str = Field(min_length=1, max_length=50)
    max_object_bytes: int = Field(gt=0, le=10**19 - 1)


class ProviderUpdate(_PartialUpdate):
    provider_name: str | None = Field(default=None, min_length=1, max_length=50)
    max_object_bytes: int | None = Field(default=None, gt=0, le=10**19 - 1)
    is_active: bool | None = None


class RegionCreate(BaseModel):
    region_code: str = Field(min_length=1, max_length=50)
    bucket_name: str = Field(min_length=1, max_length=150)
    reliability_score: Decimal = _score()
    security_score: Decimal = _score()
    egress_usd_per_gb: Decimal = _price()
    is_illustrative: bool = True


class RegionUpdate(_PartialUpdate):
    bucket_name: str | None = Field(default=None, min_length=1, max_length=150)
    reliability_score: Decimal | None = _score(None)
    security_score: Decimal | None = _score(None)
    egress_usd_per_gb: Decimal | None = _price(None)
    health_status: Health | None = None
    is_active: bool | None = None


class PricingUpsert(BaseModel):
    native_class_name: str = Field(min_length=1, max_length=40)
    storage_usd_per_gb_month: Decimal = _price()
    retrieval_usd_per_gb: Decimal = _price()
    put_usd_per_1k: Decimal = _price()
    get_usd_per_1k: Decimal = _price()
    min_storage_days: int = Field(default=0, ge=0, le=9999)
    is_illustrative: bool = True


class SelectionBody(BaseModel):
    """What the caller knows about the file (used by /selection/select and /selection/compare)."""

    file_size_bytes: int = Field(ge=0, le=10**15 * 1024)
    security_level: Level
    expected_accesses_per_month: Decimal = Field(default=Decimal(1), ge=0, le=1_000_000)
    retention_months: Decimal = Field(default=Decimal(12), gt=0, le=1200)
    tier: Tier = "HOT"
    preset: str | None = Field(
        default=None, description="BALANCED, COST_SAVER, PERFORMANCE, SECURE"
    )
    weights: dict[str, Decimal] | None = Field(
        default=None,
        description="Per-request override of any of: "
        + ", ".join(["storage_cost", "transfer_cost", "latency", "reliability", "security"])
        + ". If the final weights do not sum to 1 they are normalised and the response says so.",
    )
    fixed_provider_code: str | None = Field(
        default=None, description="Also report what a fixed-provider baseline (e.g. AWS) would do"
    )
