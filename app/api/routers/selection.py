"""Provider selection endpoints. Both are DRY RUNS: nothing is uploaded or written.

Upload (chunk 4) will call the same engine; these endpoints let anyone with provider:read see
what it would choose, and why, before committing a file.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.schemas_providers import SelectionBody
from app.auth.deps import get_database, require_permission
from app.auth.rbac import Permission
from app.db.base import Database, UserRecord
from app.selection import service

router = APIRouter(prefix="/selection", tags=["selection (dry run)"])


def _request(body: SelectionBody):
    return service.request_from_body(
        body.file_size_bytes, body.security_level, body.expected_accesses_per_month,
        body.retention_months, body.tier,
    )  # fmt: skip


@router.post("/compare")
def compare(
    body: SelectionBody,
    _: UserRecord = Depends(require_permission(Permission.PROVIDER_READ)),
    database: Database = Depends(get_database),
) -> dict[str, Any]:
    """Every provider-region side by side (cost breakdown, eligibility and rejection reasons)
    and the winner under each weight preset. `preset` and `weights` in the body are ignored here."""
    return service.compare_providers(
        database, _request(body), fixed_provider_code=body.fixed_provider_code
    )


@router.post("/select")
def select(
    body: SelectionBody,
    _: UserRecord = Depends(require_permission(Permission.PROVIDER_READ)),
    database: Database = Depends(get_database),
) -> dict[str, Any]:
    """Rank the providers for this file and explain the choice, plus the baselines."""
    return service.select_provider(
        database,
        _request(body),
        preset=body.preset,
        overrides=body.weights,
        fixed_provider_code=body.fixed_provider_code,
    )
