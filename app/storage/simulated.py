"""SIMULATED storage provider: writes objects to local folders. THIS IS NOT A CLOUD.

Layout:  <root>/<PROVIDER_CODE>/<region>/<object_key>
         e.g. storage_sim/AWS/ap-south-1/u3/9f1c...   (folders are named after the provider)

Nothing here talks to AWS, Azure or Google Cloud. It exists so the upload, download, encryption
and selection logic can be exercised end to end on one machine. A real adapter would implement
the same four methods using the provider's SDK (for example boto3 for S3).

Safety: object keys are validated so a key can never point outside the provider folder.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from app.storage.base import ObjectNotFoundError, StorageError

SIMULATED = True  # every adapter and every response that mentions storage says so

_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,298}$")
_FOLDER_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _folder(name: str) -> str:
    cleaned = _FOLDER_UNSAFE.sub("_", name).strip(".")
    if not cleaned:
        raise StorageError("provider or region name cannot be used as a folder name")
    return cleaned


class SimulatedStorage:
    """One provider-region's folder. Implements app.storage.base.StorageProvider."""

    def __init__(self, root: Path, provider_code: str, region_code: str):
        self.base = (Path(root) / _folder(provider_code) / _folder(region_code)).resolve()

    def _path(self, object_key: str) -> Path:
        if not _KEY.fullmatch(object_key) or ".." in object_key.split("/") or "//" in object_key:
            raise StorageError("invalid object key")
        path = (self.base / object_key).resolve()
        if self.base not in path.parents:
            raise StorageError("object key escapes the provider folder")
        return path

    def put(self, object_key: str, data: bytes) -> None:
        path = self._path(object_key)
        if path.exists():
            raise StorageError("object already exists")
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_bytes(data)
            os.replace(temp, path)  # atomic: readers never see a half-written object
        except OSError as exc:
            temp.unlink(missing_ok=True)
            raise StorageError(f"could not write object: {exc.strerror or exc}") from exc

    def get(self, object_key: str) -> bytes:
        path = self._path(object_key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise ObjectNotFoundError("object not found") from exc
        except OSError as exc:
            raise StorageError(f"could not read object: {exc.strerror or exc}") from exc

    def delete(self, object_key: str) -> bool:
        path = self._path(object_key)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise StorageError(f"could not delete object: {exc.strerror or exc}") from exc
        return True

    def exists(self, object_key: str) -> bool:
        return self._path(object_key).is_file()


class SimulatedStorageRegistry:
    """Hands out the SimulatedStorage for each provider-region under one root folder."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def for_region(self, provider_code: str, region_code: str) -> SimulatedStorage:
        return SimulatedStorage(self.root, provider_code, region_code)
