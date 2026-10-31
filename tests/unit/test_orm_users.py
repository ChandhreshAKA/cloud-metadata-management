"""ORM users repository and the transaction wrapper, on in-memory SQLite (no Oracle).

What this proves: the mapping, the repository logic, commit/rollback through OracleDatabase, and
the translation of SQLAlchemy-wrapped Oracle errors. What it does NOT prove: that Oracle accepts
the SQL (tests/integration/test_oracle_integration.py does).
"""

import pytest
from sqlalchemy import insert, select
from sqlalchemy.dialects import oracle
from sqlalchemy.exc import IntegrityError

from app.db.errors import (
    CheckViolationError,
    DatabaseError,
    UniqueViolationError,
)
from app.db.orm import User
from app.db.pool import OracleDatabase, _translate
from app.db.repositories.users import SqlAlchemyUserRepository
from tests.orm_helpers import sqlite_engine, sqlite_session


class _StubPool:
    def close(self, force: bool = False) -> None:
        pass


def _database() -> OracleDatabase:
    return OracleDatabase(_StubPool(), engine=sqlite_engine())  # type: ignore[arg-type]


# ---- repository -------------------------------------------------------------------------------
def test_create_returns_the_new_id_and_sets_role_and_status():
    with sqlite_session() as session:
        repo = SqlAlchemyUserRepository(session)
        first = repo.create("Ada", "ada@example.com", "h1")
        second = repo.create("Bob", "bob@example.com", "h2")
        assert second == first + 1
        row = session.execute(select(User).where(User.user_id == first)).scalar_one()
        assert (row.role, row.account_status, row.password_hash) == ("USER", "ACTIVE", "h1")


def test_lookups_by_email_and_id_agree_and_miss_cleanly():
    with sqlite_session() as session:
        repo = SqlAlchemyUserRepository(session)
        user_id = repo.create("Ada", "ada@example.com", "h")
        assert repo.get_by_email("ada@example.com") == repo.get_by_id(user_id)
        assert repo.get_by_email("nobody@example.com") is None
        assert repo.get_by_id(999) is None


def test_touch_last_login_sets_the_timestamp():
    with sqlite_session() as session:
        repo = SqlAlchemyUserRepository(session)
        user_id = repo.create("Ada", "ada@example.com", "h")
        assert session.get(User, user_id).last_login_at is None
        repo.touch_last_login(user_id)
        session.expire_all()
        assert session.get(User, user_id).last_login_at is not None


def test_duplicate_email_fails_at_create_not_later():
    with sqlite_session() as session:
        repo = SqlAlchemyUserRepository(session)
        repo.create("Ada", "ada@example.com", "h")
        with pytest.raises(IntegrityError):
            repo.create("Ada 2", "ada@example.com", "h")


def test_oracle_insert_reads_the_identity_back_with_returning_into():
    statement = insert(User).values(
        full_name="Ada", email="ada@example.com", role="USER",
        password_hash="h", account_status="ACTIVE",
    )  # fmt: skip
    sql = str(statement.compile(dialect=oracle.dialect()))
    assert "RETURNING users.user_id INTO" in sql
    assert "ada@example.com" not in sql  # values are binds
    assert "user_id" not in sql.split("VALUES")[0]  # the identity column is not inserted


# ---- error translation ------------------------------------------------------------------------
def _wrapped(ora_text: str) -> IntegrityError:
    return IntegrityError("INSERT ...", {}, Exception(ora_text))


def test_wrapped_unique_violation_keeps_the_constraint_name():
    error = _translate(_wrapped("ORA-00001: unique constraint (CLOUDMETA.UQ_USERS_EMAIL) violated"))
    assert isinstance(error, UniqueViolationError)
    assert error.constraint == "UQ_USERS_EMAIL"


def test_wrapped_check_violation_is_mapped():
    error = _translate(_wrapped("ORA-02290: check constraint (CLOUDMETA.CK_USERS_ROLE) violated"))
    assert isinstance(error, CheckViolationError)
    assert error.constraint == "CK_USERS_ROLE"


# ---- transaction wrapper ----------------------------------------------------------------------
def _count(database: OracleDatabase, email: str) -> int:
    with database.transaction() as session:
        cursor = session.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE email = ?", (email,))
        return cursor.fetchone()[0]


def test_transaction_commits_when_the_block_succeeds():
    database = _database()
    with database.transaction() as session:
        session.users.create("Kept", "kept@example.com", "h")
    assert _count(database, "kept@example.com") == 1


def test_transaction_rolls_back_when_the_block_raises():
    database = _database()
    with pytest.raises(RuntimeError), database.transaction() as session:
        session.users.create("Temp", "temp@example.com", "h")
        raise RuntimeError("boom")
    assert _count(database, "temp@example.com") == 0


def test_database_error_inside_the_block_becomes_a_domain_error_and_rolls_back():
    database = _database()
    with database.transaction() as session:
        session.users.create("Ada", "ada@example.com", "h")
    with pytest.raises(DatabaseError), database.transaction() as session:
        session.users.create("Other", "other@example.com", "h")
        session.users.create("Ada again", "ada@example.com", "h")  # duplicate
    assert _count(database, "other@example.com") == 0  # the whole block was rolled back
    assert _count(database, "ada@example.com") == 1


def test_orm_and_raw_cursor_work_share_one_transaction():
    database = _database()
    with pytest.raises(RuntimeError), database.transaction() as session:
        session.users.create("Orm", "orm@example.com", "h")
        cursor = session.connection.cursor()
        cursor.execute(
            "INSERT INTO users (full_name, email, role, account_status) "
            "VALUES ('Raw', 'raw@example.com', 'USER', 'ACTIVE')"
        )
        raise RuntimeError("boom")
    assert _count(database, "orm@example.com") == 0
    assert _count(database, "raw@example.com") == 0

    with database.transaction() as session:
        session.users.create("Orm", "orm@example.com", "h")
        session.connection.cursor().execute(
            "INSERT INTO users (full_name, email, role, account_status) "
            "VALUES ('Raw', 'raw@example.com', 'USER', 'ACTIVE')"
        )
    assert _count(database, "orm@example.com") == 1
    assert _count(database, "raw@example.com") == 1
