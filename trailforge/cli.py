from __future__ import annotations

import argparse
import json
from pathlib import Path

from trailforge.config import get_settings
from trailforge.database.migrations import (
    assert_database_integrity,
    initialize_database,
    migration_status,
)
from trailforge.database.session import Database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trailforge", description="TrailForge maintenance CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="create the SQLite schema and apply migrations")
    subparsers.add_parser("migration-status", help="show applied and pending migrations")
    subparsers.add_parser("check-db", help="run SQLite integrity and foreign-key checks")
    reset = subparsers.add_parser("reset-db", help="delete and recreate the local SQLite database")
    reset.add_argument("--confirm", action="store_true", help="confirm destructive local reset")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = get_settings()
    database = Database(settings)
    if args.command == "init-db":
        applied = initialize_database(database)
        print(json.dumps({"database": str(database.path), "applied": applied}, ensure_ascii=False))
        return 0
    if args.command == "migration-status":
        print(json.dumps(migration_status(database), ensure_ascii=False))
        return 0
    if args.command == "check-db":
        result = assert_database_integrity(database)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["healthy"] else 1
    if args.command == "reset-db":
        if not args.confirm:
            parser = build_parser()
            parser.error("reset-db requires --confirm")
        path = database.path
        if path is None:
            raise SystemExit("reset-db is unavailable for in-memory SQLite")
        database.engine.dispose()
        allowed_suffixes = {".db", ".sqlite", ".sqlite3"}
        if path.suffix.lower() not in allowed_suffixes:
            raise SystemExit("refusing to reset a file without a SQLite extension")
        for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
            if candidate.exists():
                candidate.unlink()
        recreated = Database(settings)
        applied = initialize_database(recreated)
        print(json.dumps({"database": str(path), "applied": applied}, ensure_ascii=False))
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
