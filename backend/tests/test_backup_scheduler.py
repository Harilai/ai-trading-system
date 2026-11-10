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


def test_backup_cycle_replicates_and_records_verified_off_host_objects(tmp_path):
    from app.backup_scheduler import read_backup_status
    from hashlib import sha256

    class FakeExternalStore:
        def __init__(self):
            self.keys = set()
            self.upload_calls = 0

        def list_backups(self):
            return [{"key": key} for key in self.keys]

        def upload(self, path, *, created_at):
            self.upload_calls += 1
            digest = sha256(path.read_bytes()).hexdigest()
            key = f"ai-trading/{path.name}-{digest[:12]}.sqlite3"
            self.keys.add(key)
            return {
                "key": key, "sha256": digest, "size_bytes": path.stat().st_size,
                "server_side_encryption": "AES256", "verified": True,
            }

    trading = tmp_path / "trading.db"
    quota = tmp_path / "twelve_data_state.sqlite3"
    destination = tmp_path / "backups"
    create_database(trading, "portfolio")
    create_database(quota, "quota-ledger")
    external = FakeExternalStore()

    created = run_backup_cycle(
        [trading, quota], destination,
        now=datetime(2026, 11, 10, tzinfo=timezone.utc),
        external_store=external, external_enabled=True,
    )

    assert len(created) == 2
    assert external.upload_calls == 2
    status = read_backup_status(destination)
    assert status["external"]["status"] == "verified"
    assert len(status["external"]["uploaded"]) == 2
    assert len(status["external"]["uploaded_sources"]) == 2
    assert all(item["verified"] for item in status["external"]["uploaded"])


def test_backup_cycle_retries_failed_off_host_uploads_without_losing_local_backups(tmp_path):
    from app.backup import BackupError
    from app.backup_scheduler import read_backup_status
    from hashlib import sha256

    class FailingStore:
        def list_backups(self):
            return []

        def upload(self, path, *, created_at):
            raise BackupError("temporary object store outage")

    class WorkingStore:
        def __init__(self):
            self.keys = set()
            self.upload_calls = 0

        def list_backups(self):
            return [{"key": key} for key in self.keys]

        def upload(self, path, *, created_at):
            self.upload_calls += 1
            digest = sha256(path.read_bytes()).hexdigest()
            key = f"retry/{path.name}-{digest[:12]}.sqlite3"
            self.keys.add(key)
            return {
                "key": key, "sha256": digest, "size_bytes": path.stat().st_size,
                "server_side_encryption": "AES256", "verified": True,
            }

    trading = tmp_path / "trading.db"
    destination = tmp_path / "backups"
    create_database(trading, "portfolio")
    run_backup_cycle(
        [trading], destination,
        now=datetime(2026, 11, 10, tzinfo=timezone.utc),
        external_store=FailingStore(), external_enabled=True,
    )
    failed = read_backup_status(destination)
    assert failed["local"]["status"] == "created"
    assert failed["external"]["status"] == "failed"
    assert failed["external"]["uploaded_sources"] == {}
    assert failed["local"]["unverified_offsite_count"] == 1
    assert list(destination.glob("*.sqlite3"))

    working = WorkingStore()
    run_backup_cycle(
        [trading], destination,
        now=datetime(2026, 11, 11, tzinfo=timezone.utc),
        external_store=working, external_enabled=True,
    )
    recovered = read_backup_status(destination)
    assert recovered["external"]["status"] == "verified"
    assert recovered["local"]["unverified_offsite_count"] == 0
    assert working.upload_calls == 2
    assert len(recovered["external"]["uploaded_sources"]) == 2


def test_backup_cycle_does_not_reupload_unchanged_verified_snapshots(tmp_path):
    from hashlib import sha256

    class FakeExternalStore:
        def __init__(self):
            self.keys = set()
            self.upload_calls = 0

        def list_backups(self):
            return [{"key": key} for key in self.keys]

        def upload(self, path, *, created_at):
            self.upload_calls += 1
            digest = sha256(path.read_bytes()).hexdigest()
            key = f"verified/{path.name}-{digest[:12]}.sqlite3"
            self.keys.add(key)
            return {
                "key": key, "sha256": digest, "size_bytes": path.stat().st_size,
                "server_side_encryption": "AES256", "verified": True,
            }

    trading = tmp_path / "trading.db"
    destination = tmp_path / "backups"
    create_database(trading, "portfolio")
    external = FakeExternalStore()
    run_backup_cycle(
        [trading], destination,
        now=datetime(2026, 11, 10, tzinfo=timezone.utc),
        external_store=external, external_enabled=True,
    )
    assert external.upload_calls == 1

    run_backup_cycle(
        [], destination,
        now=datetime(2026, 11, 10, 0, 5, tzinfo=timezone.utc),
        external_store=external, external_enabled=True,
    )
    assert external.upload_calls == 1


def test_retention_keeps_expired_local_snapshot_until_offhost_verification(tmp_path):
    from app.backup import BackupError
    from hashlib import sha256

    class FailingStore:
        def list_backups(self):
            return []

        def upload(self, path, *, created_at):
            raise BackupError("offsite unavailable")

    class WorkingStore:
        def __init__(self):
            self.keys = set()

        def list_backups(self):
            return [{"key": key} for key in self.keys]

        def upload(self, path, *, created_at):
            digest = sha256(path.read_bytes()).hexdigest()
            key = f"retention/{path.name}-{digest[:12]}.sqlite3"
            self.keys.add(key)
            return {
                "key": key, "sha256": digest, "size_bytes": path.stat().st_size,
                "server_side_encryption": "AES256", "verified": True,
            }

    source = tmp_path / "trading.db"
    destination = tmp_path / "backups"
    create_database(source, "current")
    destination.mkdir()
    expired = destination / "trading-older.sqlite3"
    create_database(expired, "old")
    old_time = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
    os.utime(expired, (old_time, old_time))

    run_backup_cycle(
        [source], destination, retention_days=14,
        external_store=FailingStore(), external_enabled=True,
    )
    assert expired.exists()

    run_backup_cycle(
        [], destination, retention_days=14,
        now=datetime.now(timezone.utc),
        external_store=WorkingStore(), external_enabled=True,
    )
    assert not expired.exists()
