from __future__ import annotations

import sqlite3
import time
from collections.abc import Generator
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

    def dependency(self) -> Generator[Session, None, None]:
        with self.session() as session:
            yield session

    def run_write(self, operation: callable[[Session], T]) -> T:
        attempts = self.settings.sqlite_busy_retries + 1
        for attempt in range(attempts):
            try:
                with self.session() as session:
                    return operation(session)
            except OperationalError as exc:
                if not self._is_busy(exc) or attempt == attempts - 1:
                    if self._is_busy(exc):
                        raise DatabaseBusyError(
                            "SQLite remained busy after configured retries",
                            context={"attempts": attempts},
                        ) from exc
                    raise
                delay = self.settings.sqlite_busy_backoff_seconds * (2**attempt)
                time.sleep(delay)
        raise AssertionError("unreachable")

    @staticmethod
    def _is_busy(exc: OperationalError) -> bool:
        message = str(exc).lower()
        return "database is locked" in message or "database is busy" in message

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
