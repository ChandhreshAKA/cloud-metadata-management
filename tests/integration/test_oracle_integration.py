"""INTEGRATION TESTS: these talk to a real Oracle database. You run them yourself.

    $env:RUN_INTEGRATION = "1"     # the ORACLE_* and JWT_SECRET variables must also be set
    .venv\\Scripts\\python.exe -m pytest tests/integration -v

They are skipped unless RUN_INTEGRATION=1, so the normal `pytest` run never needs Oracle.
Only these tests (not tests/unit) show that the SQL and the constraints work on Oracle.

Side effects: every test user is named itest_<random>@example.test. audit_events is append-only,
so test users that have audit rows cannot be deleted; they are set to DISABLED at the end of the
run. Remove them with sql/cleanup_integration_tests.sql.
"""

from __future__ import annotations

import os
import uuid

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from app.config import load_settings
from app.db.errors import CheckViolationError, ValueTooLargeError
from app.db.pool import OracleDatabase
from app.main import create_app

pytestmark = pytest.mark.integration

load_dotenv()
if os.environ.get("RUN_INTEGRATION") != "1":
    pytest.skip("set RUN_INTEGRATION=1 to run tests against Oracle", allow_module_level=True)

PASSWORD = "integration-test-pw-1"  # noqa: S105  (test value)


@pytest.fixture(scope="module")
def database():
    db = OracleDatabase.from_settings(load_settings())
    yield db
    with db.transaction() as session:
        session.connection.cursor().execute(
            "UPDATE users SET account_status = 'DISABLED' WHERE email LIKE 'itest\\_%' ESCAPE '\\'"
        )
    db.close()


@pytest.fixture(scope="module")
def client(database):
    with TestClient(create_app(database=database, settings=load_settings())) as c:
        yield c


def _unique_email() -> str:
    return f"itest_{uuid.uuid4().hex[:10]}@example.test"


def _scalar(database, sql: str, binds: dict | None = None):
    with database.transaction() as session:
        cursor = session.connection.cursor()
        cursor.execute(sql, binds or {})
        return cursor.fetchone()[0]


def _register_and_login(client, email=None, role=None, database=None):
    email = email or _unique_email()
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


def test_health_reports_database_up(client):
    assert client.get("/health").json() == {"status": "ok", "database": "up"}


def test_register_login_me_round_trip_and_audit_rows(client, database):
    email, headers = _register_and_login(client)
    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 200 and me.json()["email"] == email and me.json()["role"] == "USER"
    user_id = me.json()["user_id"]
    types = _scalar(
        database,
        "SELECT LISTAGG(event_type, ',') WITHIN GROUP (ORDER BY event_id) "
        "FROM audit_events WHERE actor_user_id = :id",
        {"id": user_id},
    )
    assert types == "USER_REGISTERED,LOGIN_OK"
    assert (
        _scalar(database, "SELECT last_login_at FROM users WHERE user_id = :id", {"id": user_id})
        is not None
    )


def test_password_is_stored_as_an_argon2_hash(client, database):
    email, _ = _register_and_login(client)
    stored = _scalar(database, "SELECT password_hash FROM users WHERE email = :e", {"e": email})
    assert stored.startswith("$argon2id$") and PASSWORD not in stored


def test_duplicate_email_is_a_clean_409_and_creates_no_second_row(client, database):
    email, _ = _register_and_login(client)
    again = client.post(
        "/auth/register", json={"full_name": "Dup", "email": email.upper(), "password": PASSWORD}
    )
    assert again.status_code == 409
    assert again.json() == {"detail": "That e-mail address is already registered"}
    assert "ORA-" not in again.text
    assert _scalar(database, "SELECT COUNT(*) FROM users WHERE email = :e", {"e": email}) == 1


def test_failed_login_and_denial_are_audited_in_oracle(client, database):
    email, headers = _register_and_login(client)
    bad = client.post("/auth/login", json={"email": email, "password": "wrong-password-1"})
    assert bad.status_code == 401
    assert client.get("/protected/providers", headers=headers).status_code == 403
    user_id = client.get("/auth/me", headers=headers).json()["user_id"]
    events = _scalar(
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
    _, headers = _register_and_login(
        client, role=None if role == "USER" else role, database=database
    )
    assert client.get("/protected/files", headers=headers).status_code == files
    assert client.get("/protected/providers", headers=headers).status_code == providers
    assert client.get("/protected/audit", headers=headers).status_code == audit


INJECTION = ["' OR '1'='1' --", "x'; DROP TABLE users; --", "admin@example.com' --"]


@pytest.mark.parametrize("payload", INJECTION)
def test_sql_injection_strings_do_nothing_against_oracle(client, database, payload):
    users_before = _scalar(database, "SELECT COUNT(*) FROM users")
    as_email = client.post("/auth/login", json={"email": payload, "password": PASSWORD})
    as_password = client.post(
        "/auth/login", json={"email": "harini@example.com", "password": payload}
    )
    register = client.post(
        "/auth/register",
        json={"full_name": payload, "email": _unique_email(), "password": PASSWORD},
    )
    assert as_email.status_code == 422
    assert as_password.status_code == 401
    assert register.status_code == 201  # the name is stored as plain text
    assert _scalar(database, "SELECT COUNT(*) FROM users") == users_before + 1
    assert _scalar(database, "SELECT COUNT(*) FROM user_tables WHERE table_name = 'USERS'") == 1
    stored = _scalar(
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
        session.users.create("n" * 101, _unique_email(), "hash")


def test_transaction_helper_rolls_back_on_error_and_commits_on_success(database):
    email = _unique_email()
    with pytest.raises(RuntimeError), database.transaction() as session:
        session.users.create("Temp", email, "hash")
        raise RuntimeError("boom")
    assert _scalar(database, "SELECT COUNT(*) FROM users WHERE email = :e", {"e": email}) == 0
    with database.transaction() as session:
        session.users.create("Kept", email, "hash")
    assert _scalar(database, "SELECT COUNT(*) FROM users WHERE email = :e", {"e": email}) == 1
