"""Provider registry endpoints. Reads: provider:read (all roles); writes: provider:manage."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Request, status

from app.api.schemas_providers import (
    PricingUpsert,
    ProviderCreate,
    ProviderUpdate,
    RegionCreate,
    RegionUpdate,
    Tier,
)
from app.auth.deps import client_ip, get_database, require_permission
from app.auth.rbac import Permission
from app.db.base import Database, UserRecord
from app.providers import service
from app.providers.models import PricingRow

router = APIRouter(tags=["providers"])

_read = require_permission(Permission.PROVIDER_READ)
_manage = require_permission(Permission.PROVIDER_MANAGE)


@router.get("/providers")
def list_providers(
    _: UserRecord = Depends(_read), database: Database = Depends(get_database)
) -> list[dict[str, Any]]:
    """All providers with their regions, scores, availability and per-tier pricing."""
    return service.list_registry(database)


@router.post("/providers", status_code=status.HTTP_201_CREATED)
def register_provider(
    body: ProviderCreate,
    request: Request,
    admin: UserRecord = Depends(_manage),
    database: Database = Depends(get_database),
) -> dict[str, Any]:
    row = service.register_provider(
        database, admin.user_id, client_ip(request), body.provider_code, body.provider_name,
        body.max_object_bytes,
    )  # fmt: skip
    return asdict(row)


@router.patch("/providers/{provider_id}")
def configure_provider(
    provider_id: int,
    body: ProviderUpdate,
    request: Request,
    admin: UserRecord = Depends(_manage),
    database: Database = Depends(get_database),
) -> dict[str, Any]:
    """Rename, change the size limit, or enable/disable (is_active) a provider."""
    return asdict(
        service.configure_provider(
            database, admin.user_id, client_ip(request), provider_id, body.changes()
        )
    )


@router.post("/providers/{provider_id}/regions", status_code=status.HTTP_201_CREATED)
def register_region(
    provider_id: int,
    body: RegionCreate,
    request: Request,
    admin: UserRecord = Depends(_manage),
    database: Database = Depends(get_database),
) -> dict[str, Any]:
    row = service.register_region(
        database, admin.user_id, client_ip(request), provider_id, body.region_code,
        body.bucket_name, body.reliability_score, body.security_score, body.egress_usd_per_gb,
        body.is_illustrative,
    )  # fmt: skip
    return asdict(row)


@router.patch("/regions/{region_id}")
def configure_region(
    region_id: int,
    body: RegionUpdate,
    request: Request,
    admin: UserRecord = Depends(_manage),
    database: Database = Depends(get_database),
) -> dict[str, Any]:
    """Change scores, egress price, bucket, availability (health_status) or enable/disable."""
    return asdict(
        service.configure_region(
            database, admin.user_id, client_ip(request), region_id, body.changes()
        )
    )


@router.put("/regions/{region_id}/pricing/{tier_code}")
def set_pricing(
    region_id: int,
    tier_code: Tier,
    body: PricingUpsert,
    request: Request,
    admin: UserRecord = Depends(_manage),
    database: Database = Depends(get_database),
) -> dict[str, Any]:
    """Create or replace the price row for one storage tier of a region."""
    row = PricingRow(region_id=region_id, tier_code=tier_code, **body.model_dump())
    return asdict(service.set_pricing(database, admin.user_id, client_ip(request), row))
