"""Helpers for ORM tests that run on in-memory SQLite (no Oracle needed).

SQLite proves the mapping and the repository logic. It does NOT prove Oracle behaviour:
tests/integration does that.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.orm import Base


def sqlite_engine() -> Engine:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return engine


@contextmanager
def sqlite_session() -> Iterator[Session]:
    engine = sqlite_engine()
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


@contextmanager
def capture_statements(session: Session) -> Iterator[list[tuple[str, object]]]:
    """Record (SQL text, parameters) of every statement sent to the driver."""
    seen: list[tuple[str, object]] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        seen.append((statement, parameters))

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)
