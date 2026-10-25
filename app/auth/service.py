"""Registration and login logic (no HTTP here, so it can be tested without a web server)."""

from __future__ import annotations

from dataclasses import dataclass

from app.audit import service as audit
from app.auth.passwords import DUMMY_HASH, hash_password, verify_password
from app.auth.tokens import create_access_token
from app.config import Settings
from app.db.base import Database, UserRecord
from app.db.errors import DuplicateEmailError, UniqueViolationError

ACTIVE = "ACTIVE"


class AuthenticationError(Exception):
    """Login failed. The message is deliberately the same for every cause."""


@dataclass(frozen=True)
class LoginResult:
    access_token: str
    expires_in_seconds: int
    user: UserRecord


def register_user(
    database: Database, full_name: str, email: str, password: str, ip_address: str | None = None
) -> int:
    """Create an ACTIVE user with role USER. Roles other than USER are never self-assigned."""
    password_hash = hash_password(password)
    try:
        with database.transaction() as session:
            user_id = session.users.create(full_name, email, password_hash)
            session.audit.record(
                audit.USER_REGISTERED,
                audit.SUCCESS,
                actor_user_id=user_id,
                target_type="USER",
                target_id=user_id,
                ip_address=ip_address,
            )
    except UniqueViolationError as exc:
        if exc.constraint == "UQ_USERS_EMAIL":
            audit.record_event(
                database,
                audit.USER_REGISTERED,
                audit.FAILURE,
                ip_address=ip_address,
                details={"reason": "duplicate_email", "email": email[:150]},
            )
            raise DuplicateEmailError from exc
        raise
    return user_id


def authenticate(
    database: Database,
    settings: Settings,
    email: str,
    password: str,
    ip_address: str | None = None,
) -> LoginResult:
    with database.transaction() as session:
        user = session.users.get_by_email(email)

    # Always run one Argon2 verification, so unknown users cost the same time as known ones.
    stored_hash = user.password_hash if user and user.password_hash else DUMMY_HASH
    password_ok = verify_password(stored_hash, password)

    reason = None
    if user is None:
        reason = "unknown_user"
    elif not password_ok or not user.password_hash:
        reason = "bad_password"
    elif user.account_status != ACTIVE:
        reason = "account_not_active"

    if reason is not None or user is None:
        audit.record_event(
            database,
            audit.LOGIN_FAILED,
            audit.FAILURE,
            actor_user_id=user.user_id if user else None,
            ip_address=ip_address,
            details={"reason": reason, "email": email[:150]},
        )
        raise AuthenticationError("Invalid e-mail or password")

    with database.transaction() as session:
        session.users.touch_last_login(user.user_id)
        session.audit.record(
            audit.LOGIN_OK,
            audit.SUCCESS,
            actor_user_id=user.user_id,
            target_type="USER",
            target_id=user.user_id,
            ip_address=ip_address,
        )
    token = create_access_token(settings, user.user_id, user.role)
    return LoginResult(token, settings.jwt_expiry_minutes * 60, user)
