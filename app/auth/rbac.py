"""Role-based access control: the written permission table.

This table is the single source of truth. Endpoints ask for a *permission*
(`require_permission(Permission.X)`), never for a hard-coded role name, so changing who may do
what means editing this table and its test, not hunting through routes.

Permission table (Y = allowed)
                              USER  ADMIN  AUDITOR
  PROVIDER_READ                Y      Y      Y
  PROVIDER_MANAGE                     Y
  FILE_READ_OWN                Y
  FILE_WRITE_OWN               Y
  FILE_DELETE_OWN              Y
  FILE_READ_ANY_METADATA              Y      Y        (metadata only; never file contents)
  USER_MANAGE                         Y
  CONFIG_READ                         Y      Y
  CONFIG_MANAGE                       Y
  AUDIT_READ                          Y      Y
  REPORT_READ                         Y      Y

Decisions to note:
  * AUDITOR holds only *_READ permissions: it can see everything it audits and change nothing.
  * ADMIN manages providers, users and configuration but has no own-file permissions in this
    chunk. If admins should upload files, add the FILE_*_OWN permissions to ADMIN here.
  * "Own" permissions only say the role may act on files it owns; the ownership check itself
    happens where the file is loaded (chunk 4).
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    USER = "USER"
    ADMIN = "ADMIN"
    AUDITOR = "AUDITOR"


class Permission(StrEnum):
    PROVIDER_READ = "provider:read"
    PROVIDER_MANAGE = "provider:manage"
    FILE_READ_OWN = "file:read_own"
    FILE_WRITE_OWN = "file:write_own"
    FILE_DELETE_OWN = "file:delete_own"
    FILE_READ_ANY_METADATA = "file:read_any_metadata"
    USER_MANAGE = "user:manage"
    CONFIG_READ = "config:read"
    CONFIG_MANAGE = "config:manage"
    AUDIT_READ = "audit:read"
    REPORT_READ = "report:read"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.USER: frozenset(
        {
            Permission.PROVIDER_READ,
            Permission.FILE_READ_OWN,
            Permission.FILE_WRITE_OWN,
            Permission.FILE_DELETE_OWN,
        }
    ),
    Role.ADMIN: frozenset(
        {
            Permission.PROVIDER_READ,
            Permission.PROVIDER_MANAGE,
            Permission.FILE_READ_ANY_METADATA,
            Permission.USER_MANAGE,
            Permission.CONFIG_READ,
            Permission.CONFIG_MANAGE,
            Permission.AUDIT_READ,
            Permission.REPORT_READ,
        }
    ),
    Role.AUDITOR: frozenset(
        {
            Permission.PROVIDER_READ,
            Permission.FILE_READ_ANY_METADATA,
            Permission.CONFIG_READ,
            Permission.AUDIT_READ,
            Permission.REPORT_READ,
        }
    ),
}

# Permissions that change state. AUDITOR must never hold any of these (tested).
MODIFYING_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.PROVIDER_MANAGE,
        Permission.FILE_WRITE_OWN,
        Permission.FILE_DELETE_OWN,
        Permission.USER_MANAGE,
        Permission.CONFIG_MANAGE,
    }
)


def has_permission(role: str, permission: Permission) -> bool:
    """False for any unknown role string (deny by default)."""
    try:
        return permission in ROLE_PERMISSIONS[Role(role)]
    except (ValueError, KeyError):
        return False
