from datetime import datetime, timedelta, timezone
import os
import sqlite3

from app.backup_scheduler import run_backup_cycle


def create_database(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE state(value TEXT)")
        db.execute("INSERT INTO state(value) VALUES(?)", (value,))


def read_value(path):
    with sqlite3.connect(path) as db:
        return db.execute("SELECT value FROM state").fetchone()[0]


def test_backup_cycle_snapshots_existing_databases_and_skips_missing(tmp_path):
    trading = tmp_path / "trading.db"
    quota = tmp_path / "twelve_data_state.sqlite3"
    missing = tmp_path / "not-created.db"
    destination = tmp_path / "backups"
    create_database(trading, "portfolio")
    create_database(quota, "quota-ledger")

    now = datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc)
    created = run_backup_cycle(
        [trading, quota, missing],
        destination,
        retention_days=14,
        now=now,
    )

    assert len(created) == 2
    assert {path.name for path in created} == {
        "trading-20261110T000000Z.sqlite3",
        "twelve_data_state-20261110T000000Z.sqlite3",
    }
    assert read_value(destination / "trading-20261110T000000Z.sqlite3") == "portfolio"
    assert read_value(destination / "twelve_data_state-20261110T000000Z.sqlite3") == "quota-ledger"


def test_backup_cycle_continues_after_one_database_is_corrupt(tmp_path):
    healthy = tmp_path / "trading.db"
    corrupt = tmp_path / "corrupt.db"
    destination = tmp_path / "backups"
    create_database(healthy, "good")
    corrupt.write_bytes(b"not sqlite")

    created = run_backup_cycle(
        [corrupt, healthy],
        destination,
        now=datetime(2026, 11, 10, tzinfo=timezone.utc),
    )

    assert len(created) == 1
    assert read_value(created[0]) == "good"


def test_backup_cycle_removes_only_expired_sqlite_backup_files(tmp_path):
    source = tmp_path / "trading.db"
    destination = tmp_path / "backups"
    create_database(source, "current")
    destination.mkdir()
    expired = destination / "old.sqlite3"
    keep = destination / "keep.txt"
    expired.write_bytes(b"old backup")
    keep.write_text("not a backup", encoding="utf-8")
    old_time = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
    os.utime(expired, (old_time, old_time))

    created = run_backup_cycle(
        [source],
        destination,
        retention_days=14,
    )

    assert len(created) == 1
    assert not expired.exists()
    assert keep.exists()
    assert created[0].exists()


def test_backup_cycle_rejects_invalid_retention(tmp_path):
    try:
        run_backup_cycle([], tmp_path / "backups", retention_days=0)
    except ValueError as exc:
        assert "retention_days" in str(exc)
    else:
        raise AssertionError("Expected invalid retention to fail")
