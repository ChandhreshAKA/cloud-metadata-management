"""File endpoints: upload, download, delete (owner only; AUDITOR and ADMIN cannot read contents).

Upload takes the RAW file as the request body (Content-Type: application/octet-stream) and the
details as query parameters, so no multipart library is needed:

  Invoke-RestMethod -Method Post -Headers $auth -InFile report.pdf `
      -ContentType "application/octet-stream" `
      -Uri "http://127.0.0.1:8000/files?file_name=report.pdf&security_level=HIGH"

Storage behind these endpoints is SIMULATED (local folders). See app/storage/simulated.py.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.api.schemas_providers import Level, Tier
from app.auth.deps import (
    client_ip,
    get_crypto,
    get_database,
    get_settings,
    get_storage,
    require_permission,
)
from app.auth.rbac import Permission
from app.config import Settings
from app.crypto.service import CryptoService
from app.db.base import Database, UserRecord
from app.files.errors import FileTooLargeError
from app.files.service import FileService, UploadParams
from app.storage.base import StorageRegistry

router = APIRouter(prefix="/files", tags=["files (storage is SIMULATED)"])


def get_file_service(
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
    crypto: CryptoService = Depends(get_crypto),
    storage: StorageRegistry = Depends(get_storage),
) -> FileService:
    return FileService(database, settings, crypto, storage)


async def read_body_limited(request: Request, limit: int) -> bytes:
    """Read the body; refuse early (by header) or while streaming if it exceeds `limit`."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise FileTooLargeError(f"file is {declared} bytes; the limit is {limit} bytes")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise FileTooLargeError(f"file is larger than the limit of {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def upload(
    request: Request,
    file_name: Annotated[str, Query(min_length=1, max_length=255)],
    security_level: Level,
    expected_accesses_per_month: Annotated[Decimal, Query(ge=0, le=1_000_000)] = Decimal(1),
    retention_months: Annotated[Decimal, Query(gt=0, le=1200)] = Decimal(12),
    tier: Tier = "HOT",
    preset: str | None = None,
    user: UserRecord = Depends(require_permission(Permission.FILE_WRITE_OWN)),
    service: FileService = Depends(get_file_service),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Encrypt and store a file at the provider the selection engine chooses (SIMULATED storage)."""
    data = await read_body_limited(request, settings.max_upload_bytes)
    params = UploadParams(
        file_name, security_level, expected_accesses_per_month, retention_months, tier, preset
    )
    return service.upload(user, params, data, client_ip(request))


@router.get("")
def list_my_files(
    user: UserRecord = Depends(require_permission(Permission.FILE_READ_OWN)),
    service: FileService = Depends(get_file_service),
) -> dict[str, Any]:
    """Your own files (metadata only), newest first. Deleted files are not listed."""
    return service.list_files(user)


@router.get("/{file_id}/placement")
def placement(
    file_id: int,
    request: Request,
    user: UserRecord = Depends(require_permission(Permission.FILE_READ_OWN)),
    service: FileService = Depends(get_file_service),
) -> dict[str, Any]:
    """Why one of your files is stored where it is: the saved ranking and explanation."""
    return service.placement(user, file_id, client_ip(request))


@router.get("/{file_id}/download")
def download(
    file_id: int,
    request: Request,
    user: UserRecord = Depends(require_permission(Permission.FILE_READ_OWN)),
    service: FileService = Depends(get_file_service),
) -> Response:
    """Fetch, decrypt and integrity-check one of your files."""
    content, name = service.download(user, file_id, client_ip(request))
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


@router.delete("/{file_id}")
def delete(
    file_id: int,
    request: Request,
    confirm: bool = False,
    user: UserRecord = Depends(require_permission(Permission.FILE_DELETE_OWN)),
    service: FileService = Depends(get_file_service),
) -> dict[str, Any]:
    """Permanently delete one of your files. Requires `confirm=true`."""
    return service.delete(user, file_id, confirm, client_ip(request))
