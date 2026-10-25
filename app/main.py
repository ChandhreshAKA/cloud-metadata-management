"""FastAPI application factory.

Run:  python -m uvicorn app.main:create_app --factory --port 8000
Tests call create_app(database=..., settings=...) with a fake database.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routers import auth, files, health, protected, providers, selection
from app.config import Settings, load_settings
from app.crypto.service import CryptoService
from app.db.base import Database
from app.db.errors import (
    CLIENT_MESSAGES,
    CheckViolationError,
    DatabaseError,
    DatabaseUnavailableError,
    DuplicateEmailError,
    NotNullViolationError,
    ReferenceViolationError,
    UniqueViolationError,
    ValueTooLargeError,
)
from app.db.pool import OracleDatabase
from app.files import errors as file_errors
from app.providers.service import NotFoundError
from app.selection.presets import WeightError
from app.selection.service import InvalidSelectionRequest, UnknownPresetError
from app.storage.base import StorageRegistry
from app.storage.simulated import SimulatedStorageRegistry

log = logging.getLogger(__name__)


def _json(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": message})


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default response echoes the submitted value (which could be a password).
        errors = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422, content={"detail": "Invalid request", "errors": errors}
        )

    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return _json(404, str(exc))

    @app.exception_handler(WeightError)
    @app.exception_handler(UnknownPresetError)
    @app.exception_handler(InvalidSelectionRequest)
    async def bad_selection_input(_: Request, exc: Exception) -> JSONResponse:
        return _json(422, str(exc))

    file_error_status = {
        file_errors.InvalidFileError: 422,
        file_errors.FileTooLargeError: 413,
        file_errors.StorageUnavailableError: 503,
        file_errors.StoredFileNotFoundError: 404,
        file_errors.FileDeletedError: 410,
        file_errors.NotOwnerError: 403,
        file_errors.ObjectMissingError: 404,
        file_errors.IntegrityFailureError: 502,
        file_errors.ConfirmationRequiredError: 400,
    }

    @app.exception_handler(file_errors.FileServiceError)
    async def file_error(_: Request, exc: file_errors.FileServiceError) -> JSONResponse:
        if isinstance(exc, file_errors.NoEligibleProviderError):
            return JSONResponse(
                status_code=422, content={"detail": str(exc), "rejected": exc.rejected}
            )
        return _json(file_error_status.get(type(exc), 400), str(exc))

    @app.exception_handler(DuplicateEmailError)
    async def duplicate_email(_: Request, __: DuplicateEmailError) -> JSONResponse:
        return _json(409, CLIENT_MESSAGES["UQ_USERS_EMAIL"])

    @app.exception_handler(UniqueViolationError)
    async def unique_violation(_: Request, exc: UniqueViolationError) -> JSONResponse:
        log.info("unique violation: %s", exc.constraint)
        return _json(409, CLIENT_MESSAGES.get(exc.constraint or "", "That record already exists"))

    @app.exception_handler(ReferenceViolationError)
    async def reference_violation(_: Request, exc: ReferenceViolationError) -> JSONResponse:
        log.info("reference violation: %s", exc.constraint)
        return _json(409, CLIENT_MESSAGES.get(exc.constraint or "", "Related record conflict"))

    @app.exception_handler(CheckViolationError)
    @app.exception_handler(NotNullViolationError)
    @app.exception_handler(ValueTooLargeError)
    async def invalid_value(_: Request, exc: Exception) -> JSONResponse:
        constraint = getattr(exc, "constraint", None)
        log.info("value rejected by database: %s", constraint)
        return _json(422, CLIENT_MESSAGES.get(constraint or "", "A submitted value is not valid"))

    @app.exception_handler(DatabaseUnavailableError)
    async def unavailable(_: Request, exc: DatabaseUnavailableError) -> JSONResponse:
        log.error("database unavailable: %s", exc.detail)
        return _json(503, "Database unavailable, try again later")

    @app.exception_handler(DatabaseError)
    async def database_error(_: Request, exc: DatabaseError) -> JSONResponse:
        log.error("unexpected database error: %s", exc.detail)
        return _json(500, "Internal database error")


def create_app(
    database: Database | None = None,
    settings: Settings | None = None,
    storage: StorageRegistry | None = None,
) -> FastAPI:
    """Build the app. With no arguments it reads the environment (and .env) and opens the pool."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owns_database = database is None
        if settings is None or database is None:
            load_dotenv()
        app.state.settings = settings or load_settings()
        app.state.crypto = CryptoService(
            app.state.settings.master_key, app.state.settings.master_key_version
        )
        app.state.storage = storage or SimulatedStorageRegistry(app.state.settings.storage_root)
        app.state.database = database or OracleDatabase.from_settings(app.state.settings)
        try:
            yield
        finally:
            if owns_database:
                app.state.database.close()

    app = FastAPI(
        title="Multi-Cloud Metadata Manager",
        version="0.4.0",
        description="Chunks 2-4: authentication, RBAC, audit, provider registry, selection "
        "and encrypted upload/download to SIMULATED storage. Monitoring arrives later.",
        lifespan=lifespan,
    )
    _register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(protected.router)
    app.include_router(providers.router)
    app.include_router(selection.router)
    app.include_router(files.router)
    return app
