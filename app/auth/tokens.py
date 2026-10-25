"""JWT creation and validation (PyJWT, HMAC signing)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.config import Settings


class TokenError(Exception):
    """The token is missing, malformed, badly signed or expired."""


class TokenExpiredError(TokenError):
    pass


@dataclass(frozen=True)
class TokenClaims:
    user_id: int
    role: str  # informational only: authorization always re-reads the role from the database
    expires_at: datetime


def create_access_token(
    settings: Settings,
    user_id: int,
    role: str,
    *,
    now: datetime | None = None,
    expires_minutes: int | None = None,
) -> str:
    """`now` and `expires_minutes` exist so tests can create already-expired tokens."""
    issued = now or datetime.now(UTC)
    minutes = settings.jwt_expiry_minutes if expires_minutes is None else expires_minutes
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": int(issued.timestamp()),
        "exp": int((issued + timedelta(minutes=minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(settings: Settings, token: str) -> TokenClaims:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],  # fixed list: blocks alg=none and alg confusion
            options={"require": ["exp", "sub", "iat"]},
        )
        return TokenClaims(
            user_id=int(payload["sub"]),
            role=str(payload.get("role", "")),
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError("token expired") from exc
    except (jwt.InvalidTokenError, ValueError, KeyError) as exc:
        raise TokenError("invalid token") from exc
