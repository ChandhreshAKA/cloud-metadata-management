"""INTEGRATION TESTS (chunk 2): auth, RBAC and audit against a real Oracle database.

See tests/integration/conftest.py for how to run them. Only these tests (not tests/unit) show
that the SQL and the constraints work on Oracle.
"""

from __future__ import annotations

import pytest

from app.db.errors import CheckViolationError, ValueTooLargeError
from tests.integration.helpers import PASSWORD, register_and_login, scalar, unique_email

pytestmark = pytest.mark.integration


def test_health_reports_database_up(client):
    assert client.get("/health").json() == {"status": "ok", "database": "up"}


def test_register_login_me_round_trip_and_audit_rows(client, database):
    email, headers = register_and_login(client)
    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 200 and me.json()["email"] == email and me.json()["role"] == "USER"
    user_id = me.json()["user_id"]
    types = scalar(
        database,
        "SELECT LISTAGG(event_type, ',') WITHIN GROUP (ORDER BY event_id) "
        "FROM audit_events WHERE actor_user_id = :id",
        {"id": user_id},
    )
    assert types == "USER_REGISTERED,LOGIN_OK"
    assert (
        scalar(database, "SELECT last_login_at FROM users WHERE user_id = :id", {"id": user_id})
        is not None
    )


def test_password_is_stored_as_an_argon2_hash(client, database):
    email, _ = register_and_login(client)
    stored = scalar(database, "SELECT password_hash FROM users WHERE email = :e", {"e": email})
    assert stored.startswith("$argon2id$") and PASSWORD not in stored


def test_duplicate_email_is_a_clean_409_and_creates_no_second_row(client, database):
    email, _ = register_and_login(client)
    again = client.post(
        "/auth/register", json={"full_name": "Dup", "email": email.upper(), "password": PASSWORD}
    )
    assert again.status_code == 409
    assert again.json() == {"detail": "That e-mail address is already registered"}
    assert "ORA-" not in again.text
    assert scalar(database, "SELECT COUNT(*) FROM users WHERE email = :e", {"e": email}) == 1


def test_failed_login_and_denial_are_audited_in_oracle(client, database):
    email, headers = register_and_login(client)
    bad = client.post("/auth/login", json={"email": email, "password": "wrong-password-1"})
    assert bad.status_code == 401
    assert client.get("/protected/providers", headers=headers).status_code == 403
    user_id = client.get("/auth/me", headers=headers).json()["user_id"]
    events = scalar(
        database,
        "SELECT LISTAGG(event_type || ':' || outcome, ',') WITHIN GROUP (ORDER BY event_id) "
        "FROM audit_events WHERE actor_user_id = :id "
        "AND event_type IN ('LOGIN_FAILED','ACCESS_DENIED')",
        {"id": user_id},
    )
    assert events == "LOGIN_FAILED:FAILURE,ACCESS_DENIED:DENIED"


@pytest.mark.parametrize(
    ("role", "files", "providers", "audit"),
    [("USER", 200, 403, 403), ("ADMIN", 403, 200, 200), ("AUDITOR", 403, 403, 200)],
)
def test_role_enforcement_against_real_roles_in_the_database(
    client, database, role, files, providers, audit
):
    _, headers = register_and_login(
        client, role=None if role == "USER" else role, database=database
    )
    assert client.get("/protected/files", headers=headers).status_code == files
    assert client.get("/protected/providers", headers=headers).status_code == providers
    assert client.get("/protected/audit", headers=headers).status_code == audit


INJECTION = ["' OR '1'='1' --", "x'; DROP TABLE users; --", "admin@example.com' --"]


@pytest.mark.parametrize("payload", INJECTION)
def test_sql_injection_strings_do_nothing_against_oracle(client, database, payload):
    users_before = scalar(database, "SELECT COUNT(*) FROM users")
    as_email = client.post("/auth/login", json={"email": payload, "password": PASSWORD})
    as_password = client.post(
        "/auth/login", json={"email": "harini@example.com", "password": payload}
    )
    register = client.post(
        "/auth/register",
        json={"full_name": payload, "email": unique_email(), "password": PASSWORD},
    )
    assert as_email.status_code == 422
    assert as_password.status_code == 401
    assert register.status_code == 201  # the name is stored as plain text
    assert scalar(database, "SELECT COUNT(*) FROM users") == users_before + 1
    assert scalar(database, "SELECT COUNT(*) FROM user_tables WHERE table_name = 'USERS'") == 1
    stored = scalar(
        database,
        "SELECT full_name FROM users WHERE user_id = :i",
        {"i": register.json()["user_id"]},
    )
    assert stored == payload


def test_oracle_constraints_surface_as_domain_errors_not_raw_driver_errors(database):
    with pytest.raises(CheckViolationError) as lower, database.transaction() as session:
        session.users.create("Upper", "UPPER@EXAMPLE.TEST", "hash")
    assert lower.value.constraint == "CK_USERS_EMAIL_LOWER"
    with pytest.raises(ValueTooLargeError), database.transaction() as session:
        session.users.create("n" * 101, unique_email(), "hash")


def test_transaction_helper_rolls_back_on_error_and_commits_on_success(database):
    email = unique_email()
    with pytest.raises(RuntimeError), database.transaction() as session:
        session.users.create("Temp", email, "hash")
        raise RuntimeError("boom")
    assert scalar(database, "SELECT COUNT(*) FROM users WHERE email = :e", {"e": email}) == 0
    with database.transaction() as session:
        session.users.create("Kept", email, "hash")
    assert scalar(database, "SELECT COUNT(*) FROM users WHERE email = :e", {"e": email}) == 1
