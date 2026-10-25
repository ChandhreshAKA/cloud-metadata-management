"""Encryption service and the simulated storage adapter (no database, no HTTP)."""

import os
from pathlib import Path

import pytest

from app.config import ConfigError, load_settings
from app.crypto.service import (
    ALGORITHM,
    CryptoService,
    DecryptionError,
    KeyVersionError,
    sha256_hex,
)
from app.storage.base import ObjectNotFoundError, StorageError
from app.storage.simulated import SimulatedStorage, SimulatedStorageRegistry

MASTER = bytes(range(32))  # TEST ONLY
OTHER_MASTER = bytes(range(1, 33))
KEY = "u1/0123456789abcdef"


def service(master=MASTER, version=1) -> CryptoService:
    return CryptoService(master, version)


# ---- AES-256-GCM envelope encryption ---------------------------------------------------------
def test_round_trip_returns_identical_bytes():
    for plaintext in (b"x", b"hello world", os.urandom(1), os.urandom(4096), b"\x00" * 1000):
        payload = service().encrypt(plaintext, KEY)
        back = service().decrypt(
            payload.ciphertext, payload.wrapped_dek, payload.nonce, payload.master_key_version, KEY
        )
        assert back == plaintext


def test_shapes_of_what_is_stored():
    payload = service().encrypt(b"a" * 100, KEY)
    assert ALGORITHM == "AES-256-GCM"
    assert len(payload.ciphertext) == 100 + 16  # plaintext + GCM tag
    assert len(payload.nonce) == 12
    assert len(payload.wrapped_dek) == 12 + 32 + 16  # wrap nonce + 256-bit key + tag
    assert payload.master_key_version == 1
    assert payload.ciphertext_sha256 == sha256_hex(payload.ciphertext)


def test_ciphertext_does_not_contain_the_plaintext():
    plaintext = b"TOP-SECRET-MARKER-" * 50
    payload = service().encrypt(plaintext, KEY)
    assert b"TOP-SECRET-MARKER" not in payload.ciphertext
    assert b"TOP-SECRET-MARKER" not in payload.wrapped_dek


def test_every_encryption_uses_a_fresh_data_key_and_nonce():
    a, b = service().encrypt(b"same", KEY), service().encrypt(b"same", KEY)
    assert a.nonce != b.nonce and a.wrapped_dek != b.wrapped_dek and a.ciphertext != b.ciphertext


def test_the_data_key_is_only_recoverable_with_the_master_key():
    payload = service().encrypt(b"data", KEY)
    dek = service().unwrap_dek(payload.wrapped_dek, 1)
    assert len(dek) == 32 and dek not in payload.wrapped_dek and dek not in payload.ciphertext
    with pytest.raises(DecryptionError):
        service(OTHER_MASTER).unwrap_dek(payload.wrapped_dek, 1)


@pytest.mark.parametrize("position", [0, 5, 49, 50, 65])
def test_flipping_any_single_ciphertext_byte_fails_authentication(position):
    payload = service().encrypt(b"b" * 50, KEY)  # 50 bytes of data + 16 of tag = 66 bytes
    tampered = bytearray(payload.ciphertext)
    tampered[position] ^= 0x01
    with pytest.raises(DecryptionError):
        service().decrypt(bytes(tampered), payload.wrapped_dek, payload.nonce, 1, KEY)


def test_modified_nonce_wrapped_key_or_truncation_fail():
    payload = service().encrypt(b"c" * 30, KEY)
    flipped_nonce = bytes([payload.nonce[0] ^ 1]) + payload.nonce[1:]
    flipped_wrapped = (
        bytes([payload.wrapped_dek[20] ^ 1]) + payload.wrapped_dek[1:20] + payload.wrapped_dek[21:]
    )
    for args in (
        (payload.ciphertext, payload.wrapped_dek, flipped_nonce),
        (
            payload.ciphertext,
            flipped_wrapped[:20] + bytes([payload.wrapped_dek[20] ^ 1]) + payload.wrapped_dek[21:],
            payload.nonce,
        ),
        (payload.ciphertext[:-1], payload.wrapped_dek, payload.nonce),
        (payload.ciphertext + b"\x00", payload.wrapped_dek, payload.nonce),
        (b"", payload.wrapped_dek, payload.nonce),
    ):
        with pytest.raises(DecryptionError):
            service().decrypt(args[0], args[1], args[2], 1, KEY)


def test_ciphertext_is_bound_to_its_object_key():
    """Copying a ciphertext under another object name must not decrypt (associated data)."""
    payload = service().encrypt(b"bound", KEY)
    with pytest.raises(DecryptionError):
        service().decrypt(
            payload.ciphertext, payload.wrapped_dek, payload.nonce, 1, "u1/another-key"
        )


def test_wrong_master_key_cannot_decrypt():
    payload = service().encrypt(b"secret", KEY)
    with pytest.raises(DecryptionError):
        service(OTHER_MASTER).decrypt(
            payload.ciphertext, payload.wrapped_dek, payload.nonce, 1, KEY
        )


