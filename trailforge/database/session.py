from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy import Engine, event
from sqlalchemy import create_engine as sqlalchemy_create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from trailforge.config import Settings
from trailforge.database.base import Base
from trailforge.errors import DatabaseBusyError

T = TypeVar("T")


def is_sqlite_busy(exc: BaseException) -> bool:
    """Return True for SQLite lock-contention errors (and only those)."""
    message = str(exc).lower()
    return "database is locked" in message or "database is busy" in message


def begin_immediate(session: Session) -> None:
    """Upgrade the session's transaction to ``BEGIN IMMEDIATE``.

    SQLite opens deferred transactions: reads run outside any lock and the
    write lock is only taken when the first DML statement executes. That
    leaves a check-then-write window in which a concurrent connection can
    commit between this transaction's reads and writes. ``BEGIN IMMEDIATE``
    acquires the database write lock up front, serializing writers across
    connections and processes without any in-process locking.

    Lock contention surfaces as :class:`DatabaseBusyError` so it is never
    confused with a business conflict. If the session already participates
    in a transaction the existing transaction is left untouched.
    """
    if session.in_transaction():
        return
    try:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    except OperationalError as exc:
        if is_sqlite_busy(exc):
            raise DatabaseBusyError(
                "timed out acquiring the SQLite write lock",
                context={"reason": "begin_immediate"},
            ) from exc
        raise


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.ensure_runtime_directories()
        connect_args: dict[str, Any] = {
            "check_same_thread": False,
            "timeout": settings.sqlite_timeout_seconds,
        }
        engine_options: dict[str, Any] = {
            "connect_args": connect_args,
            "future": True,
        }
        if settings.database_url in {"sqlite://", "sqlite:///:memory:"}:
            engine_options["poolclass"] = StaticPool
        self.engine = sqlalchemy_create_engine(settings.database_url, **engine_options)
        self._configure_sqlite(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            autoflush=False,
            expire_on_commit=False,
        )

    def _configure_sqlite(self, engine: Engine) -> None:
        timeout_ms = int(self.settings.sqlite_timeout_seconds * 1000)

        @event.listens_for(engine, "connect")
        def set_pragmas(connection: sqlite3.Connection, record: Any) -> None:
            del record
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={timeout_ms}")
            if self.settings.database_url not in {"sqlite://", "sqlite:///:memory:"}:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    def create_schema(self) -> None:
        from trailforge.models import load_all_models

        load_all_models()
        Base.metadata.create_all(self.engine)

    def drop_schema(self) -> None:
        from trailforge.models import load_all_models

        load_all_models()
        Base.metadata.drop_all(self.engine)

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def write_session(self) -> Generator[Session, None, None]:
        """Session whose transaction holds the SQLite write lock from the start.

        Use this for read-modify-write operations that must stay consistent
        under concurrent connections. Lock contention is reported as
        :class:`DatabaseBusyError`; business errors pass through unchanged.
        """
        session = self.session_factory()
        try:
            begin_immediate(session)
            yield session
            session.commit()
        except OperationalError as exc:
            session.rollback()
            if is_sqlite_busy(exc):
                raise DatabaseBusyError(
                    "SQLite database stayed busy beyond the configured timeout",
                    context={"timeout_seconds": self.settings.sqlite_timeout_seconds},
                ) from exc
            raise
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dependency(self) -> Generator[Session, None, None]:
        with self.session() as session:
            yield session

    def run_write(self, operation: Callable[[Session], T]) -> T:
        attempts = self.settings.sqlite_busy_retries + 1
        for attempt in range(attempts):
            try:
                with self.write_session() as session:
                    return operation(session)
            except DatabaseBusyError as exc:
                if attempt == attempts - 1:
                    raise DatabaseBusyError(
                        "SQLite remained busy after configured retries",
                        context={"attempts": attempts},
                    ) from exc
                time.sleep(self.settings.sqlite_busy_backoff_seconds * (2**attempt))
        raise AssertionError("unreachable")

    def verify_connection(self) -> dict[str, str | int]:
        with self.engine.connect() as connection:
            foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
            journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
            user_version = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
        return {
            "foreign_keys": int(foreign_keys),
            "journal_mode": str(journal_mode),
            "user_version": int(user_version),
        }

    @property
    def path(self) -> Path | None:
        return self.settings.database_path
