"""Audit event helper.

Audit rows are written in their OWN transaction so that they survive even when the request
that caused them fails or is rolled back (a failed login or a denied request must still leave
a record). Audit writes are not swallowed: if the audit trail cannot be written, the request
fails rather than proceeding unrecorded.
"""

from __future__ import annotations

from typing import Any

from app.db.base import Database

# Event types accepted by audit_events.ck_audit_type (sql/schema.sql)
USER_REGISTERED = "USER_REGISTERED"
LOGIN_OK = "LOGIN_OK"
LOGIN_FAILED = "LOGIN_FAILED"
ACCESS_DENIED = "ACCESS_DENIED"
CONFIG_CHANGED = "CONFIG_CHANGED"
PROVIDER_STATUS_CHANGED = "PROVIDER_STATUS_CHANGED"
FILE_UPLOADED = "FILE_UPLOADED"
FILE_DELETED = "FILE_DELETED"

SUCCESS = "SUCCESS"
FAILURE = "FAILURE"
DENIED = "DENIED"


def record_event(
    database: Database,
    event_type: str,
    outcome: str,
    *,
    actor_user_id: int | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    ip_address: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    with database.transaction() as session:
        session.audit.record(
            event_type,
            outcome,
            actor_user_id=actor_user_id,
            target_type=target_type,
            target_id=target_id,
            ip_address=ip_address,
            details=details,
        )
