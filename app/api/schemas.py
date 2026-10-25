"""Request and response models."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

from app.auth.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH

# Deliberately simple: something@something.tld, no spaces, one @. The database also enforces
# lower-case and presence of "@" (ck_users_email_lower, ck_users_email_format).
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_EMAIL_LENGTH = 150  # users.email is VARCHAR2(150)


def _normalise_email(value: str) -> str:
    email = value.strip().lower()
    if len(email) > MAX_EMAIL_LENGTH or not _EMAIL.fullmatch(email):
        raise ValueError("must be a valid e-mail address")
    return email


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=100)
    email: str
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return _normalise_email(value)

    @field_validator("full_name")
    @classmethod
    def _name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("must not be blank")
        return name


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return _normalise_email(value)


class UserOut(BaseModel):
    user_id: int
    full_name: str
    email: str
    role: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105  (OAuth token type label, not a secret)
    expires_in: int = Field(description="Seconds until the token expires")


class HealthResponse(BaseModel):
    status: str
    database: str
