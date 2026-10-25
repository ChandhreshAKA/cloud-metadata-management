"""SQL for the users table. Column names are those of sql/schema.sql (Chunk 1).

Every statement uses bind variables (:name). Nothing from a request is ever concatenated into SQL.
"""

from __future__ import annotations

from typing import Any

from app.db.base import UserRecord

_SELECT_COLUMNS = "user_id, full_name, email, role, password_hash, account_status"

_INSERT_USER = """
    INSERT INTO users (full_name, email, role, password_hash, account_status)
    VALUES (:full_name, :email, 'USER', :password_hash, 'ACTIVE')
    RETURNING user_id INTO :new_id
"""

_SELECT_BY_EMAIL = f"SELECT {_SELECT_COLUMNS} FROM users WHERE email = :email"  # noqa: S608
_SELECT_BY_ID = f"SELECT {_SELECT_COLUMNS} FROM users WHERE user_id = :user_id"  # noqa: S608
_TOUCH_LAST_LOGIN = "UPDATE users SET last_login_at = SYSTIMESTAMP WHERE user_id = :user_id"


def _to_record(row: tuple[Any, ...] | None) -> UserRecord | None:
    if row is None:
        return None
    user_id, full_name, email, role, password_hash, account_status = row
    return UserRecord(int(user_id), full_name, email, role, password_hash, account_status)


class OracleUserRepository:
    def __init__(self, connection: Any):
        self._conn = connection

    def create(self, full_name: str, email: str, password_hash: str) -> int:
        cursor = self._conn.cursor()
        new_id = cursor.var(int)
        cursor.execute(
            _INSERT_USER,
            {
                "full_name": full_name,
                "email": email,
                "password_hash": password_hash,
                "new_id": new_id,
            },
        )
        return int(new_id.getvalue()[0])

    def get_by_email(self, email: str) -> UserRecord | None:
        cursor = self._conn.cursor()
        cursor.execute(_SELECT_BY_EMAIL, {"email": email})
        return _to_record(cursor.fetchone())

    def get_by_id(self, user_id: int) -> UserRecord | None:
        cursor = self._conn.cursor()
        cursor.execute(_SELECT_BY_ID, {"user_id": user_id})
        return _to_record(cursor.fetchone())

    def touch_last_login(self, user_id: int) -> None:
        cursor = self._conn.cursor()
        cursor.execute(_TOUCH_LAST_LOGIN, {"user_id": user_id})
