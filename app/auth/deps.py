"""FastAPI dependencies: current user and reusable permission / role checks."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.audit import service as audit
from app.auth.rbac import Permission, Role, has_permission
from app.auth.service import ACTIVE
from app.auth.tokens import TokenError, TokenExpiredError, decode_access_token
from app.config import Settings
from app.db.base import Database, UserRecord

_bearer = HTTPBearer(auto_error=False, description="Paste the access_token from POST /auth/login")
_UNAUTHENTICATED = {"WWW-Authenticate": "Bearer"}


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_crypto(request: Request):
    return request.app.state.crypto


def get_storage(request: Request):
    return request.app.state.storage


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
    database: Database = Depends(get_database),
) -> UserRecord:
    """Validate the bearer token, then load the user (and ROLE) fresh from the database.

    Re-reading the user means a disabled account or a changed role takes effect immediately,
    not when the token expires.
    """
    if credentials is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers=_UNAUTHENTICATED
        )
    try:
        claims = decode_access_token(settings, credentials.credentials)
    except TokenExpiredError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Token expired", headers=_UNAUTHENTICATED
        ) from None
    except TokenError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid token", headers=_UNAUTHENTICATED
        ) from None

    with database.transaction() as session:
        user = session.users.get_by_id(claims.user_id)
    if user is None or user.account_status != ACTIVE:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token", headers=_UNAUTHENTICATED)
    return user


def _deny(request: Request, database: Database, user: UserRecord, required: str) -> None:
    audit.record_event(
        database,
        audit.ACCESS_DENIED,
        audit.DENIED,
        actor_user_id=user.user_id,
        ip_address=client_ip(request),
        details={"required": required, "method": request.method, "path": request.url.path},
    )
    raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have permission to do this")


def require_permission(permission: Permission) -> Callable[..., UserRecord]:
    """Dependency factory: allow only roles whose permission set contains `permission`."""

    def dependency(
        request: Request,
        user: UserRecord = Depends(current_user),
        database: Database = Depends(get_database),
    ) -> UserRecord:
        if not has_permission(user.role, permission):
            _deny(request, database, user, permission.value)
        return user

    return dependency


def require_role(*roles: Role) -> Callable[..., UserRecord]:
    """Dependency factory for the rare case a rule really is about the role itself."""
    allowed = {r.value for r in roles}

    def dependency(
        request: Request,
        user: UserRecord = Depends(current_user),
        database: Database = Depends(get_database),
    ) -> UserRecord:
        if user.role not in allowed:
            _deny(request, database, user, "role:" + "|".join(sorted(allowed)))
        return user

    return dependency
