"""Health check: is the process up, and can it reach the database?"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.schemas import HealthResponse
from app.auth.deps import get_database
from app.db.base import Database
from app.db.errors import DatabaseError

router = APIRouter(tags=["health"])
log = logging.getLogger(__name__)


@router.get("/health", response_model=HealthResponse)
def health(database: Database = Depends(get_database)):
    """200 if the database answers a trivial query, 503 if not. No error details are returned."""
    try:
        database.ping()
    except DatabaseError as exc:
        log.warning("health check: database unreachable: %s", (exc.detail.splitlines() or [""])[0])
        return JSONResponse(
            status_code=503, content=HealthResponse(status="degraded", database="down").model_dump()
        )
    return HealthResponse(status="ok", database="up")
