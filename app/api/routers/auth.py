"""Registration, login and 'who am I'."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut
from app.auth import service
from app.auth.deps import client_ip, current_user, get_database, get_settings
from app.config import Settings
from app.db.base import Database, UserRecord

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(
    body: RegisterRequest, request: Request, database: Database = Depends(get_database)
) -> UserOut:
    """Create an account with role USER. A duplicate e-mail returns 409 (see app.main handlers)."""
    user_id = service.register_user(
        database, body.full_name, body.email, body.password, client_ip(request)
    )
    return UserOut(user_id=user_id, full_name=body.full_name, email=body.email, role="USER")


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    request: Request,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    try:
        result = service.authenticate(
            database, settings, body.email, body.password, client_ip(request)
        )
    except service.AuthenticationError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid e-mail or password",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    return TokenResponse(access_token=result.access_token, expires_in=result.expires_in_seconds)


@router.get("/me", response_model=UserOut)
def me(user: UserRecord = Depends(current_user)) -> UserOut:
    return UserOut(user_id=user.user_id, full_name=user.full_name, email=user.email, role=user.role)
