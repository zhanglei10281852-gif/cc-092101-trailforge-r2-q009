from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from trailforge.config import Settings
from trailforge.database.migrations import (
    assert_database_integrity,
    initialize_database,
    migration_status,
)
from trailforge.database.session import Database
from trailforge.models.users import User


def test_settings_reject_non_sqlite_url() -> None:
    with pytest.raises(ValueError, match="SQLite"):
        Settings(database_url="unsupported://localhost/trailforge")


def test_database_enables_foreign_keys_and_wal(database: Database) -> None:
    details = database.verify_connection()
    assert details["foreign_keys"] == 1
    assert str(details["journal_mode"]).lower() == "wal"


def test_migration_initialization_is_idempotent(database: Database) -> None:
    assert initialize_database(database) == []
    status = migration_status(database)
    assert status == {"initialized": True, "applied": ["0001"], "pending": []}


def test_integrity_check_reports_healthy_database(database: Database) -> None:
    result = assert_database_integrity(database)
    assert result["integrity_check"] == "ok"
    assert result["foreign_key_violations"] == []
    assert result["healthy"] is True


def test_unique_constraint_rolls_back_transaction(database: Database) -> None:
    with pytest.raises(IntegrityError), database.session() as session:
        session.add(User(email="same@example.com", display_name="First"))
        session.flush()
        session.add(User(email="same@example.com", display_name="Second"))
        session.flush()
    with database.session() as session:
        assert session.query(User).count() == 0


def test_foreign_key_constraint_is_enforced(database: Database) -> None:
    with pytest.raises(IntegrityError), database.session() as session:
        session.execute(
            text(
                "INSERT INTO sport_profiles "
                "(user_id,height_cm,weight_kg,fitness_level,outdoor_experience,"
                "weekly_training_minutes,notes,created_at,updated_at) "
                "VALUES (999,170,70,'beginner','',0,'','2026-01-01T00:00:00Z',"
                "'2026-01-01T00:00:00Z')"
            )
        )


def test_file_database_survives_engine_restart(settings: Settings) -> None:
    first = Database(settings)
    initialize_database(first)
    with first.session() as session:
        session.add(User(email="persist@example.com", display_name="Persistent"))
    first.engine.dispose()
    second = Database(settings)
    initialize_database(second)
    with second.session() as session:
        restored = session.query(User).filter_by(email="persist@example.com").one()
        assert restored.display_name == "Persistent"
    second.engine.dispose()


def test_concurrent_run_write_preserves_all_rows(database: Database) -> None:
    def write(index: int) -> int:
        return database.run_write(lambda session: _insert_concurrent_user(session, index))

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(write, range(12)))
    assert len(set(ids)) == 12
    with database.session() as session:
        assert session.query(User).filter(User.email.like("thread-%")).count() == 12


def _insert_concurrent_user(session, index: int) -> int:
    user = User(email=f"thread-{index}@example.com", display_name=f"Thread {index}")
    session.add(user)
    session.flush()
    return user.id
