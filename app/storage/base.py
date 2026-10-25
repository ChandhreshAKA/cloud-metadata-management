"""The storage interface every provider adapter implements.

Adapters only ever receive and return CIPHERTEXT: encryption happens before `put` and decryption
after `get`, in the file service. There is exactly one adapter in this project, and it is
SIMULATED (app/storage/simulated.py). No real cloud adapter exists.
"""

from __future__ import annotations

from typing import Protocol


class StorageError(Exception):
    """The storage provider could not complete the operation (unavailable, disk error, bad key)."""


class ObjectNotFoundError(StorageError):
    """The requested object is not in storage."""


class StorageProvider(Protocol):
    def put(self, object_key: str, data: bytes) -> None:
        """Store `data` under `object_key`. Raises StorageError; never overwrites a key."""
        ...

    def get(self, object_key: str) -> bytes:
        """Return the stored bytes. Raises ObjectNotFoundError if absent, StorageError otherwise."""
        ...

    def delete(self, object_key: str) -> bool:
        """Remove the object. Returns True if something was removed, False if it was not there."""
        ...

    def exists(self, object_key: str) -> bool: ...


class StorageRegistry(Protocol):
    def for_region(self, provider_code: str, region_code: str) -> StorageProvider:
        """The adapter for one provider-region (what `selection` chose)."""
        ...
