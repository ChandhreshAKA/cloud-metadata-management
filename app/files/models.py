"""Row types for the file service."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StoredFile:
    """A file with its primary location and encryption record, as needed to serve or delete it."""

    file_id: int
    owner_id: int
    file_name: str
    file_size_bytes: int
    file_type: str
    security_level: str
    status: str  # ACTIVE, DEGRADED, DELETED
    content_sha256: str | None
    location_id: int | None
    region_id: int | None
    provider_code: str | None
    region_code: str | None
    object_key: str | None
    tier_code: str | None
    wrapped_dek: bytes | None  # None once the file is crypto-erased (or never encrypted: legacy)
    nonce: bytes | None
    master_key_version: int | None
    ciphertext_sha256: str | None
