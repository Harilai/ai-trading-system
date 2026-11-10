from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlparse

from .backup import BackupError, _integrity_check
from .config import settings


class S3BackupStore:
    """Verified off-host backup store for S3 and compatible object storage."""

    def __init__(
        self,
        client,
        bucket: str,
        *,
        prefix: str = "ai-trading-system",
        server_side_encryption: str = "AES256",
        kms_key_id: str = "",
    ):
        if not bucket or not bucket.strip():
            raise BackupError("External backup bucket is required")
        normalized_prefix = prefix.strip("/")
        if any(part in (".", "..") for part in normalized_prefix.split("/") if part):
            raise BackupError("External backup prefix contains an unsafe path segment")
        if server_side_encryption not in ("AES256", "aws:kms", "provider-managed"):
            raise BackupError("Unsupported external backup encryption mode")
        if server_side_encryption == "aws:kms" and not kms_key_id.strip():
            raise BackupError("A KMS key ID is required when using aws:kms encryption")
        self.client = client
        self.bucket = bucket.strip()
        self.prefix = normalized_prefix
        self.server_side_encryption = server_side_encryption
        self.kms_key_id = kms_key_id.strip()

    @classmethod
    def from_settings(cls, config=settings) -> "S3BackupStore":
        if not config.backup_external_enabled:
            raise BackupError("External backups are disabled")
        if not config.backup_s3_bucket.strip():
            raise BackupError("BACKUP_S3_BUCKET is required when external backups are enabled")

        endpoint_url = config.backup_s3_endpoint_url.strip() or None
        if endpoint_url:
            parsed = urlparse(endpoint_url)
            if parsed.scheme != "https" and not config.backup_s3_allow_insecure_endpoint:
                raise BackupError("External backup endpoint must use HTTPS")
            if parsed.scheme not in ("https", "http") or not parsed.netloc:
                raise BackupError("External backup endpoint URL is invalid")

        access_key = config.backup_s3_access_key_id.strip()
        secret_key = config.backup_s3_secret_access_key.strip()
        session_token = config.backup_s3_session_token.strip()
        if bool(access_key) != bool(secret_key):
            raise BackupError("Both S3 access key ID and secret access key must be configured together")

        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise BackupError("boto3 is required when external backups are enabled") from exc

        client_kwargs = {
            "service_name": "s3",
            "region_name": config.backup_s3_region,
            "endpoint_url": endpoint_url,
            "config": Config(
                signature_version="s3v4",
                retries={"max_attempts": 5, "mode": "standard"},
                connect_timeout=10,
                read_timeout=60,
            ),
        }
        if access_key:
            client_kwargs["aws_access_key_id"] = access_key
            client_kwargs["aws_secret_access_key"] = secret_key
        if session_token:
            client_kwargs["aws_session_token"] = session_token
        try:
            client = boto3.client(**client_kwargs)
        except Exception as exc:
            raise BackupError(f"Unable to initialize S3 backup client ({type(exc).__name__})") from exc

        return cls(
            client,
            config.backup_s3_bucket,
            prefix=config.backup_s3_prefix,
            server_side_encryption=config.backup_s3_server_side_encryption,
            kms_key_id=config.backup_s3_kms_key_id,
        )

    def _key_for(self, path: Path, created_at: datetime, digest: str) -> str:
        stamp = created_at.astimezone(timezone.utc)
        name = f"{path.stem}-{stamp.strftime('%Y%m%dT%H%M%SZ')}-{digest[:12]}.sqlite3"
        date_prefix = stamp.strftime("%Y/%m/%d")
        return "/".join(part for part in (self.prefix, date_prefix, name) if part)

    def _validate_key(self, key: str) -> str:
        if not isinstance(key, str) or not key.strip():
            raise BackupError("Remote backup key is required")
        normalized = key.strip().lstrip("/")
        if any(part in (".", "..") for part in normalized.split("/")):
            raise BackupError("Remote backup key contains an unsafe path segment")
        if self.prefix and not normalized.startswith(self.prefix + "/"):
            raise BackupError("Remote backup key is outside the configured prefix")
        if not normalized.endswith(".sqlite3"):
            raise BackupError("Remote object is not a SQLite backup")
        return normalized

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def upload(self, source_path: str | Path, *, created_at: datetime | None = None) -> dict:
        source = Path(source_path).expanduser().resolve()
        _integrity_check(source)
        timestamp = (created_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        digest = self._sha256(source)
        size = source.stat().st_size
        if size > 5 * 1024 * 1024 * 1024:
            raise BackupError("SQLite backup exceeds the 5 GiB single-request S3 upload limit")
        key = self._key_for(source, timestamp, digest)
        metadata = {
            "sha256": digest,
            "source_filename": source.name,
            "created_at": timestamp.isoformat(),
        }
        args = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": source.open("rb"),
            "ContentType": "application/vnd.sqlite3",
            "Metadata": metadata,
        }
        if self.server_side_encryption != "provider-managed":
            args["ServerSideEncryption"] = self.server_side_encryption
            if self.server_side_encryption == "aws:kms":
                args["SSEKMSKeyId"] = self.kms_key_id

        try:
            with args["Body"] as body:
                self.client.put_object(**{**args, "Body": body})
            head = self.client.head_object(Bucket=self.bucket, Key=key)
            if int(head.get("ContentLength", -1)) != size:
                raise BackupError("Remote backup size verification failed")
            remote_metadata = {str(k).lower(): str(v) for k, v in head.get("Metadata", {}).items()}
            if remote_metadata.get("sha256") != digest:
                raise BackupError("Remote backup checksum metadata verification failed")
            if self.server_side_encryption != "provider-managed":
                if head.get("ServerSideEncryption") != self.server_side_encryption:
                    raise BackupError("Remote backup server-side encryption verification failed")
            remote = self.client.get_object(Bucket=self.bucket, Key=key)
            remote_digest = hashlib.sha256()
            remote_size = 0
            body = remote["Body"]
            try:
                while True:
                    chunk = body.read(1024 * 1024)
                    if not chunk:
                        break
                    remote_digest.update(chunk)
                    remote_size += len(chunk)
            finally:
                body.close()
            if remote_size != size or remote_digest.hexdigest() != digest:
                raise BackupError("Remote backup read-back checksum verification failed")
        except BackupError:
            raise
        except Exception as exc:
            raise BackupError(f"External backup upload or verification failed ({type(exc).__name__})") from exc

        return {
            "key": key,
            "sha256": digest,
            "size_bytes": size,
            "server_side_encryption": self.server_side_encryption,
            "verified": True,
        }

    def download(self, key: str, destination_path: str | Path) -> Path:
        remote_key = self._validate_key(key)
        destination = Path(destination_path).expanduser().resolve()
        if destination.exists():
            raise BackupError(f"Restore candidate already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_handle = tempfile.NamedTemporaryFile(
            prefix=f".{destination.name}.",
            suffix=".download",
            dir=destination.parent,
            delete=False,
        )
        temporary = Path(temporary_handle.name)
        temporary_handle.close()
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=remote_key)
            expected_size = int(head.get("ContentLength", -1))
            metadata = {str(k).lower(): str(v) for k, v in head.get("Metadata", {}).items()}
            expected_digest = metadata.get("sha256")
            if (
                expected_size < 1
                or not expected_digest
                or len(expected_digest) != 64
                or any(char not in "0123456789abcdef" for char in expected_digest.lower())
            ):
                raise BackupError("Remote backup is missing valid size or SHA-256 metadata")
            if self.server_side_encryption != "provider-managed":
                if head.get("ServerSideEncryption") != self.server_side_encryption:
                    raise BackupError("Remote backup encryption verification failed")
            response = self.client.get_object(Bucket=self.bucket, Key=remote_key)
            digest = hashlib.sha256()
            written = 0
            body = response["Body"]
            try:
                with temporary.open("wb") as handle:
                    while True:
                        chunk = body.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        digest.update(chunk)
                        written += len(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                body.close()
            if written != expected_size or digest.hexdigest() != expected_digest:
                raise BackupError("Downloaded backup failed size or SHA-256 verification")
            _integrity_check(temporary)
            # Hard-link publication is atomic and refuses to overwrite an existing candidate.
            os.link(temporary, destination)
            temporary.unlink(missing_ok=True)
            return destination
        except BackupError:
            temporary.unlink(missing_ok=True)
            raise
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            raise BackupError(f"External backup download or verification failed ({type(exc).__name__})") from exc

    def list_backups(self) -> list[dict]:
        prefix = f"{self.prefix}/" if self.prefix else ""
        continuation = None
        items = []
        try:
            while True:
                args = {"Bucket": self.bucket, "Prefix": prefix}
                if continuation:
                    args["ContinuationToken"] = continuation
                response = self.client.list_objects_v2(**args)
                for item in response.get("Contents", []):
                    key = str(item.get("Key", ""))
                    if key.endswith(".sqlite3"):
                        items.append({
                            "key": key,
                            "size_bytes": int(item.get("Size", 0)),
                            "last_modified": (
                                item["LastModified"].astimezone(timezone.utc).isoformat()
                                if item.get("LastModified") else None
                            ),
                        })
                if not response.get("IsTruncated"):
                    break
                continuation = response.get("NextContinuationToken")
                if not continuation:
                    raise BackupError("S3 listing is truncated without a continuation token")
        except BackupError:
            raise
        except Exception as exc:
            raise BackupError(f"Unable to list external backups ({type(exc).__name__})") from exc
        return sorted(items, key=lambda item: (item["last_modified"] or "", item["key"]), reverse=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect and restore verified off-host SQLite backups")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List remote SQLite snapshots")
    download_parser = commands.add_parser("download", help="Download and verify a restore candidate")
    download_parser.add_argument("--key", required=True)
    download_parser.add_argument("--destination", required=True)
    args = parser.parse_args()

    store = S3BackupStore.from_settings()
    if args.command == "list":
        print(json.dumps(store.list_backups(), indent=2))
        return 0
    result = store.download(args.key, args.destination)
    print(f"Verified restore candidate downloaded: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
