"""Helpers shared by the integration tests. They talk to a real Oracle database."""

from __future__ import annotations

import uuid

PASSWORD = "integration-test-pw-1"  # noqa: S105  (test value)


def unique_email() -> str:
    return f"itest_{uuid.uuid4().hex[:10]}@example.test"


def scalar(database, sql: str, binds: dict | None = None):
    with database.transaction() as session:
        cursor = session.connection.cursor()
        cursor.execute(sql, binds or {})
        return cursor.fetchone()[0]


def register_and_login(client, email=None, role=None, database=None):
    """Register a new USER through the API, optionally promote it in the database, log in."""
    email = email or unique_email()
    response = client.post(
        "/auth/register",
        json={"full_name": "Integration Test", "email": email, "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    if role:
        with database.transaction() as session:
            session.connection.cursor().execute(
                "UPDATE users SET role = :role WHERE email = :email", {"role": role, "email": email}
            )
    login = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return email, {"Authorization": f"Bearer {login.json()['access_token']}"}
