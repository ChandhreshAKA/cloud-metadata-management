"""Encryption service: envelope encryption with AES-256-GCM (library: `cryptography`).

HOW IT WORKS (this is real cryptography; only the key *management* is simulated)
  1. For every file a fresh random 256-bit data key (DEK) is generated.
  2. The file is encrypted with AES-256-GCM under that DEK and a fresh random 96-bit nonce. The
     object's key name is bound in as "associated data", so a ciphertext copied under another
     object name will not decrypt. GCM also authenticates: any changed byte fails decryption.
  3. The DEK is "wrapped": encrypted with AES-256-GCM under the MASTER KEY (its own random nonce
     and the key version as associated data). wrapped_dek = wrap_nonce(12) + ciphertext + tag(16).
  4. The database stores ONLY the wrapped DEK, the file nonce, the algorithm, the master-key
     version and the SHA-256 of the stored ciphertext. The plaintext DEK and the master key are
     never stored in the database, and the master key is never written to disk by this code.
  5. The storage provider only ever receives ciphertext.

WHAT IS SIMULATED
  The master key comes from the MASTER_KEY environment variable and lives in this process's memory.
  A real cloud KMS (AWS KMS, Azure Key Vault, Google Cloud KMS) would add:
    * a master key that never leaves a hardware security module: the application sends the wrapped
      key to the KMS and gets the data key back, instead of holding the master key itself;
    * key policies and IAM, so only named identities may use the key, with separation of duties;
    * automatic rotation and key versions, with old versions still usable for decryption;
    * an audit trail of every use of the key (who unwrapped which data key, and when);
    * FIPS 140 validated hardware, and multi-region key replication for availability.
  None of that exists here. Rotation is not implemented either: a file whose stored
  master_key_version differs from the configured one cannot be decrypted.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ALGORITHM = "AES-256-GCM"
_NONCE_BYTES = 12
_KEY_BYTES = 32
_WRAP_AAD_PREFIX = b"cmm-dek-wrap:v"
_FILE_AAD_PREFIX = b"cmm-file:"


class DecryptionError(Exception):
    """Data could not be decrypted or failed authentication (wrong key, or modified bytes)."""


class KeyVersionError(DecryptionError):
    """The file was encrypted under a master-key version this process does not have."""


@dataclass(frozen=True)
class EncryptedPayload:
    ciphertext: bytes  # what is handed to the storage provider (includes the 16-byte GCM tag)
    wrapped_dek: bytes  # stored in file_encryption.wrapped_dek
    nonce: bytes  # stored in file_encryption.nonce
    master_key_version: int
    ciphertext_sha256: str  # stored in file_encryption.ciphertext_sha256


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CryptoService:
    def __init__(self, master_key: bytes, master_key_version: int = 1):
        if len(master_key) != _KEY_BYTES:
            raise ValueError("master key must be exactly 32 bytes")
        self._master = AESGCM(master_key)
        self.master_key_version = master_key_version

    def _wrap_aad(self, version: int) -> bytes:
        return _WRAP_AAD_PREFIX + str(version).encode()

    def encrypt(self, plaintext: bytes, object_key: str) -> EncryptedPayload:
        dek = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(dek).encrypt(nonce, plaintext, _FILE_AAD_PREFIX + object_key.encode())
        wrap_nonce = os.urandom(_NONCE_BYTES)
        wrapped = wrap_nonce + self._master.encrypt(
            wrap_nonce, dek, self._wrap_aad(self.master_key_version)
        )
        return EncryptedPayload(
            ciphertext=ciphertext,
            wrapped_dek=wrapped,
            nonce=nonce,
            master_key_version=self.master_key_version,
            ciphertext_sha256=sha256_hex(ciphertext),
        )

    def unwrap_dek(self, wrapped_dek: bytes, master_key_version: int) -> bytes:
        if master_key_version != self.master_key_version:
            raise KeyVersionError(
                f"file was encrypted under master key version {master_key_version}; "
                f"this process has version {self.master_key_version} (rotation is not implemented)"
            )
        try:
            return self._master.decrypt(
                wrapped_dek[:_NONCE_BYTES], wrapped_dek[_NONCE_BYTES:],
                self._wrap_aad(master_key_version),
            )  # fmt: skip
        except InvalidTag as exc:
            raise DecryptionError("data key could not be unwrapped (wrong master key?)") from exc

    def decrypt(
        self,
        ciphertext: bytes,
        wrapped_dek: bytes,
        nonce: bytes,
        master_key_version: int,
        object_key: str,
    ) -> bytes:
        dek = self.unwrap_dek(wrapped_dek, master_key_version)
        try:
            return AESGCM(dek).decrypt(nonce, ciphertext, _FILE_AAD_PREFIX + object_key.encode())
        except InvalidTag as exc:
            raise DecryptionError(
                "ciphertext failed authentication (modified or wrong key)"
            ) from exc
