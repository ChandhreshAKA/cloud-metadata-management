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
import textwrap
from pathlib import Path

import pytest

from app.db.repositories.audit import OracleAuditRepository
from app.db.repositories.users import SqlAlchemyUserRepository
from tests.conftest import PASSWORD
from tests.fakes import RecordingConnection
from tests.orm_helpers import capture_statements, sqlite_session

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
    audit = OracleAuditRepository(connection)
    audit.record("LOGIN_FAILED", "FAILURE", details={"email": payload}, ip_address=payload)
    assert len(connection.calls) == 1
    for sql, binds in connection.calls:
        assert payload not in sql
        assert isinstance(binds, dict)  # named binds, not string interpolation
        for name in binds:
            assert f":{name}" in sql  # every bind has a placeholder in the statement


@pytest.mark.parametrize("payload", PAYLOADS)
def test_orm_user_repository_sends_the_payload_only_as_a_parameter(payload):
    """The ORM users repository: statements the driver receives never contain the payload."""
    with sqlite_session() as session:
        repo = SqlAlchemyUserRepository(session)
        with capture_statements(session) as seen:
            repo.get_by_email(payload)
            repo.get_by_id(1)
            repo.create(payload, payload, payload)
            repo.touch_last_login(1)
    assert seen, "no statement captured: the test would be vacuous"
    for statement, parameters in seen:
        assert payload not in statement
        assert parameters  # values travel separately from the SQL text
    assert any(payload in str(parameters) for _, parameters in seen)


def test_sql_text_is_identical_for_normal_and_hostile_input():
    with sqlite_session() as session:
        users = SqlAlchemyUserRepository(session)
        with capture_statements(session) as seen:
            users.get_by_email("ada@example.com")
            users.get_by_email("' OR '1'='1")
    assert len(seen) == 2 and seen[0][0] == seen[1][0]


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


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _check_execute_calls(tree: ast.Module) -> list[str]:
    """Return a problem description for every execute() whose SQL is not provably constant.

    Accepted: a literal, a module constant, an f-string of module constants, or a *parameter* of
    the enclosing function (a helper such as `_all(self, sql, binds)`) provided EVERY call to
    that helper passes a constant for that parameter.
    """
    constants = _module_constants(tree)
    problems: list[str] = []
    functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    for function in functions:
        params = [a.arg for a in function.args.args]
        offset = 1 if params and params[0] == "self" else 0
        for call in (n for n in ast.walk(function) if isinstance(n, ast.Call)):
            if not (isinstance(call.func, ast.Attribute) and call.func.attr == "execute"):
                continue
            if not call.args:
                problems.append(f"line {call.lineno}: execute() without SQL")
                continue
            sql = call.args[0]
            if _is_constant_sql(sql, constants):
                continue
            if isinstance(sql, ast.Name) and sql.id in params:
                index = params.index(sql.id) - offset
                callers = [
                    c for c in ast.walk(tree)
                    if isinstance(c, ast.Call) and _call_name(c) == function.name
                ]  # fmt: skip
                if not callers:
                    problems.append(f"line {call.lineno}: helper {function.name} is never called")
                for caller in callers:
                    if len(caller.args) <= index or not _is_constant_sql(
                        caller.args[index], constants
                    ):
                        problems.append(
                            f"line {caller.lineno}: {function.name}() called with non-constant SQL"
                        )
                continue
            problems.append(f"line {call.lineno}: SQL built from a non-constant")
    return problems


ORM_MODULES = {"users.py"}  # use the ORM: no raw SQL strings, no cursors


@pytest.mark.parametrize(
    "path",
    sorted(p for p in REPO_DIR.glob("*.py") if p.name not in ORM_MODULES),
    ids=lambda p: p.name,
)
def test_every_execute_call_uses_a_constant_sql_string(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert _check_execute_calls(tree) == [], path.name
    if path.name in {"audit.py", "providers.py"}:
        executes = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "execute"
        ]  # fmt: skip
        assert executes, "scan found no execute() calls: the test would be vacuous"


@pytest.mark.parametrize("name", sorted(ORM_MODULES))
def test_orm_repository_modules_contain_no_raw_sql(name):
    tree = ast.parse((REPO_DIR / name).read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("sqlalchemy")
        for alias in node.names
    }
    assert "text" not in imported and "select" in imported  # ORM constructs, never text()
    calls = {getattr(n.func, "attr", "") for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "cursor" not in calls  # no direct driver cursor
    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        if getattr(call.func, "attr", "") == "execute" and call.args:
            assert not isinstance(call.args[0], ast.Constant)  # never a SQL string literal


def test_scan_flags_a_helper_called_with_a_formatted_string():
    bad = ast.parse(
        textwrap.dedent(
            """
            class R:
                def _all(self, sql, binds=None):
                    self.c.execute(sql, binds)

                def find(self, name):
                    return self._all(f"SELECT * FROM t WHERE n = '{name}'")
            """
        )
    )
    assert any("non-constant SQL" in p for p in _check_execute_calls(bad))


def test_scan_accepts_a_helper_only_called_with_constants():
    good = ast.parse(
        textwrap.dedent(
            """
            Q = "SELECT 1 FROM dual"

            class R:
                def _all(self, sql, binds=None):
                    self.c.execute(sql, binds)

                def find(self):
                    return self._all(Q)
            """
        )
    )
    assert _check_execute_calls(good) == []


def test_scan_would_catch_string_formatting():
    """Sanity check of the scanner itself: it must flag an f-string built from a variable."""
    bad = ast.parse("def f(c, e):\n    c.execute(f\"SELECT * FROM users WHERE email = '{e}'\")\n")
    call = next(
        n
        for n in ast.walk(bad)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "execute"
    )
    assert _is_constant_sql(call.args[0], {}) is False
