"""SQL-injection defences.

Three independent layers are tested here, none of which needs Oracle:
  1. Input validation rejects obviously malformed input before any database call.
  2. The REAL repository classes are run against a recording connection: user input appears only
     in bind values, never in SQL text.
  3. A static (AST) scan: every execute() call in the repositories passes a module-level constant
     as the SQL text, so no request data can be formatted into a statement.
The live-Oracle proof (injection strings against the real database) is in tests/integration.
"""

import ast
import json
from pathlib import Path

import pytest

from app.db.repositories.audit import OracleAuditRepository
from app.db.repositories.users import OracleUserRepository
from tests.conftest import PASSWORD
from tests.fakes import RecordingConnection

PAYLOADS = [
    "' OR '1'='1' --",
    "x'; DROP TABLE users; --",
    '" OR 1=1 --',
    "admin@example.com' --",
    "1; DELETE FROM audit_events",
    "' UNION SELECT user_id, password_hash FROM users --",
]


# ---- layer 1: validation + behaviour through the API -----------------------------------------
@pytest.mark.parametrize("payload", PAYLOADS)
def test_injection_string_as_email_is_rejected_at_login_and_register(client, db, payload):
    login = client.post("/auth/login", json={"email": payload, "password": PASSWORD})
    register = client.post(
        "/auth/register", json={"full_name": "x", "email": payload, "password": "a-fine-password-1"}
    )
    assert login.status_code == 422 and register.status_code == 422
    assert db.state["users"] == {} and db.audit == []  # rejected before touching the database


@pytest.mark.parametrize("payload", PAYLOADS)
def test_injection_string_as_password_is_just_a_wrong_password(client, db, make_user, payload):
    _, email = make_user()
    response = client.post("/auth/login", json={"email": email, "password": payload})
    assert response.status_code == 401
    assert payload not in json.dumps(db.audit)  # and it is never written to the audit log


def test_injection_that_looks_like_a_valid_email_is_only_data(client, db):
    """No spaces, so it passes the e-mail check, and is then treated as a literal value."""
    sneaky = "x'or'1'='1@example.com"
    assert (
        client.post("/auth/login", json={"email": sneaky, "password": PASSWORD}).status_code == 401
    )
    assert db.audit_of("LOGIN_FAILED")[0]["details"]["reason"] == "unknown_user"


def test_injection_string_in_a_name_is_stored_verbatim_not_executed(client, db):
    name = "Robert'); DROP TABLE users;--"
    response = client.post(
        "/auth/register",
        json={"full_name": name, "email": "bobby@example.com", "password": "a-fine-password-1"},
    )
    assert response.status_code == 201
    assert db.state["users"][response.json()["user_id"]].full_name == name


# ---- layer 2: the real repositories only ever bind values ------------------------------------
@pytest.mark.parametrize("payload", PAYLOADS)
def test_payload_is_only_ever_a_bind_value_never_part_of_the_sql(payload):
    connection = RecordingConnection()
    users, audit = OracleUserRepository(connection), OracleAuditRepository(connection)

    users.get_by_email(payload)
    users.get_by_id(1)
    users.create(payload, payload, payload)
    users.touch_last_login(1)
    audit.record("LOGIN_FAILED", "FAILURE", details={"email": payload}, ip_address=payload)

    assert len(connection.calls) == 5
    for sql, binds in connection.calls:
        assert payload not in sql
        assert isinstance(binds, dict)  # named binds, not string interpolation
        for name in binds:
            assert f":{name}" in sql  # every bind has a placeholder in the statement
    assert connection.calls[0][1] == {"email": payload}


def test_sql_text_is_identical_for_normal_and_hostile_input():
    connection = RecordingConnection()
    users = OracleUserRepository(connection)
    users.get_by_email("ada@example.com")
    users.get_by_email("' OR '1'='1")
    assert connection.calls[0][0] == connection.calls[1][0]


# ---- layer 3: static scan of the repository modules ------------------------------------------
def _module_constants(tree: ast.Module) -> dict[str, ast.expr]:
    return {
        t.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name)
    }


def _is_constant_sql(node: ast.expr, constants: dict[str, ast.expr]) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.JoinedStr):  # f-string: allowed only if every {part} is a constant
        return all(
            isinstance(part, ast.Constant)
            or (
                isinstance(part, ast.FormattedValue)
                and isinstance(part.value, ast.Name)
                and part.value.id in constants
                and _is_constant_sql(constants[part.value.id], constants)
            )
            for part in node.values
        )
    if isinstance(node, ast.Name):
        return node.id in constants and _is_constant_sql(constants[node.id], constants)
    return False


REPO_DIR = Path(__file__).resolve().parents[2] / "app" / "db" / "repositories"


@pytest.mark.parametrize("path", sorted(REPO_DIR.glob("*.py")), ids=lambda p: p.name)
def test_every_execute_call_uses_a_constant_sql_string(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    constants = _module_constants(tree)
    executes = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "execute"
    ]
    for call in executes:
        assert call.args, f"{path.name}:{call.lineno} execute() without SQL"
        assert _is_constant_sql(call.args[0], constants), (
            f"{path.name}:{call.lineno} builds SQL from a non-constant"
        )
    if path.name in {"users.py", "audit.py"}:
        assert executes, "scan found no execute() calls: the test would be vacuous"


def test_scan_would_catch_string_formatting():
    """Sanity check of the scanner itself: it must flag an f-string built from a variable."""
    bad = ast.parse("def f(c, e):\n    c.execute(f\"SELECT * FROM users WHERE email = '{e}'\")\n")
    call = next(
        n
        for n in ast.walk(bad)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "execute"
    )
    assert _is_constant_sql(call.args[0], {}) is False
