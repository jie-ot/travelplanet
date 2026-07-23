"""Database engine and session management.

Dialect-neutral engine built from `settings.DATABASE_URL` (SQLite demo,
PostgreSQL-ready). SQLite needs `PRAGMA foreign_keys=ON` enabled per
connection (《数据库表结构与迁移规范》十五.2).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine

from app.core.config import settings


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _ensure_sqlite_dir(url: str) -> None:
    """Make sure the SQLite file's parent directory exists."""
    # Form: sqlite:///./data/travelplanet.db
    prefix = "sqlite:///"
    if url.startswith(prefix):
        path = url[len(prefix):]
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)


def _build_engine() -> Engine:
    url = settings.DATABASE_URL
    connect_args: dict = {}
    if _is_sqlite(url):
        _ensure_sqlite_dir(url)
        # Allow usage across FastAPI's threadpool for sync routes.
        connect_args = {"check_same_thread": False}
    return create_engine(url, echo=False, connect_args=connect_args)


engine = _build_engine()


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001
    """Enable foreign key enforcement on SQLite connections."""
    if _is_sqlite(settings.DATABASE_URL):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a database session."""
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager for service-layer transactions."""
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