def test_a_different_master_key_version_is_refused_with_a_clear_error():
    payload = service(version=1).encrypt(b"v1", KEY)
    with pytest.raises(KeyVersionError, match="version 1"):
        service(version=2).decrypt(payload.ciphertext, payload.wrapped_dek, payload.nonce, 1, KEY)


def test_master_key_must_be_32_bytes():
    for bad in (b"", b"short", bytes(16), bytes(33)):
        with pytest.raises(ValueError, match="32 bytes"):
            CryptoService(bad)


def test_sha256_known_answer():
    assert sha256_hex(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


# ---- MASTER_KEY configuration ----------------------------------------------------------------
BASE_ENV = {"ORACLE_DSN": "d", "ORACLE_USER": "u", "ORACLE_PASSWORD": "p", "JWT_SECRET": "s" * 40}
GOOD_KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


def test_master_key_is_decoded_from_base64_and_hidden_from_repr():
    settings = load_settings({**BASE_ENV, "MASTER_KEY": GOOD_KEY})
    assert settings.master_key == bytes(range(32))
    assert GOOD_KEY not in repr(settings) and "master_key=<hidden>" in repr(settings)


def test_url_safe_base64_is_also_accepted():
    url_safe = "-_" + GOOD_KEY[2:]  # same text with URL-safe alphabet characters substituted
    assert len(load_settings({**BASE_ENV, "MASTER_KEY": url_safe}).master_key) == 32


@pytest.mark.parametrize(
    ("value", "message"),
    [("not base64 !!", "base64"), ("AAAA", "exactly 32 bytes"), ("", "MASTER_KEY")],
)
def test_bad_master_keys_are_rejected(value, message):
    with pytest.raises(ConfigError, match=message):
        load_settings({**BASE_ENV, "MASTER_KEY": value})


def test_upload_limit_and_storage_root_have_safe_defaults_and_validation():
    settings = load_settings({**BASE_ENV, "MASTER_KEY": GOOD_KEY})
    assert settings.max_upload_bytes == 50 * 1024 * 1024 and settings.storage_root == "storage_sim"
    with pytest.raises(ConfigError, match="MAX_UPLOAD_BYTES"):
        load_settings({**BASE_ENV, "MASTER_KEY": GOOD_KEY, "MAX_UPLOAD_BYTES": "0"})


# ---- the SIMULATED storage adapter -----------------------------------------------------------
def test_adapter_contract(tmp_path):
    storage = SimulatedStorage(tmp_path, "AWS", "ap-south-1")
    assert not storage.exists("u1/abc")
    storage.put("u1/abc", b"ciphertext")
    assert storage.exists("u1/abc") and storage.get("u1/abc") == b"ciphertext"
    assert storage.delete("u1/abc") is True
    assert not storage.exists("u1/abc") and storage.delete("u1/abc") is False
    with pytest.raises(ObjectNotFoundError):
        storage.get("u1/abc")


def test_objects_live_in_folders_named_after_the_provider_and_region(tmp_path):
    registry = SimulatedStorageRegistry(tmp_path)
    registry.for_region("AZURE", "Central India").put("u2/obj", b"x")
    assert (tmp_path / "AZURE" / "Central_India" / "u2" / "obj").read_bytes() == b"x"
    assert not registry.for_region("AWS", "ap-south-1").exists("u2/obj")  # providers are isolated


def test_put_never_overwrites(tmp_path):
    storage = SimulatedStorage(tmp_path, "AWS", "r")
    storage.put("u1/k", b"first")
    with pytest.raises(StorageError, match="already exists"):
        storage.put("u1/k", b"second")
    assert storage.get("u1/k") == b"first"


@pytest.mark.parametrize(
    "key",
    [
        "",
        "../x",
        "/abs",
        "a/../../b",
        "a//b",
        "a\\b",
        "..",
        ".",
        "x" * 400,
        "a/../b",
        " lead",
        "ä/b",
    ],
)
def test_dangerous_object_keys_are_rejected(tmp_path, key):
    storage = SimulatedStorage(tmp_path, "AWS", "r")
    with pytest.raises(StorageError):
        storage.put(key, b"x")
    with pytest.raises(StorageError):
        storage.get(key)
    assert not any(p.is_file() for p in tmp_path.rglob("*"))


def test_a_failed_write_leaves_no_temporary_file(tmp_path, monkeypatch):
    storage = SimulatedStorage(tmp_path, "AWS", "r")

    def broken_replace(*_args):
        raise OSError("disk full")

    monkeypatch.setattr("app.storage.simulated.os.replace", broken_replace)
    with pytest.raises(StorageError, match="could not write"):
        storage.put("u1/k", b"data")
    assert not [p for p in Path(tmp_path).rglob("*") if p.is_file()]
