from datetime import datetime, timezone
from io import BytesIO
import sqlite3

import pytest

from app.backup import BackupError
from app.backup_remote import S3BackupStore


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, **kwargs):
        body = kwargs["Body"]
        self.objects[kwargs["Key"]] = {
            "body": body.read(),
            "metadata": dict(kwargs.get("Metadata", {})),
            "encryption": kwargs.get("ServerSideEncryption"),
        }
        return {}

    def head_object(self, Bucket, Key):
        item = self.objects[Key]
        result = {
            "ContentLength": len(item["body"]),
            "Metadata": item["metadata"],
        }
        if item["encryption"]:
            result["ServerSideEncryption"] = item["encryption"]
        return result

    def get_object(self, Bucket, Key):
        return {"Body": BytesIO(self.objects[Key]["body"])}

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        contents = [
            {
                "Key": key,
                "Size": len(value["body"]),
                "LastModified": datetime(2026, 11, 10, tzinfo=timezone.utc),
            }
            for key, value in self.objects.items()
            if key.startswith(Prefix)
        ]
        return {"Contents": contents, "IsTruncated": False}


def create_database(path, value="portfolio"):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS state(value TEXT NOT NULL)")
        db.execute("INSERT INTO state(value) VALUES(?)", (value,))


def read_values(path):
    with sqlite3.connect(path) as db:
        return [row[0] for row in db.execute("SELECT value FROM state ORDER BY rowid")]


def test_s3_backup_upload_readback_list_and_restore_are_verified(tmp_path):
    source = tmp_path / "trading.db"
    destination = tmp_path / "restore-candidate.sqlite3"
    create_database(source)
    client = FakeS3()
    store = S3BackupStore(client, "private-backups", prefix="ai-trading", server_side_encryption="AES256")

    uploaded = store.upload(
        source,
        created_at=datetime(2026, 11, 10, 12, 30, tzinfo=timezone.utc),
    )

    assert uploaded["verified"] is True
    assert uploaded["server_side_encryption"] == "AES256"
    assert uploaded["sha256"]
    assert uploaded["key"].startswith("ai-trading/2026/11/10/")
    listed = store.list_backups()
    assert listed[0]["key"] == uploaded["key"]
    assert listed[0]["size_bytes"] == uploaded["size_bytes"]

    restored = store.download(uploaded["key"], destination)
    assert restored == destination.resolve()
    assert read_values(restored) == ["portfolio"]


def test_s3_backup_download_rejects_corrupt_remote_object(tmp_path):
    source = tmp_path / "trading.db"
    destination = tmp_path / "restore-candidate.sqlite3"
    create_database(source)
    client = FakeS3()
    store = S3BackupStore(client, "private-backups", prefix="ai-trading")
    uploaded = store.upload(source)
    client.objects[uploaded["key"]]["body"] = b"corrupted object"

    with pytest.raises(BackupError, match="SHA-256"):
        store.download(uploaded["key"], destination)
    assert not destination.exists()


def test_s3_backup_refuses_unsafe_or_out_of_prefix_keys(tmp_path):
    store = S3BackupStore(FakeS3(), "private-backups", prefix="ai-trading")
    with pytest.raises(BackupError, match="unsafe"):
        store.download("ai-trading/../outside.sqlite3", tmp_path / "restore.sqlite3")
    with pytest.raises(BackupError, match="outside"):
        store.download("other-prefix/backup.sqlite3", tmp_path / "restore.sqlite3")


def test_s3_backup_refuses_to_overwrite_restore_candidate(tmp_path):
    source = tmp_path / "trading.db"
    destination = tmp_path / "restore.sqlite3"
    create_database(source)
    destination.write_text("keep", encoding="utf-8")
    store = S3BackupStore(FakeS3(), "private-backups", prefix="ai-trading")
    uploaded = store.upload(source)
    with pytest.raises(BackupError, match="already exists"):
        store.download(uploaded["key"], destination)
    assert destination.read_text(encoding="utf-8") == "keep"


def test_s3_backup_requires_tls_and_complete_static_credentials():
    from types import SimpleNamespace

    base = {
        "backup_external_enabled": True,
        "backup_s3_bucket": "private-backups",
        "backup_s3_endpoint_url": "http://object-store.example",
        "backup_s3_allow_insecure_endpoint": False,
        "backup_s3_access_key_id": "",
        "backup_s3_secret_access_key": "",
        "backup_s3_session_token": "",
        "backup_s3_region": "us-east-1",
        "backup_s3_prefix": "ai-trading",
        "backup_s3_server_side_encryption": "AES256",
        "backup_s3_kms_key_id": "",
    }
    with pytest.raises(BackupError, match="HTTPS"):
        S3BackupStore.from_settings(SimpleNamespace(**base))

    base["backup_s3_endpoint_url"] = ""
    base["backup_s3_access_key_id"] = "access"
    with pytest.raises(BackupError, match="configured together"):
        S3BackupStore.from_settings(SimpleNamespace(**base))


def test_s3_backup_requires_kms_key_for_kms_encryption():
    with pytest.raises(BackupError, match="KMS key ID"):
        S3BackupStore(
            FakeS3(), "private-backups",
            server_side_encryption="aws:kms",
        )
