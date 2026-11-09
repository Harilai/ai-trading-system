from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import tempfile


class BackupError(RuntimeError):
    pass


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _integrity_check(path: Path) -> None:
    if not path.is_file():
        raise BackupError(f"SQLite database does not exist: {path}")
    try:
        with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as db:
            result = db.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        raise BackupError(f"SQLite integrity validation failed for {path}: {exc}") from exc
    if not result or result[0] != "ok":
        raise BackupError(f"SQLite integrity check failed for {path}: {result[0] if result else 'no result'}")


def _copy_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
        delete=False,
    )
    temporary = Path(handle.name)
    handle.close()
    try:
        with sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True) as source_db:
            with sqlite3.connect(temporary) as target_db:
                source_db.backup(target_db)
        _integrity_check(temporary)
        with temporary.open("rb") as file_handle:
            os.fsync(file_handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def backup_sqlite(source_path: str | Path, destination_path: str | Path) -> Path:
    """Create an atomic, integrity-checked SQLite backup including committed WAL data."""
    source = _resolved(source_path)
    destination = _resolved(destination_path)
    if source == destination:
        raise BackupError("Backup source and destination must be different files")
    _integrity_check(source)
    try:
        _copy_database(source, destination)
    except (sqlite3.Error, OSError) as exc:
        raise BackupError(f"Unable to create SQLite backup: {exc}") from exc
    return destination


def restore_sqlite(backup_path: str | Path, destination_path: str | Path) -> Path | None:
    """Restore a validated backup, first creating a safety copy of the current database."""
    backup = _resolved(backup_path)
    destination = _resolved(destination_path)
    if backup == destination:
        raise BackupError("Restore source and destination must be different files")
    _integrity_check(backup)
    safety_copy = None
    if destination.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safety_copy = destination.with_name(f"{destination.name}.pre-restore-{stamp}")
        if safety_copy.exists():
            raise BackupError(f"Safety backup already exists: {safety_copy}")
        backup_sqlite(destination, safety_copy)
    try:
        _copy_database(backup, destination)
    except (sqlite3.Error, OSError) as exc:
        raise BackupError(f"Unable to restore SQLite database: {exc}") from exc
    return safety_copy


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomic SQLite backup and restore utility")
    commands = parser.add_subparsers(dest="command", required=True)
    backup_parser = commands.add_parser("backup", help="Create an integrity-checked backup")
    backup_parser.add_argument("--source", required=True)
    backup_parser.add_argument("--destination", required=True)
    restore_parser = commands.add_parser("restore", help="Restore a backup while preserving the current DB")
    restore_parser.add_argument("--source", required=True, help="Backup database path")
    restore_parser.add_argument("--destination", required=True, help="Database path to replace")
    args = parser.parse_args()

    if args.command == "backup":
        result = backup_sqlite(args.source, args.destination)
        print(f"Backup created and verified: {result}")
        return 0

    safety_copy = restore_sqlite(args.source, args.destination)
    print(f"Restore completed: {Path(args.destination).expanduser().resolve()}")
    if safety_copy:
        print(f"Previous database preserved at: {safety_copy}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
