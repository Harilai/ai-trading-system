import sqlite3

import pytest

from app.backup import BackupError, backup_sqlite, restore_sqlite


def create_database(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS state (value TEXT NOT NULL)")
        db.execute("INSERT INTO state(value) VALUES(?)", (value,))


def read_values(path):
    with sqlite3.connect(path) as db:
        return [row[0] for row in db.execute("SELECT value FROM state ORDER BY rowid")]


def test_backup_copies_committed_wal_data_and_passes_integrity_check(tmp_path):
    source = tmp_path / "source with spaces.db"
    destination = tmp_path / "backups" / "snapshot.db"
    create_database(source, "initial")

    with sqlite3.connect(source) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("INSERT INTO state(value) VALUES('committed-in-wal')")

    result = backup_sqlite(source, destination)

    assert result == destination.resolve()
    assert read_values(destination) == ["initial", "committed-in-wal"]


def test_restore_preserves_current_database_before_replacing_it(tmp_path):
    backup = tmp_path / "backup.db"
    destination = tmp_path / "live.db"
    create_database(backup, "backup-state")
    create_database(destination, "current-state")

    safety_copy = restore_sqlite(backup, destination)

    assert read_values(destination) == ["backup-state"]
    assert safety_copy is not None
    assert safety_copy.exists()
    assert read_values(safety_copy) == ["current-state"]


def test_restore_without_existing_destination_does_not_create_safety_copy(tmp_path):
    backup = tmp_path / "backup.db"
    destination = tmp_path / "new" / "live.db"
    create_database(backup, "restored")

    safety_copy = restore_sqlite(backup, destination)

    assert safety_copy is None
    assert read_values(destination) == ["restored"]


def test_backup_rejects_same_source_and_destination(tmp_path):
    source = tmp_path / "same.db"
    create_database(source, "state")

    with pytest.raises(BackupError, match="different files"):
        backup_sqlite(source, source)


def test_backup_rejects_corrupt_source_without_overwriting_destination(tmp_path):
    source = tmp_path / "corrupt.db"
    destination = tmp_path / "existing-backup.db"
    source.write_bytes(b"not a sqlite database")
    create_database(destination, "keep-me")

    with pytest.raises(BackupError, match="integrity"):
        backup_sqlite(source, destination)

    assert read_values(destination) == ["keep-me"]


def test_restore_rejects_corrupt_backup_without_touching_destination(tmp_path):
    backup = tmp_path / "corrupt-backup.db"
    destination = tmp_path / "live.db"
    backup.write_bytes(b"broken")
    create_database(destination, "still-live")

    with pytest.raises(BackupError, match="integrity"):
        restore_sqlite(backup, destination)

    assert read_values(destination) == ["still-live"]


def test_restore_rejects_same_source_and_destination(tmp_path):
    source = tmp_path / "same.db"
    create_database(source, "state")

    with pytest.raises(BackupError, match="different files"):
        restore_sqlite(source, source)
