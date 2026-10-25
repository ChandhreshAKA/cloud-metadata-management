"""SQL for audit_events (append-only; a trigger blocks UPDATE and DELETE)."""

from __future__ import annotations

import json
from typing import Any

_MAX_DETAILS_CHARS = 2000  # audit_events.details_json is VARCHAR2(2000)

_INSERT_EVENT = """
    INSERT INTO audit_events
        (event_type, outcome, actor_user_id, target_type, target_id, ip_address, details_json)
    VALUES
        (:event_type, :outcome, :actor_user_id, :target_type, :target_id,
         :ip_address, :details_json)
"""


def encode_details(details: dict[str, Any] | None) -> str | None:
    """JSON-encode details; if too long for the column, keep a valid JSON marker instead."""
    if not details:
        return None
    text = json.dumps(details, default=str, ensure_ascii=True)
    if len(text) > _MAX_DETAILS_CHARS:
        return json.dumps({"truncated": True})
    return text


class OracleAuditRepository:
    def __init__(self, connection: Any):
        self._conn = connection

    def record(
        self,
        event_type: str,
        outcome: str,
        *,
        actor_user_id: int | None = None,
        target_type: str | None = None,
        target_id: int | None = None,
        ip_address: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            _INSERT_EVENT,
            {
                "event_type": event_type,
                "outcome": outcome,
                "actor_user_id": actor_user_id,
                "target_type": target_type,
                "target_id": target_id,
                "ip_address": ip_address,
                "details_json": encode_details(details),
            },
        )
