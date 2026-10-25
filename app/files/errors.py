"""Errors raised by the file service. app.main maps each to a clean HTTP response."""

from __future__ import annotations


class FileServiceError(Exception):
    """Base class; the message is safe to show to the client."""


class InvalidFileError(FileServiceError):  # 422
    pass


class FileTooLargeError(FileServiceError):  # 413
    pass


class NoEligibleProviderError(FileServiceError):  # 422, with the rejection reasons
    def __init__(self, message: str, rejected: list[dict]):
        super().__init__(message)
        self.rejected = rejected


class StorageUnavailableError(FileServiceError):  # 503
    pass


class StoredFileNotFoundError(FileServiceError):  # 404
    pass


class FileDeletedError(FileServiceError):  # 410
    pass


class NotOwnerError(FileServiceError):  # 403
    pass


class ObjectMissingError(FileServiceError):  # 404: metadata exists but the stored object does not
    pass


class IntegrityFailureError(FileServiceError):  # 502: stored data failed verification
    pass


class ConfirmationRequiredError(FileServiceError):  # 400
    pass
