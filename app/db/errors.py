"""Domain errors for database failures, and the translation from raw Oracle errors.

The translation reads the text of the error ("ORA-00001: unique constraint (SCHEMA.NAME) ...").
That text format was copied from real Oracle 26ai Free output
(see sql/demo_constraint_failures.log),
but it is still parsed with regular expressions: if Oracle changes the wording, the mapping falls
back to a generic DatabaseError instead of guessing.
"""

from __future__ import annotations

import re


class DatabaseError(Exception):
    """Generic database failure. `detail` is for server logs only, never for API responses."""

    def __init__(self, detail: str = ""):
        super().__init__(detail)
        self.detail = detail


class DatabaseUnavailableError(DatabaseError):
    """The database could not be reached (listener down, bad DSN, pool exhausted)."""


class ConstraintViolationError(DatabaseError):
    def __init__(self, detail: str = "", constraint: str | None = None):
        super().__init__(detail)
        self.constraint = constraint


class UniqueViolationError(ConstraintViolationError):  # ORA-00001
    pass


class CheckViolationError(ConstraintViolationError):  # ORA-02290
    pass


class NotNullViolationError(ConstraintViolationError):  # ORA-01400, ORA-01407
    pass


class ReferenceViolationError(ConstraintViolationError):  # ORA-02291, ORA-02292
    pass


class ValueTooLargeError(ConstraintViolationError):  # ORA-12899, ORA-01438
    pass


class DuplicateEmailError(Exception):
    """Raised by the auth service when the e-mail is already registered."""


_ORA_CODE = re.compile(r"ORA-(\d{5})")
_CONSTRAINT = re.compile(r"constraint \((?:[A-Za-z0-9_$#]+\.)?([A-Za-z0-9_$#]+)\)")
_NOT_NULL_COLUMN = re.compile(
    r'cannot (?:insert|update) NULL (?:into|to) \("[^"]+"\."[^"]+"\."([^"]+)"\)'
)
_TOO_LARGE_COLUMN = re.compile(r'for column "[^"]+"\."[^"]+"\."([^"]+)"')

# ORA codes meaning "cannot reach the database" (listener, service name, credentials, network)
_UNAVAILABLE_ORA = {"12541", "12514", "12170", "12154", "12537", "12547", "03113", "03114", "01017"}
# python-oracledb's own connection errors
_UNAVAILABLE_DPY = ("DPY-6005", "DPY-6001", "DPY-4011", "DPY-4026", "DPY-6000")


def ora_code(exc: BaseException) -> str | None:
    match = _ORA_CODE.search(str(exc))
    return match.group(1) if match else None


def map_db_error(exc: BaseException) -> DatabaseError:
    """Turn a raw driver exception into one of the domain errors above."""
    text = str(exc)
    code = ora_code(exc)
    constraint_match = _CONSTRAINT.search(text)
    constraint = constraint_match.group(1) if constraint_match else None

    if code == "00001":
        return UniqueViolationError(text, constraint)
    if code == "02290":
        return CheckViolationError(text, constraint)
    if code in ("01400", "01407"):
        column = _NOT_NULL_COLUMN.search(text)
        return NotNullViolationError(text, column.group(1) if column else None)
    if code in ("02291", "02292"):
        return ReferenceViolationError(text, constraint)
    if code in ("12899", "01438"):
        column = _TOO_LARGE_COLUMN.search(text)
        return ValueTooLargeError(text, column.group(1) if column else None)
    if code in _UNAVAILABLE_ORA or any(marker in text for marker in _UNAVAILABLE_DPY):
        return DatabaseUnavailableError(text)
    return DatabaseError(text)


# Friendly, non-leaking messages for constraints a client can plausibly trigger.
# Anything not listed here gets a generic message (the constraint name stays in the server log).
CLIENT_MESSAGES: dict[str, str] = {
    "UQ_USERS_EMAIL": "That e-mail address is already registered",
    "CK_USERS_EMAIL_LOWER": "E-mail must be lower case",
    "CK_USERS_EMAIL_FORMAT": "E-mail address is not valid",
    "CK_USERS_ROLE": "Role is not valid",
    "FK_FILES_OWNER": "Referenced user does not exist",
    "UQ_PROVIDERS_CODE": "A provider with that code already exists",
    "UQ_PROVIDERS_NAME": "A provider with that name already exists",
    "UQ_REGIONS_PROVIDER_CODE": "That provider already has a region with that code",
    "UQ_REGIONS_PROVIDER_BUCKET": "That provider already uses that bucket name",
    "FK_PRICING_TIER": "Unknown storage tier",
    "UQ_PRICING_NATIVE": "That region already has a price row using that class name",
    "CK_REGIONS_RELIABILITY": "Reliability score must be between 0 and 10",
    "CK_REGIONS_SECURITY": "Security score must be between 0 and 10",
    "CK_PRICING_NONNEG": "Prices must not be negative",
}
