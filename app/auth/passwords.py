"""Password hashing with Argon2id (argon2-cffi defaults, which follow RFC 9106 guidance)."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128  # bounds the work an attacker can force per request

_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    """Return an encoded Argon2id hash (includes a random salt and the parameters)."""
    return _hasher.hash(plain)


def verify_password(stored_hash: str, plain: str) -> bool:
    """True only if `plain` matches `stored_hash`. Never raises for bad input."""
    try:
        return _hasher.verify(stored_hash, plain)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)


# Verified against when the e-mail is unknown, so "no such user" and "wrong password"
# take about the same time and cannot be told apart by timing.
DUMMY_HASH = _hasher.hash("not-a-real-password-used-for-timing-only")
