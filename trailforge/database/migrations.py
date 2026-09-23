from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import inspect

from trailforge.database.session import Database
from trailforge.models.audit import SchemaMigration


@dataclass(frozen=True)
class Migration:
    version: str
    description: str


MIGRATIONS = [
    Migration(version="0001", description="Initial TrailForge schema"),
]


def initialize_database(database: Database) -> list[str]:
    database.create_schema()
    applied: list[str] = []
    with database.session() as session:
        known = {
            row.version
            for row in session.query(SchemaMigration).order_by(SchemaMigration.version).all()
        }
        for migration in MIGRATIONS:
            if migration.version in known:
                continue
            session.add(
                SchemaMigration(
                    version=migration.version,
                    description=migration.description,
                )
            )
            applied.append(migration.version)
    return applied


def migration_status(database: Database) -> dict[str, object]:
    inspector = inspect(database.engine)
    if "schema_migrations" not in inspector.get_table_names():
        return {
            "initialized": False,
            "applied": [],
            "pending": [item.version for item in MIGRATIONS],
        }
    with database.session() as session:
        applied = [
            row.version
            for row in session.query(SchemaMigration).order_by(SchemaMigration.version).all()
        ]
    pending = [item.version for item in MIGRATIONS if item.version not in set(applied)]
    return {"initialized": True, "applied": applied, "pending": pending}


def assert_database_integrity(database: Database) -> dict[str, object]:
    with database.engine.connect() as connection:
        integrity = connection.exec_driver_sql("PRAGMA integrity_check").scalar_one()
        foreign_key_rows = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    return {
        "integrity_check": str(integrity),
        "foreign_key_violations": [list(row) for row in foreign_key_rows],
        "healthy": integrity == "ok" and not foreign_key_rows,
    }
