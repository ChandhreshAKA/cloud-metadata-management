"""Test doubles. NOTHING in this file talks to Oracle.

FakeDatabase: an in-memory implementation of app.db.base.Database. Tests that use it prove the
APPLICATION LOGIC (auth flow, RBAC, error mapping, audit decisions). They do NOT prove that the
SQL is valid on Oracle or that Oracle enforces the constraints; tests/integration does that.

RecordingConnection: records (sql, binds) passed to the REAL repository classes, to prove that
user input only ever travels as bind values.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.db.base import UserRecord
from app.db.errors import DatabaseError, DatabaseUnavailableError, UniqueViolationError


class _FakeUsers:
    def __init__(self, state: dict[str, Any]):
        self._s = state

    def create(self, full_name: str, email: str, password_hash: str) -> int:
        if any(u.email == email for u in self._s["users"].values()):
            raise UniqueViolationError(
                "ORA-00001: unique constraint (CLOUDMETA.UQ_USERS_EMAIL) violated", "UQ_USERS_EMAIL"
            )
        self._s["next_id"] += 1
        user_id = self._s["next_id"]
        self._s["users"][user_id] = UserRecord(
            user_id, full_name, email, "USER", password_hash, "ACTIVE"
        )
        return user_id

    def get_by_email(self, email: str) -> UserRecord | None:
        return next((u for u in self._s["users"].values() if u.email == email), None)

    def get_by_id(self, user_id: int) -> UserRecord | None:
        return self._s["users"].get(user_id)

    def touch_last_login(self, user_id: int) -> None:
        self._s["last_login"][user_id] = self._s["last_login"].get(user_id, 0) + 1


class _FakeAudit:
    def __init__(self, state: dict[str, Any], database: FakeDatabase):
        self._s = state
        self._db = database

    def record(
        self,
        event_type: str,
        outcome: str,
        *,
        actor_user_id: int | None = None,
        target_type: str | None = None,
        target_id: int | None = None,
        ip_address: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self._db.fail_audit_with is not None:
            raise self._db.fail_audit_with
        self._s["audit"].append(
            {
                "event_type": event_type,
                "outcome": outcome,
                "actor_user_id": actor_user_id,
                "target_type": target_type,
                "target_id": target_id,
                "ip_address": ip_address,
                "details": details,
            }
        )


class FakeSession:
    def __init__(self, state: dict[str, Any], database: FakeDatabase):
        self.users = _FakeUsers(state)
        self.audit = _FakeAudit(state, database)


class FakeDatabase:
    """In-memory database with real commit/rollback behaviour (snapshot and restore)."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {"users": {}, "audit": [], "next_id": 0, "last_login": {}}
        self.available = True
        self.fail_audit_with: Exception | None = None
        self.fail_create_with: Exception | None = None

    @contextmanager
    def transaction(self) -> Iterator[FakeSession]:
        if not self.available:
            raise DatabaseUnavailableError("fake database is down")
        snapshot = copy.deepcopy(self.state)
        session = FakeSession(self.state, self)
        if self.fail_create_with is not None:
            error = self.fail_create_with

            def boom(*_args: Any, **_kwargs: Any) -> int:
                raise error

            session.users.create = boom  # type: ignore[method-assign]
        try:
            yield session
        except BaseException:
            self.state.clear()
            self.state.update(snapshot)
            raise

    def ping(self) -> None:
        if not self.available:
            raise DatabaseUnavailableError("fake database is down")

    def close(self) -> None:
        pass

    # --- helpers for tests -------------------------------------------------------------
    def add_user(
        self,
        email: str,
        password_hash: str | None,
        role: str = "USER",
        status: str = "ACTIVE",
        full_name: str = "Test User",
    ) -> int:
        self.state["next_id"] += 1
        user_id = self.state["next_id"]
        self.state["users"][user_id] = UserRecord(
            user_id, full_name, email, role, password_hash, status
        )
        return user_id

    def set_role(self, user_id: int, role: str) -> None:
        u = self.state["users"][user_id]
        self.state["users"][user_id] = UserRecord(
            u.user_id, u.full_name, u.email, role, u.password_hash, u.account_status
        )

    def set_status(self, user_id: int, status: str) -> None:
        u = self.state["users"][user_id]
        self.state["users"][user_id] = UserRecord(
            u.user_id, u.full_name, u.email, u.role, u.password_hash, status
        )

    @property
    def audit(self) -> list[dict[str, Any]]:
        return self.state["audit"]

    def audit_of(self, event_type: str) -> list[dict[str, Any]]:
        return [e for e in self.state["audit"] if e["event_type"] == event_type]


# --- recording connection (for parameterisation tests) --------------------------------------
class _Var:
    def getvalue(self) -> list[int]:
        return [1]


class RecordingCursor:
    def __init__(self, log: list[tuple[str, Any]], row: tuple[Any, ...] | None = None):
        self._log = log
        self._row = row
        self.description = None

    def var(self, _type: Any) -> _Var:
        return _Var()

    def execute(self, sql: str, binds: Any = None) -> None:
        self._log.append((sql, binds))

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


class RecordingConnection:
    def __init__(self, row: tuple[Any, ...] | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.row = row

    def cursor(self) -> RecordingCursor:
        return RecordingCursor(self.calls, self.row)


__all__ = [
    "DatabaseError",
    "FakeDatabase",
    "RecordingConnection",
]
