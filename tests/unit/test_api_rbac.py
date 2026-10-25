"""Role enforcement through the three /protected endpoints (FakeDatabase)."""

import pytest

from app.auth.tokens import create_access_token

ENDPOINTS = {
    "/protected/files": {"USER": 200, "ADMIN": 403, "AUDITOR": 403},
    "/protected/providers": {"USER": 403, "ADMIN": 200, "AUDITOR": 403},
    "/protected/audit": {"USER": 403, "ADMIN": 200, "AUDITOR": 200},
}
CASES = [
    (path, role, code) for path, by_role in ENDPOINTS.items() for role, code in by_role.items()
]


@pytest.mark.parametrize(("path", "role", "expected"), CASES)
def test_role_matrix(client, make_user, login, path, role, expected):
    _, email = make_user(role=role)
    assert client.get(path, headers=login(email)).status_code == expected


@pytest.mark.parametrize("path", list(ENDPOINTS))
def test_every_protected_endpoint_requires_a_token(client, path):
    assert client.get(path).status_code == 401


def test_denial_is_audited_with_actor_and_required_permission(client, db, make_user, login):
    user_id, email = make_user(role="USER")
    assert client.get("/protected/providers", headers=login(email)).status_code == 403
    [event] = db.audit_of("ACCESS_DENIED")
    assert event["outcome"] == "DENIED" and event["actor_user_id"] == user_id
    assert event["details"] == {
        "required": "provider:manage",
        "method": "GET",
        "path": "/protected/providers",
    }


def test_allowed_requests_write_no_denial_event(client, db, make_user, login):
    _, email = make_user(role="ADMIN")
    client.get("/protected/providers", headers=login(email))
    assert db.audit_of("ACCESS_DENIED") == []


def test_forged_role_claim_in_a_valid_token_is_ignored(client, make_user, settings):
    """A token whose role claim says ADMIN does not make a USER an admin: the DB role decides."""
    user_id, _ = make_user(role="USER")
    token = create_access_token(settings, user_id, "ADMIN")
    response = client.get("/protected/providers", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_role_change_takes_effect_without_a_new_login(client, db, make_user, login):
    user_id, email = make_user(role="USER")
    headers = login(email)
    assert client.get("/protected/providers", headers=headers).status_code == 403
    db.set_role(user_id, "ADMIN")
    assert client.get("/protected/providers", headers=headers).status_code == 200


def test_auditor_cannot_reach_any_modifying_area_but_can_read_audit(client, make_user, login):
    _, email = make_user(role="AUDITOR")
    headers = login(email)
    assert client.get("/protected/audit", headers=headers).status_code == 200
    assert client.get("/protected/files", headers=headers).status_code == 403
    assert client.get("/protected/providers", headers=headers).status_code == 403


def test_a_user_with_an_unknown_role_is_denied_everywhere(client, db, make_user, login):
    user_id, email = make_user(role="USER")
    headers = login(email)
    db.set_role(user_id, "SUPERUSER")
    for path in ENDPOINTS:
        assert client.get(path, headers=headers).status_code == 403
