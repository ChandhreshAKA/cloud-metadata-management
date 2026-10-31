"""Oracle connection pool and the transaction helper.

oracledb runs in *thin mode* (pure Python): no Oracle Instant Client is required.
All SQL in this project uses bind variables; values are never formatted into SQL text.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import oracledb
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.db.errors import DatabaseError, DatabaseUnavailableError, map_db_error
from app.db.repositories.audit import OracleAuditRepository
from app.db.repositories.files import OracleFileRepository
from app.db.repositories.providers import OracleProviderRepository
from app.db.repositories.reports import OracleReportRepository
from app.db.repositories.users import SqlAlchemyUserRepository

log = logging.getLogger(__name__)

# Return NUMBER columns with a scale as Decimal instead of float: money and scores must be exact.
oracledb.defaults.fetch_decimals = True


class OracleSession:
    """The repositories bound to one connection (one transaction).

    `connection` is the raw oracledb connection used by the SQL repositories. `orm` is a
    SQLAlchemy Session that runs on that SAME connection, so ORM and raw SQL work in one
    transaction and are committed or rolled back together.
    """

    def __init__(self, connection: oracledb.Connection, orm: Session):
        self.connection = connection
        self.orm = orm
        self.users = SqlAlchemyUserRepository(orm)
        self.audit = OracleAuditRepository(connection)
        self.providers = OracleProviderRepository(connection)
        self.files = OracleFileRepository(connection)
        self.reports = OracleReportRepository(connection)


def _translate(exc: DBAPIError) -> DatabaseError:
    """SQLAlchemy wraps the driver error; `orig` is the oracledb error carrying the ORA text."""
    return map_db_error(exc.orig if exc.orig is not None else exc)


class OracleDatabase:
    def __init__(self, pool: oracledb.ConnectionPool, engine: Engine | None = None):
        self._pool = pool
        # python-oracledb keeps the pooling; SQLAlchemy only borrows a connection from it
        # (NullPool: closing the SQLAlchemy connection hands it back to the oracledb pool).
        # `engine` can be replaced in unit tests.
        self._engine = engine or create_engine(
            "oracle+oracledb://", creator=pool.acquire, poolclass=NullPool
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> OracleDatabase:
        try:
            pool = oracledb.create_pool(
                user=settings.oracle_user,
                password=settings.oracle_password,
                dsn=settings.oracle_dsn,
                min=settings.db_pool_min,
                max=settings.db_pool_max,
                increment=1,
            )
        except oracledb.Error as exc:
            raise map_db_error(exc) from exc
        return cls(pool)

    @contextmanager
    def transaction(self) -> Iterator[OracleSession]:
        """Acquire a connection; COMMIT if the block succeeds, ROLLBACK if it raises.

        Driver errors are translated to the domain errors in app.db.errors. Exceptions that are
        not database errors (for example our own HTTP errors) are re-raised unchanged after
        the rollback.
        """
        try:
            sa_connection = self._engine.connect()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        except oracledb.Error as exc:
            raise map_db_error(exc) from exc
        connection = sa_connection.connection.driver_connection
        orm = Session(bind=sa_connection, expire_on_commit=False)
        try:
            yield OracleSession(connection, orm)
            orm.flush()
            # Commit on the driver connection: it also covers raw cursor work that SQLAlchemy
            # does not know about.
            connection.commit()
        except DBAPIError as exc:
            _safe_rollback(connection)
            raise _translate(exc) from exc
        except oracledb.Error as exc:
            _safe_rollback(connection)
            raise map_db_error(exc) from exc
        except BaseException:
            _safe_rollback(connection)
            raise
        finally:
            try:
                orm.close()
                sa_connection.close()  # returns the connection to the oracledb pool
            except (DBAPIError, oracledb.Error):
                log.warning("could not release connection to the pool", exc_info=True)

    def ping(self) -> None:
        try:
            with self.transaction() as session:
                cursor = session.connection.cursor()
                cursor.execute("SELECT 1 FROM dual")
                cursor.fetchone()
        except DatabaseUnavailableError:
            raise
        except Exception as exc:  # any failure of a trivial query means "not healthy"
            raise DatabaseUnavailableError(str(exc)) from exc

    def close(self) -> None:
        self._engine.dispose()
        try:
            self._pool.close(force=True)
        except oracledb.Error:
            log.warning("error while closing the pool", exc_info=True)


def _safe_rollback(connection: Any) -> None:
    try:
        connection.rollback()
    except Exception:  # noqa: BLE001 - a failed rollback must not hide the original error
        log.warning("rollback failed", exc_info=True)
