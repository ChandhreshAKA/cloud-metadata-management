"""Read-only reports: duplicate files and cold-file candidates.

A USER sees only their own files. ADMIN and AUDITOR see every owner's files (metadata only; they
still cannot read file contents). Both endpoints are GET and never change anything: the cold-file
list is "candidate for archival review", not an instruction to move or delete.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.auth.deps import get_database, report_scope
from app.db.base import Database
from app.reports.service import MAX_DAYS, ReportScope, ReportService

router = APIRouter(prefix="/reports", tags=["reports (read-only)"])


def get_report_service(database: Database = Depends(get_database)) -> ReportService:
    return ReportService(database)


@router.get("/duplicates")
def duplicate_files(
    scope: ReportScope = Depends(report_scope),
    service: ReportService = Depends(get_report_service),
) -> dict[str, Any]:
    """Groups of identical files (same SHA-256), copies, wasted bytes, estimated monthly waste."""
    return service.duplicates(scope)


@router.get("/cold-files")
def cold_files(
    idle_days: Annotated[
        int | None,
        Query(ge=0, le=MAX_DAYS, description="Cold only if idle MORE than this many days"),
    ] = None,
    max_accesses: Annotated[
        int | None,
        Query(ge=1, le=1_000_000, description="... and FEWER than this many downloads"),
    ] = None,
    window_days: Annotated[
        int | None,
        Query(ge=1, le=MAX_DAYS, description="Lookback window for counting downloads"),
    ] = None,
    scope: ReportScope = Depends(report_scope),
    service: ReportService = Depends(get_report_service),
) -> dict[str, Any]:
    """Cold-file candidates. Omitted thresholds come from system_config, then built-in defaults."""
    return service.cold_files(scope, idle_days, max_accesses, window_days)
