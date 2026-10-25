"""Test endpoints that exist only to demonstrate and verify RBAC.

Chunks 3 and 4 add the real provider and file routes using the same `require_permission`
dependency; these three stay as a permanent, cheap regression check of the permission table.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth.deps import require_permission
from app.auth.rbac import Permission
from app.db.base import UserRecord

router = APIRouter(prefix="/protected", tags=["rbac-demo"])


@router.get("/files")
def own_files_area(user: UserRecord = Depends(require_permission(Permission.FILE_WRITE_OWN))):
    """USER only: stands in for 'manage my own files'."""
    return {"area": "own-files", "user_id": user.user_id, "role": user.role}


@router.get("/providers")
def provider_admin_area(user: UserRecord = Depends(require_permission(Permission.PROVIDER_MANAGE))):
    """ADMIN only: stands in for 'manage providers'."""
    return {"area": "provider-admin", "user_id": user.user_id, "role": user.role}


@router.get("/audit")
def audit_area(user: UserRecord = Depends(require_permission(Permission.AUDIT_READ))):
    """ADMIN and AUDITOR: stands in for 'read the audit log' (read-only)."""
    return {"area": "audit-read", "user_id": user.user_id, "role": user.role}
