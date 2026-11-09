import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.database_backup import create_database_backup, restore_database_backup


def test_backup_is_integrity_checked_and_can_be_restored(tmp_path):
    database = tmp_path / "trading.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE account (id INTEGER PRIMARY KEY, cash REAL)")
        db.execute("INSERT INTO account VALUES(1,20.0)")
        db.execute("PRAGMA journal_mode=WAL")

    backup = create_database_backup(str(database), retention_days=7)
    backup_path = tmp_path / "backups" / backup["filename"]

    assert backup["integrity"] == "ok"
    assert backup_path.is_file()
    assert backup["size_bytes"] > 0

    restored_path = tmp_path / "restored" / "trading.db"
    restored = restore_database_backup(str(backup_path), str(restored_path))
    assert restored["integrity"] == "ok"
    with sqlite3.connect(restored_path) as db:
        assert db.execute("SELECT cash FROM account WHERE id=1").fetchone()[0] == 20.0


def test_restore_refuses_to_overwrite_existing_database(tmp_path):
    database = tmp_path / "trading.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE sample (value TEXT)")
        db.execute("INSERT INTO sample VALUES('safe')")
    backup = create_database_backup(str(database))
    backup_path = tmp_path / "backups" / backup["filename"]
    target = tmp_path / "existing.db"
    target.write_text("keep existing")

    with pytest.raises(FileExistsError, match="already exists"):
        restore_database_backup(str(backup_path), str(target))
    assert target.read_text() == "keep existing"


def test_backup_retention_removes_only_expired_trading_backups(tmp_path):
    database = tmp_path / "trading.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE sample (value INTEGER)")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    expired = backup_dir / "trading-expired.sqlite3"
    unrelated = backup_dir / "important.sqlite3"
    expired.write_bytes(b"old")
    unrelated.write_bytes(b"keep")
    old_time = (datetime.now(timezone.utc) - timedelta(days=10)).timestamp()
    os.utime(expired, (old_time, old_time))

    result = create_database_backup(
        str(database),
        backup_dir=str(backup_dir),
        retention_days=7,
    )

    assert result["expired_backups_deleted"] == 1
    assert not expired.exists()
    assert unrelated.exists()


def test_backup_rejects_invalid_retention_and_missing_database(tmp_path):
    with pytest.raises(ValueError, match="at least 1"):
        create_database_backup(str(tmp_path / "missing.db"), retention_days=0)
    with pytest.raises(FileNotFoundError):
        create_database_backup(str(tmp_path / "missing.db"))
