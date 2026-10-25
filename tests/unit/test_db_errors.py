"""Mapping of Oracle errors to domain errors, and how the API reports them.

The message strings below were copied from real Oracle 26ai Free output captured by
sql/demo_constraint_failures.sql (see sql/demo_constraint_failures.log), so the mapping is tested
against real wording. The API tests use FakeDatabase to inject the already-mapped errors; they
show the HTTP behaviour, not Oracle's.
"""

import pytest

from app.db.errors import (
    CheckViolationError,
    DatabaseError,
    DatabaseUnavailableError,
    NotNullViolationError,
    ReferenceViolationError,
    UniqueViolationError,
    ValueTooLargeError,
    map_db_error,
    ora_code,
)

REAL_MESSAGES = [
    (
        "ORA-00001: unique constraint (CLOUDMETA.UQ_USERS_EMAIL) violated on table "
        "CLOUDMETA.USERS columns (EMAIL)\nORA-03301: (ORA-00001 details) row with column values "
        "(EMAIL:'harini@example.com') already exists",
        UniqueViolationError,
        "UQ_USERS_EMAIL",
    ),
    (
        "ORA-00001: unique constraint (CLOUDMETA.PK_USERS) violated on table CLOUDMETA.USERS "
        "columns (USER_ID)",
        UniqueViolationError,
        "PK_USERS",
    ),
    (
        "ORA-02290: check constraint (CLOUDMETA.CK_USERS_ROLE) violated",
        CheckViolationError,
        "CK_USERS_ROLE",
    ),
    (
        'ORA-01400: cannot insert NULL into ("CLOUDMETA"."USERS"."EMAIL")',
        NotNullViolationError,
        "EMAIL",
    ),
    (
        "ORA-02291: integrity constraint (CLOUDMETA.FK_FILES_OWNER) violated - "
        "parent key not found",
        ReferenceViolationError,
        "FK_FILES_OWNER",
    ),
    (
        "ORA-02292: integrity constraint (CLOUDMETA.FK_LOGS_FILE) violated - child record found",
        ReferenceViolationError,
        "FK_LOGS_FILE",
    ),
    (
        'ORA-12899: value too large for column "CLOUDMETA"."FILES"."FILE_NAME" '
        "(actual: 256, maximum: 255)",
        ValueTooLargeError,
        "FILE_NAME",
    ),
]


@pytest.mark.parametrize(("message", "expected_type", "detail"), REAL_MESSAGES)
def test_real_oracle_messages_map_to_the_right_domain_error(message, expected_type, detail):
    mapped = map_db_error(Exception(message))
    assert type(mapped) is expected_type
    assert mapped.constraint == detail


@pytest.mark.parametrize(
    "message",
    [
        "ORA-12541: Cannot connect. No listener at host localhost port 1521.",
        "DPY-6005: cannot connect to database (CONNECTION_ID=abc)",
        "ORA-01017: invalid credential or not authorized; logon denied",
    ],
)
def test_connection_failures_map_to_unavailable(message):
    assert isinstance(map_db_error(Exception(message)), DatabaseUnavailableError)


def test_unknown_errors_become_a_generic_database_error():
    mapped = map_db_error(Exception("ORA-00942: table or view does not exist"))
    assert type(mapped) is DatabaseError
    assert ora_code(Exception("no code here")) is None


# ---- how the API reports them ----------------------------------------------------------------
REGISTER = {"full_name": "Ada", "email": "ada@example.com", "password": "a-fine-password-1"}


@pytest.mark.parametrize(
    ("error", "status", "message"),
    [
        (
            CheckViolationError("ORA-02290 ...", "CK_USERS_EMAIL_LOWER"),
            422,
            "E-mail must be lower case",
        ),
        (
            CheckViolationError("ORA-02290 ...", "CK_SOMETHING_ELSE"),
            422,
            "A submitted value is not valid",
        ),
        (NotNullViolationError("ORA-01400 ...", "EMAIL"), 422, "A submitted value is not valid"),
        (ValueTooLargeError("ORA-12899 ...", "FULL_NAME"), 422, "A submitted value is not valid"),
        (UniqueViolationError("ORA-00001 ...", "UQ_UNKNOWN"), 409, "That record already exists"),
        (
            ReferenceViolationError("ORA-02291 ...", "FK_FILES_OWNER"),
            409,
            "Referenced user does not exist",
        ),
        (DatabaseUnavailableError("ORA-12541 ..."), 503, "Database unavailable, try again later"),
        (DatabaseError("ORA-00942 table or view does not exist"), 500, "Internal database error"),
    ],
)
def test_database_errors_become_clean_api_errors(client, db, error, status, message):
    db.fail_create_with = error
    response = client.post("/auth/register", json=REGISTER)
    assert response.status_code == status
    assert response.json() == {"detail": message}
    assert "ORA-" not in response.text and "CLOUDMETA" not in response.text


def test_failed_database_write_leaves_no_partial_data(client, db):
    db.fail_create_with = CheckViolationError("ORA-02290 ...", "CK_USERS_ROLE")
    client.post("/auth/register", json=REGISTER)
    assert db.state["users"] == {}
    assert db.audit_of("USER_REGISTERED") == []


def test_a_failing_audit_write_fails_the_request_instead_of_skipping_the_audit(client, db):
    db.fail_audit_with = DatabaseError("audit table unavailable")
    response = client.post("/auth/register", json=REGISTER)
    assert response.status_code == 500
    assert db.state["users"] == {}  # the whole registration rolled back with the failed audit


def test_health_is_ok_when_the_database_answers(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "up"}


def test_health_is_503_without_details_when_the_database_is_down(client, db):
    db.available = False
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "down"}
