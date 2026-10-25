"""The permission table itself (no HTTP)."""

import pytest

from app.auth.rbac import (
    MODIFYING_PERMISSIONS,
    ROLE_PERMISSIONS,
    Permission,
    Role,
    has_permission,
)

# The written table, restated independently of the code under test.
EXPECTED = {
    "USER": {"provider:read", "file:read_own", "file:write_own", "file:delete_own"},
    "ADMIN": {
        "provider:read",
        "provider:manage",
        "file:read_any_metadata",
        "user:manage",
        "config:read",
        "config:manage",
        "audit:read",
        "report:read",
    },
    "AUDITOR": {
        "provider:read",
        "file:read_any_metadata",
        "config:read",
        "audit:read",
        "report:read",
    },
}


@pytest.mark.parametrize("role", list(Role))
def test_each_role_has_exactly_the_documented_permissions(role):
    assert {p.value for p in ROLE_PERMISSIONS[role]} == EXPECTED[role.value]


def test_auditor_holds_no_modifying_permission():
    assert not (ROLE_PERMISSIONS[Role.AUDITOR] & MODIFYING_PERMISSIONS)


def test_only_admin_manages_providers_users_and_config():
    for permission in (
        Permission.PROVIDER_MANAGE,
        Permission.USER_MANAGE,
        Permission.CONFIG_MANAGE,
    ):
        holders = {r for r in Role if permission in ROLE_PERMISSIONS[r]}
        assert holders == {Role.ADMIN}


def test_only_user_manages_own_files():
    for permission in (Permission.FILE_WRITE_OWN, Permission.FILE_DELETE_OWN):
        assert {r for r in Role if permission in ROLE_PERMISSIONS[r]} == {Role.USER}


def test_every_permission_is_granted_to_somebody():
    granted = set().union(*ROLE_PERMISSIONS.values())
    assert granted == set(Permission)


@pytest.mark.parametrize("bad_role", ["", "user", "ROOT", "ADMIN ", None])
def test_unknown_roles_are_denied_everything(bad_role):
    assert not any(has_permission(bad_role, p) for p in Permission)
