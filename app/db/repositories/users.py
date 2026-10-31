"""User repository implemented with the SQLAlchemy ORM.

The ORM turns every value into a bind parameter, so request data is never part of the SQL text.
No raw SQL is needed for this table.
"""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.base import UserRecord
from app.db.orm import User


def _to_record(user: User | None) -> UserRecord | None:
    if user is None:
        return None
    return UserRecord(
        int(user.user_id),
        user.full_name,
        user.email,
        user.role,
        user.password_hash,
        user.account_status,
    )


class SqlAlchemyUserRepository:
    def __init__(self, session: Session):
        self._session = session

    def create(self, full_name: str, email: str, password_hash: str) -> int:
        user = User(
            full_name=full_name,
            email=email,
            role="USER",  # roles are never self-assigned
            password_hash=password_hash,
            account_status="ACTIVE",
        )
        self._session.add(user)
        # Send the INSERT now so a constraint violation (for example a duplicate e-mail) is raised
        # here, inside the caller's transaction block, and not later at commit time.
        self._session.flush()
        return int(user.user_id)

    def get_by_email(self, email: str) -> UserRecord | None:
        statement = select(User).where(User.email == email)
        return _to_record(self._session.execute(statement).scalar_one_or_none())

    def get_by_id(self, user_id: int) -> UserRecord | None:
        statement = select(User).where(User.user_id == user_id)
        return _to_record(self._session.execute(statement).scalar_one_or_none())

    def touch_last_login(self, user_id: int) -> None:
        statement = (
            update(User)
            .where(User.user_id == user_id)
            .values(last_login_at=func.current_timestamp())
            .execution_options(synchronize_session=False)
        )
        self._session.execute(statement)
