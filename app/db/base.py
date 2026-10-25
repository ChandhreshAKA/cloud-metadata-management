"""Interfaces between the application logic and the database.

The auth and audit code depends only on these Protocols. `app.db.pool.OracleDatabase`
implements them with Oracle; tests/fakes.py implements them in memory. A test that uses the
fake proves application logic, NOT that the SQL is valid on Oracle (the integration tests do that).
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class UserRecord:
    user_id: int
    full_name: str
    email: str
    role: str
    password_hash: str | None
    account_status: str


class UserRepository(Protocol):
    def create(self, full_name: str, email: str, password_hash: str) -> int:
        """Insert an ACTIVE user with role USER and return the new user_id."""
        ...

    def get_by_email(self, email: str) -> UserRecord | None: ...

    def get_by_id(self, user_id: int) -> UserRecord | None: ...

    def touch_last_login(self, user_id: int) -> None: ...


class AuditRepository(Protocol):
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
    ) -> None: ...


class Session(Protocol):
    users: UserRepository
    audit: AuditRepository


class Database(Protocol):
    def transaction(self) -> AbstractContextManager[Session]:
        """Commit when the block ends normally; roll back when it raises."""
        ...

    def ping(self) -> None:
        """Raise DatabaseUnavailableError if the database cannot answer a trivial query."""
        ...

    def close(self) -> None: ...
