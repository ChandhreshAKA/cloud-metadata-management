from app.auth.passwords import DUMMY_HASH, hash_password, needs_rehash, verify_password


def test_hash_is_argon2id_and_not_the_plaintext():
    hashed = hash_password("a-long-test-password")
    assert hashed.startswith("$argon2id$")
    assert "a-long-test-password" not in hashed


def test_same_password_gives_different_hashes_because_of_the_salt():
    assert hash_password("same-password-123") != hash_password("same-password-123")


def test_correct_password_verifies_and_wrong_one_does_not():
    hashed = hash_password("right-password-123")
    assert verify_password(hashed, "right-password-123") is True
    assert verify_password(hashed, "wrong-password-123") is False
    assert verify_password(hashed, "") is False


def test_malformed_hash_is_a_failed_verification_not_an_exception():
    assert verify_password("not-a-hash", "whatever") is False
    assert verify_password("", "whatever") is False


def test_fresh_hash_does_not_need_rehash():
    assert needs_rehash(hash_password("another-password-1")) is False


def test_dummy_hash_is_a_valid_argon2_hash_that_matches_nothing_real():
    assert DUMMY_HASH.startswith("$argon2id$")
    assert verify_password(DUMMY_HASH, "any-real-password") is False
