from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.auth.tokens import (
    TokenError,
    TokenExpiredError,
    create_access_token,
    decode_access_token,
)


def test_round_trip_returns_the_claims(settings):
    token = create_access_token(settings, user_id=42, role="ADMIN")
    claims = decode_access_token(settings, token)
    assert claims.user_id == 42
    assert claims.role == "ADMIN"


def test_expiry_is_set_from_the_configured_minutes(settings):
    now = datetime.now(UTC)
    token = create_access_token(settings, 1, "USER", now=now)
    claims = decode_access_token(settings, token)
    assert abs((claims.expires_at - (now + timedelta(minutes=30))).total_seconds()) <= 1


def test_expired_token_is_rejected_as_expired(settings):
    two_hours_ago = datetime.now(UTC) - timedelta(hours=2)
    token = create_access_token(settings, 1, "USER", now=two_hours_ago, expires_minutes=30)
    with pytest.raises(TokenExpiredError):
        decode_access_token(settings, token)


def test_token_signed_with_another_secret_is_rejected(settings):
    forged = jwt.encode(
        {"sub": "1", "role": "ADMIN", "iat": 1, "exp": 9999999999},
        "a-different-secret-" + "y" * 30,
        algorithm="HS256",
    )
    with pytest.raises(TokenError):
        decode_access_token(settings, forged)


def test_unsigned_alg_none_token_is_rejected(settings):
    unsigned = jwt.encode({"sub": "1", "exp": 9999999999, "iat": 1}, key=None, algorithm="none")
    with pytest.raises(TokenError):
        decode_access_token(settings, unsigned)


def test_tampered_payload_is_rejected(settings):
    token = create_access_token(settings, 1, "USER")
    header, payload, signature = token.split(".")
    other = create_access_token(settings, 2, "ADMIN").split(".")[1]
    assert payload != other
    with pytest.raises(TokenError):
        decode_access_token(settings, ".".join([header, other, signature]))


def test_token_without_expiry_is_rejected(settings):
    no_exp = jwt.encode({"sub": "1", "iat": 1}, settings.jwt_secret, algorithm="HS256")
    with pytest.raises(TokenError):
        decode_access_token(settings, no_exp)


@pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "Bearer xyz"])
def test_garbage_is_rejected(settings, garbage):
    with pytest.raises(TokenError):
        decode_access_token(settings, garbage)


def test_non_numeric_subject_is_rejected(settings):
    token = jwt.encode(
        {"sub": "not-a-number", "iat": 1, "exp": 9999999999}, settings.jwt_secret, algorithm="HS256"
    )
    with pytest.raises(TokenError):
        decode_access_token(settings, token)
