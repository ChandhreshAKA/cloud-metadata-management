"""Oracle connection pool and the transaction helper.

oracledb runs in *thin mode* (pure Python): no Oracle Instant Client is required.
All SQL in this project uses bind variables; values are never formatted into SQL text.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import oracledb

from app.config import Settings
from app.db.errors import DatabaseUnavailableError, map_db_error
from app.db.repositories.audit import OracleAuditRepository
from app.db.repositories.providers import OracleProviderRepository
from app.db.repositories.users import OracleUserRepository

log = logging.getLogger(__name__)

# Return NUMBER columns with a scale as Decimal instead of float: money and scores must be exact.
oracledb.defaults.fetch_decimals = True


class OracleSession:
    """The repositories bound to one connection (one transaction)."""

    def __init__(self, connection: oracledb.Connection):
        self.connection = connection
        self.users = OracleUserRepository(connection)
        self.audit = OracleAuditRepository(connection)
        self.providers = OracleProviderRepository(connection)


class OracleDatabase:
    def __init__(self, pool: oracledb.ConnectionPool):
        self._pool = pool

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
            connection = self._pool.acquire()
        except oracledb.Error as exc:
            raise map_db_error(exc) from exc
        try:
            yield OracleSession(connection)
            connection.commit()
        except oracledb.Error as exc:
            _safe_rollback(connection)
            raise map_db_error(exc) from exc
        except BaseException:
            _safe_rollback(connection)
            raise
        finally:
            try:
                self._pool.release(connection)
            except oracledb.Error:
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
        try:
            self._pool.close(force=True)
        except oracledb.Error:
            log.warning("error while closing the pool", exc_info=True)


def _safe_rollback(connection: oracledb.Connection) -> None:
    try:
        connection.rollback()
    except oracledb.Error:
        log.warning("rollback failed", exc_info=True)
