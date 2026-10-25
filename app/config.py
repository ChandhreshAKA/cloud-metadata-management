"""Application settings, read from environment variables only.

A .env file (see .env.example) is loaded into the environment by app.main at start-up;
this module itself never opens files. Missing or weak values fail fast with a message that
names the variable but never prints its value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_PLACEHOLDER_MARKERS = ("change-me", "put-your")
_MIN_JWT_SECRET_LENGTH = 32
_ALLOWED_ENVIRONMENTS = ("dev", "test", "production")


class ConfigError(RuntimeError):
    """Raised when a required environment variable is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    oracle_dsn: str
    oracle_user: str
    oracle_password: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 30
    db_pool_min: int = 1
    db_pool_max: int = 5
    app_env: str = "dev"

    def __repr__(self) -> str:  # never leak secrets into logs or tracebacks
        return (
            f"Settings(oracle_dsn={self.oracle_dsn!r}, oracle_user={self.oracle_user!r}, "
            f"jwt_algorithm={self.jwt_algorithm!r}, jwt_expiry_minutes={self.jwt_expiry_minutes}, "
            f"db_pool={self.db_pool_min}-{self.db_pool_max}, app_env={self.app_env!r}, "
            "oracle_password=<hidden>, jwt_secret=<hidden>)"
        )


def _required(env: dict[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"Environment variable {name} is not set (see .env.example)")
    return value


def _int(env: dict[str, str], name: str, default: int, minimum: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {name} must be an integer") from exc
    if value < minimum:
        raise ConfigError(f"Environment variable {name} must be at least {minimum}")
    return value


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build Settings from `env` (defaults to os.environ). Pass a dict in tests."""
    source = dict(os.environ) if env is None else env

    app_env = source.get("APP_ENV", "dev").strip().lower() or "dev"
    if app_env not in _ALLOWED_ENVIRONMENTS:
        raise ConfigError(f"APP_ENV must be one of {', '.join(_ALLOWED_ENVIRONMENTS)}")

    jwt_secret = _required(source, "JWT_SECRET")
    if len(jwt_secret) < _MIN_JWT_SECRET_LENGTH:
        raise ConfigError(f"JWT_SECRET must be at least {_MIN_JWT_SECRET_LENGTH} characters")
    if app_env == "production" and any(m in jwt_secret.lower() for m in _PLACEHOLDER_MARKERS):
        raise ConfigError("JWT_SECRET still contains the .env.example placeholder")

    jwt_algorithm = source.get("JWT_ALGORITHM", "HS256").strip() or "HS256"
    if jwt_algorithm not in ("HS256", "HS384", "HS512"):
        raise ConfigError("JWT_ALGORITHM must be HS256, HS384 or HS512")

    pool_min = _int(source, "DB_POOL_MIN", 1, 1)
    pool_max = _int(source, "DB_POOL_MAX", 5, 1)
    if pool_max < pool_min:
        raise ConfigError("DB_POOL_MAX must be >= DB_POOL_MIN")

    return Settings(
        oracle_dsn=_required(source, "ORACLE_DSN"),
        oracle_user=_required(source, "ORACLE_USER"),
        oracle_password=_required(source, "ORACLE_PASSWORD"),
        jwt_secret=jwt_secret,
        jwt_algorithm=jwt_algorithm,
        jwt_expiry_minutes=_int(source, "JWT_EXPIRY_MINUTES", 30, 1),
        db_pool_min=pool_min,
        db_pool_max=pool_max,
        app_env=app_env,
    )
