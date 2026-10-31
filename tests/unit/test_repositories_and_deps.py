"""Small logic checks that need neither Oracle nor a full request flow."""

import json

from fastapi import Depends
from fastapi.testclient import TestClient

from app.auth.deps import require_role
from app.auth.rbac import Role
from app.db.base import UserRecord
from app.db.repositories.audit import encode_details
from app.db.repositories.users import SqlAlchemyUserRepository
from app.main import create_app
from tests.conftest import PASSWORD
from tests.orm_helpers import sqlite_session


def test_user_row_is_converted_to_a_record():
    with sqlite_session() as session:
        repo = SqlAlchemyUserRepository(session)
        user_id = repo.create("Ada", "ada@example.com", "hash")
        user = repo.get_by_email("ada@example.com")
    assert user == UserRecord(user_id, "Ada", "ada@example.com", "USER", "hash", "ACTIVE")


def test_no_row_means_no_user():
    with sqlite_session() as session:
        assert SqlAlchemyUserRepository(session).get_by_id(1) is None


def test_audit_details_encoding():
    assert encode_details(None) is None
    assert encode_details({}) is None
    assert json.loads(encode_details({"a": 1})) == {"a": 1}
    too_long = encode_details({"x": "y" * 3000})
    assert json.loads(too_long) == {"truncated": True}  # still valid JSON for the IS JSON check


def test_require_role_allows_listed_roles_only_and_audits_denial(db, settings, make_user):
    app = create_app(database=db, settings=settings)

    @app.get("/only-admin-or-auditor")
    def route(user: UserRecord = Depends(require_role(Role.ADMIN, Role.AUDITOR))):
        return {"role": user.role}

    with TestClient(app) as client:
        results = {}
        for role in ("USER", "ADMIN", "AUDITOR"):
            _, email = make_user(role=role)
            token = client.post("/auth/login", json={"email": email, "password": PASSWORD})
            headers = {"Authorization": f"Bearer {token.json()['access_token']}"}
            results[role] = client.get("/only-admin-or-auditor", headers=headers).status_code
    assert results == {"USER": 403, "ADMIN": 200, "AUDITOR": 200}
    [event] = db.audit_of("ACCESS_DENIED")
    assert event["details"]["required"] == "role:ADMIN|AUDITOR"
