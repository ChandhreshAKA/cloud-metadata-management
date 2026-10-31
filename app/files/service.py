"""Module 7: upload, download and delete of encrypted files.

UPLOAD, in this order (authentication and authorisation happen before this code runs, in the
router's `require_permission(FILE_WRITE_OWN)` dependency):
  1. validate the name and the size (empty and over-limit files are refused);
  2. SHA-256 of the plaintext;
  3. run the selection engine (UNCHANGED, called through app.selection) to choose a provider-region;
  4. encrypt with AES-256-GCM under a fresh data key (app.crypto.service);
  5. store the ciphertext with the SIMULATED storage adapter;
  6. write ALL metadata (file, encryption record, location, the selection decision and its
     candidates, an access-log row and an audit event) in ONE database transaction.
  Failure rules: if step 5 fails nothing was written to the database (no metadata exists yet).
  If step 6 fails the database rolls back and the stored object is DELETED again (compensating
  action). If that delete itself fails the object is orphaned: it is logged with its key, and no
  automatic sweep exists yet.

DOWNLOAD: owner check, fetch ciphertext, verify its SHA-256, decrypt (GCM authenticates), verify
the plaintext SHA-256, and write an access-log row with the measured latency and the outcome.

DELETE: needs an explicit confirm flag. Order: in one transaction mark the file DELETED, remove
the encryption record (crypto-erase: without the wrapped key the stored bytes can never be
decrypted again), mark the location PENDING_DELETE, log and audit; then remove the stored object.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.audit import service as audit
from app.config import Settings
from app.crypto.service import ALGORITHM, CryptoService, DecryptionError, sha256_hex
from app.db.base import Database, UserRecord
from app.files.errors import (
    ConfirmationRequiredError,
    FileDeletedError,
    FileTooLargeError,
    IntegrityFailureError,
    InvalidFileError,
    NoEligibleProviderError,
    NotOwnerError,
    ObjectMissingError,
    StorageUnavailableError,
    StoredFileNotFoundError,
)
from app.files.models import StoredFile
from app.selection import engine, serialize
from app.selection import service as selection_service
from app.selection.models import METRICS, FileRequest
from app.storage.base import ObjectNotFoundError, StorageError, StorageRegistry

log = logging.getLogger(__name__)

_ALLOWED_TYPES = {
    "PDF", "DOCX", "PPTX", "XLSX", "CSV", "TXT", "JSON", "JPG", "PNG", "ZIP", "MP4",
}  # fmt: skip
_TYPE_ALIASES = {"JPEG": "JPG"}
_MAX_NAME = 255


@dataclass(frozen=True)
class UploadParams:
    file_name: str
    security_level: str
    expected_accesses_per_month: Decimal = Decimal(1)
    retention_months: Decimal = Decimal(12)
    tier: str = "HOT"
    preset: str | None = None


def validate_file_name(name: str) -> str:
    if not name or not name.strip():
        raise InvalidFileError("file name must not be empty")
    if len(name.encode("utf-8")) > _MAX_NAME:
        # files.file_name is VARCHAR2(255) and Oracle counts BYTES, so check bytes (accents take 2)
        raise InvalidFileError(f"file name is longer than {_MAX_NAME} bytes when encoded as UTF-8")
    if "/" in name or "\\" in name:
        raise InvalidFileError("file name must not contain / or \\")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise InvalidFileError("file name must not contain control characters")
    if name in {".", ".."}:
        raise InvalidFileError("file name is not valid")
    return name


def file_type_for(name: str) -> str:
    extension = name.rsplit(".", 1)[-1].upper() if "." in name else ""
    extension = _TYPE_ALIASES.get(extension, extension)
    return extension if extension in _ALLOWED_TYPES else "OTHER"


def _ms(seconds: float) -> Decimal:
    return Decimal(str(seconds * 1000)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class FileService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        crypto: CryptoService,
        storage: StorageRegistry,
    ):
        self.db = database
        self.settings = settings
        self.crypto = crypto
        self.storage = storage

    # ------------------------------------------------------------------------- read-only views
    def list_files(self, user: UserRecord) -> dict[str, Any]:
        """The caller's own files (metadata only), newest first. Deleted files are not listed."""
        with self.db.transaction() as session:
            files = session.files.list_for_owner(user.user_id)
        return {"count": len(files), "storage": "SIMULATED local folders", "files": files}

    def placement(self, user: UserRecord, file_id: int, ip: str | None = None) -> dict[str, Any]:
        """Why this file is where it is: the stored selection decision, owner only."""
        stored = self._load_owned(user, file_id, "VIEW_METADATA", ip)
        with self.db.transaction() as session:
            decision = session.files.get_decision(file_id)
        if decision is None:
            raise StoredFileNotFoundError(f"file {file_id} has no stored placement decision")
        chosen = decision["chosen_region_id"]
        ranked = [c for c in decision["candidates"] if c["excluded_reason"] is None]
        rejected = [c for c in decision["candidates"] if c["excluded_reason"] is not None]
        return {
            "file_id": file_id,
            "file_name": stored.file_name,
            "storage": "SIMULATED local folders",
            "preset": decision["preset"],
            "weights": decision["weights"],
            "decided_at": decision["decided_at"],
            "explanation": decision["explanation"],
            "selected": next(
                (
                    {"provider_code": c["provider_code"], "region_code": c["region_code"],
                     "composite": c["composite"]}
                    for c in ranked if c["region_id"] == chosen
                ),
                None,
            ),  # fmt: skip
            "ranked": [
                {"rank": i, "provider_code": c["provider_code"], "region_code": c["region_code"],
                 "composite": c["composite"], "raw": c["raw"]}
                for i, c in enumerate(ranked, start=1)
            ],  # fmt: skip
            "rejected": [
                {"provider_code": c["provider_code"], "region_code": c["region_code"],
                 "reason": c["excluded_reason"]}
                for c in rejected
            ],  # fmt: skip
        }

    # ------------------------------------------------------------------------- upload
    def upload(
        self, user: UserRecord, params: UploadParams, data: bytes, ip: str | None = None
    ) -> dict[str, Any]:
        name = validate_file_name(params.file_name)
        size = len(data)
        if size == 0:
            raise InvalidFileError("file is empty")
        if size > self.settings.max_upload_bytes:
            raise FileTooLargeError(
                f"file is {size} bytes; the limit is {self.settings.max_upload_bytes} bytes"
            )
        plaintext_sha = sha256_hex(data)

        request = FileRequest(
            file_size_bytes=size,
            security_level=params.security_level,
            expected_accesses_per_month=params.expected_accesses_per_month,
            retention_months=params.retention_months,
            tier=params.tier,
        )
        inputs = selection_service.load_inputs(self.db, request)
        preset, weights, note = selection_service.pick_weights(inputs, params.preset, None)
        result = engine.select(
            request,
            inputs.candidates,
            weights,
            inputs.policy,
            weights_note=note,
            preset_name=preset,
        )
        if result.selected is None:
            raise NoEligibleProviderError(
                "no provider-region can store this file",
                serialize.result_dict(result, preset)["rejected"],
            )
        chosen = result.selected.candidate

        object_key = f"u{user.user_id}/{uuid.uuid4().hex}"
        payload = self.crypto.encrypt(data, object_key)
        provider_storage = self.storage.for_region(chosen.provider_code, chosen.region_code)

        started = time.perf_counter()
        try:
            provider_storage.put(object_key, payload.ciphertext)
        except StorageError as exc:
            log.error(
                "storage put failed for %s/%s: %s", chosen.provider_code, chosen.region_code, exc
            )
            raise StorageUnavailableError("the storage provider could not store the file") from exc
        put_ms = _ms(time.perf_counter() - started)

        try:
            with self.db.transaction() as session:
                files = session.files
                file_id = files.insert_file(
                    user.user_id, name, size, file_type_for(name), params.security_level,
                    plaintext_sha,
                    int(params.expected_accesses_per_month.to_integral_value(ROUND_HALF_UP)),
                )  # fmt: skip
                files.insert_encryption(
                    file_id, ALGORITHM, payload.wrapped_dek, payload.nonce,
                    payload.master_key_version, payload.ciphertext_sha256,
                )  # fmt: skip
                files.insert_location(file_id, chosen.region_id, request.tier, object_key)
                self._store_decision(session, file_id, result, preset)
                files.insert_access_log(
                    file_id, user.user_id, "UPLOAD", "SUCCESS", put_ms, chosen.region_id
                )
                session.audit.record(
                    audit.FILE_UPLOADED, audit.SUCCESS, actor_user_id=user.user_id,
                    target_type="FILE", target_id=file_id, ip_address=ip,
                    details={"size_bytes": size, "provider": chosen.provider_code,
                             "region": chosen.region_code, "tier": request.tier},
                )  # fmt: skip
        except Exception:
            self._remove_orphan(
                provider_storage, object_key, chosen.provider_code, chosen.region_code
            )
            raise

        return {
            "file_id": file_id,
            "file_name": name,
            "file_size_bytes": size,
            "file_type": file_type_for(name),
            "security_level": params.security_level,
            "content_sha256": plaintext_sha,
            "provider": chosen.provider_name,
            "provider_code": chosen.provider_code,
            "region": chosen.region_code,
            "tier": request.tier,
            "object_key": object_key,
            "storage": (
                f"SIMULATED local folder: {chosen.provider_code}/{chosen.region_code}/{object_key}"
            ),
            "encryption": {
                "algorithm": ALGORITHM,
                "master_key_version": payload.master_key_version,
            },
            "ciphertext_sha256": payload.ciphertext_sha256,
            "put_latency_ms": str(put_ms),
            "selection": {
                "preset": preset,
                "composite": serialize.dec(result.selected.composite),
                "explanation": result.explanation,
                "warnings": list(result.warnings),
            },
        }

    @staticmethod
    def _store_decision(session: Any, file_id: int, result: Any, preset: str) -> None:
        decision_id = session.files.insert_decision(
            file_id, preset, serialize.weights_dict(result.weights),
            result.selected.candidate.region_id, result.explanation,
        )  # fmt: skip
        for item in result.ranked:
            raw = {m: serialize.dec(item.raw[m]) for m in METRICS}
            raw["total_cost"] = serialize.dec(item.cost.total)
            session.files.insert_candidate(
                decision_id, item.candidate.region_id, None, raw, item.normalised, item.composite
            )
        for rejection in result.rejected:
            session.files.insert_candidate(
                decision_id, rejection.candidate.region_id, "; ".join(rejection.reasons),
                None, None, None,
            )  # fmt: skip

    @staticmethod
    def _remove_orphan(storage: Any, object_key: str, provider: str, region: str) -> None:
        """Compensating action: the database write failed, so the stored object must go."""
        try:
            storage.delete(object_key)
            log.warning("database write failed; removed stored object %s", object_key)
        except Exception:
            log.error(
                "ORPHANED OBJECT: database write failed AND deleting %s/%s/%s failed; "
                "remove it manually (no automatic sweep exists)", provider, region, object_key,
                exc_info=True,
            )  # fmt: skip

    # ----------------------------------------------------------------------- shared helpers
    def _load_owned(
        self, user: UserRecord, file_id: int, action: str, ip: str | None
    ) -> StoredFile:
        with self.db.transaction() as session:
            stored = session.files.get_for_access(file_id)
        if stored is None:
            raise StoredFileNotFoundError(f"file {file_id} not found")
        if stored.owner_id != user.user_id:
            self._deny(user, stored, action, ip)
        if stored.status == "DELETED":
            raise FileDeletedError(f"file {file_id} has been deleted")
        return stored

    def _deny(self, user: UserRecord, stored: StoredFile, action: str, ip: str | None) -> None:
        with self.db.transaction() as session:
            session.files.insert_access_log(
                stored.file_id, user.user_id, action, "DENIED", None, None
            )
            session.audit.record(
                audit.ACCESS_DENIED, audit.DENIED, actor_user_id=user.user_id,
                target_type="FILE", target_id=stored.file_id, ip_address=ip,
                details={"reason": "not_owner", "action": action},
            )  # fmt: skip
        raise NotOwnerError("you do not have access to this file")

    def _log(
        self,
        stored: StoredFile,
        user: UserRecord,
        action: str,
        outcome: str,
        latency: Decimal | None,
    ) -> None:
        with self.db.transaction() as session:
            session.files.insert_access_log(
                stored.file_id, user.user_id, action, outcome, latency, stored.region_id
            )

    # ----------------------------------------------------------------------------- download
    def download(self, user: UserRecord, file_id: int, ip: str | None = None) -> tuple[bytes, str]:
        stored = self._load_owned(user, file_id, "DOWNLOAD", ip)
        if not (stored.object_key and stored.provider_code and stored.region_code):
            self._log(stored, user, "DOWNLOAD", "FAILURE", None)
            raise ObjectMissingError("this file has no stored object")
        if stored.wrapped_dek is None or stored.nonce is None or stored.master_key_version is None:
            self._log(stored, user, "DOWNLOAD", "FAILURE", None)
            raise ObjectMissingError("this file has no encryption record, so it cannot be read")

        provider_storage = self.storage.for_region(stored.provider_code, stored.region_code)
        started = time.perf_counter()
        try:
            ciphertext = provider_storage.get(stored.object_key)
        except ObjectNotFoundError as exc:
            self._log(stored, user, "DOWNLOAD", "FAILURE", _ms(time.perf_counter() - started))
            raise ObjectMissingError(
                "the stored object is missing from the storage provider"
            ) from exc
        except StorageError as exc:
            self._log(stored, user, "DOWNLOAD", "FAILURE", _ms(time.perf_counter() - started))
            raise StorageUnavailableError("the storage provider could not return the file") from exc
        latency = _ms(time.perf_counter() - started)

        if sha256_hex(ciphertext) != stored.ciphertext_sha256:
            self._log(stored, user, "DOWNLOAD", "FAILURE", latency)
            log.error("ciphertext hash mismatch for file %s", file_id)
            raise IntegrityFailureError(
                "integrity check failed: the stored object was modified (ciphertext hash mismatch)"
            )
        try:
            plaintext = self.crypto.decrypt(
                ciphertext, stored.wrapped_dek, stored.nonce, stored.master_key_version,
                stored.object_key,
            )  # fmt: skip
        except DecryptionError as exc:
            self._log(stored, user, "DOWNLOAD", "FAILURE", latency)
            log.error("decryption failed for file %s: %s", file_id, exc)
            raise IntegrityFailureError(
                "integrity check failed: the file could not be decrypted"
            ) from exc
        if stored.content_sha256 is None or sha256_hex(plaintext) != stored.content_sha256:
            self._log(stored, user, "DOWNLOAD", "FAILURE", latency)
            log.error("plaintext hash mismatch for file %s", file_id)
            raise IntegrityFailureError(
                "integrity check failed: the decrypted content does not match its recorded SHA-256"
            )
        self._log(stored, user, "DOWNLOAD", "SUCCESS", latency)
        return plaintext, stored.file_name

    # ------------------------------------------------------------------------------ delete
    def delete(
        self, user: UserRecord, file_id: int, confirm: bool, ip: str | None = None
    ) -> dict[str, Any]:
        if not confirm:
            raise ConfirmationRequiredError("deleting is permanent: send confirm=true to proceed")
        stored = self._load_owned(user, file_id, "DELETE", ip)

        with self.db.transaction() as session:
            if not session.files.mark_deleted(file_id):
                raise FileDeletedError(f"file {file_id} has been deleted")
            session.files.delete_encryption(file_id)  # crypto-erase: the key is gone for good
            if stored.location_id is not None:
                session.files.set_location_status(stored.location_id, "PENDING_DELETE")
            session.files.insert_access_log(
                file_id, user.user_id, "DELETE", "SUCCESS", None, stored.region_id
            )
            session.audit.record(
                audit.FILE_DELETED, audit.SUCCESS, actor_user_id=user.user_id,
                target_type="FILE", target_id=file_id, ip_address=ip,
                details={"crypto_erased": stored.wrapped_dek is not None},
            )  # fmt: skip

        object_removed = True
        if stored.object_key and stored.provider_code and stored.region_code:
            try:
                self.storage.for_region(stored.provider_code, stored.region_code).delete(
                    stored.object_key
                )
            except StorageError:
                object_removed = False
                log.error(
                    "could not remove object %s after delete; left PENDING_DELETE",
                    stored.object_key, exc_info=True,
                )  # fmt: skip
        if object_removed and stored.location_id is not None:
            with self.db.transaction() as session:
                session.files.delete_location(stored.location_id)
        return {
            "file_id": file_id,
            "status": "DELETED",
            "crypto_erased": stored.wrapped_dek is not None,
            "object_removed": object_removed,
        }
