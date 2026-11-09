from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


def _integrity_check(path: Path) -> None:
    with sqlite3.connect(str(path)) as db:
        result = db.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError(f"SQLite integrity check failed: {result}")


def create_database_backup(
    database_path: str,
    *,
    backup_dir: str | None = None,
    retention_days: int = 7,
) -> dict:
    if retention_days < 1:
        raise ValueError("retention_days must be at least 1")
    source_path = Path(database_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"Database file not found: {source_path}")

    destination_dir = Path(backup_dir) if backup_dir else source_path.parent / "backups"
    destination_dir.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc)
    stamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    destination = destination_dir / f"trading-{stamp}-{uuid4().hex[:8]}.sqlite3"
    temporary = destination.with_suffix(".sqlite3.tmp")

    try:
        with sqlite3.connect(str(source_path), timeout=30) as source:
            with sqlite3.connect(str(temporary), timeout=30) as target:
                source.backup(target)
        _integrity_check(temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()

    cutoff = created_at - timedelta(days=retention_days)
    deleted = 0
    for candidate in destination_dir.glob("trading-*.sqlite3"):
        if candidate == destination:
            continue
        modified = datetime.fromtimestamp(candidate.stat().st_mtime, timezone.utc)
        if modified < cutoff:
            candidate.unlink()
            deleted += 1

    return {
        "path": str(destination),
        "filename": destination.name,
        "size_bytes": destination.stat().st_size,
        "integrity": "ok",
        "created_at": created_at.isoformat(),
        "retention_days": retention_days,
        "expired_backups_deleted": deleted,
    }


def restore_database_backup(backup_path: str, target_path: str) -> dict:
    source_path = Path(backup_path)
    destination = Path(target_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"Backup file not found: {source_path}")
    if destination.exists():
        raise FileExistsError(
            "Restore target already exists; choose a new path to avoid overwriting live data"
        )
    _integrity_check(source_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        with sqlite3.connect(str(source_path), timeout=30) as source:
            with sqlite3.connect(str(temporary), timeout=30) as target:
                source.backup(target)
        _integrity_check(temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()

    return {
        "path": str(destination),
        "size_bytes": destination.stat().st_size,
        "integrity": "ok",
        "restored_from": str(source_path),
    }
