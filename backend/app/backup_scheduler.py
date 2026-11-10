from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import tempfile
import time

from .backup import BackupError, backup_sqlite
from .backup_remote import S3BackupStore
from .config import settings


logger = logging.getLogger("sqlite-backup")


def _read_status(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_status(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        mode="w",
        encoding="utf-8",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def read_backup_status(backup_directory: str | Path) -> dict:
    path = Path(backup_directory).expanduser().resolve() / "backup_status.json"
    status = _read_status(path)
    return status or {
        "last_cycle_at": None,
        "local": {"status": "not_run", "created": [], "failures": []},
        "external": {"enabled": False, "status": "not_run", "uploaded": [], "failures": []},
    }


def run_backup_cycle(
    database_paths: list[str | Path],
    backup_directory: str | Path,
    *,
    retention_days: int = 14,
    now: datetime | None = None,
    external_store: S3BackupStore | None = None,
    external_enabled: bool = False,
    external_error: str | None = None,
) -> list[Path]:
    if retention_days < 1:
        raise ValueError("retention_days must be positive")
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = timestamp.strftime("%Y%m%dT%H%M%SZ")
    destination_dir = Path(backup_directory).expanduser().resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    status_path = destination_dir / "backup_status.json"
    previous_status = _read_status(status_path)
    previous_external = previous_status.get("external", {})
    previous_sources = previous_external.get("uploaded_sources", {})
    if not isinstance(previous_sources, dict):
        previous_sources = {}

    created: list[Path] = []
    local_failures: list[dict] = []
    for database_path in database_paths:
        source = Path(database_path).expanduser().resolve()
        if not source.is_file():
            logger.info("Skipping backup source that does not exist: %s", source)
            continue
        destination = destination_dir / f"{source.stem}-{stamp}.sqlite3"
        try:
            created.append(backup_sqlite(source, destination))
            logger.info("Verified SQLite backup created: %s", destination)
        except BackupError as exc:
            local_failures.append({"source": source.name, "error": str(exc)})
            logger.exception("SQLite backup failed for %s", source)

    cutoff = timestamp - timedelta(days=retention_days)
    for old_backup in destination_dir.glob("*.sqlite3"):
        try:
            modified = datetime.fromtimestamp(old_backup.stat().st_mtime, timezone.utc)
            if modified < cutoff:
                old_backup.unlink()
                logger.info("Expired SQLite backup removed: %s", old_backup)
        except OSError:
            logger.exception("Unable to apply backup retention to %s", old_backup)

    external_uploads: list[dict] = []
    external_failures: list[dict] = []
    uploaded_sources = {
        name: record
        for name, record in previous_sources.items()
        if (destination_dir / name).is_file()
    }
    if external_enabled:
        if external_error:
            external_failures.append({"operation": "configure", "error": external_error})
        elif external_store is None:
            external_failures.append({"operation": "configure", "error": "External store is unavailable"})
        else:
            remote_keys: set[str] = set()
            try:
                remote_keys = {item["key"] for item in external_store.list_backups()}
            except BackupError as exc:
                external_failures.append({"operation": "list", "error": str(exc)})
                logger.exception("Unable to list existing external backups")

            for local_backup in sorted(destination_dir.glob("*.sqlite3")):
                try:
                    stat = local_backup.stat()
                    previous = uploaded_sources.get(local_backup.name)
                    if (
                        previous
                        and int(previous.get("size_bytes", -1)) == stat.st_size
                        and int(previous.get("mtime_ns", -1)) == stat.st_mtime_ns
                        and previous.get("key") in remote_keys
                    ):
                        continue
                    uploaded = external_store.upload(local_backup, created_at=timestamp)
                    uploaded_sources[local_backup.name] = {
                        "key": uploaded["key"],
                        "sha256": uploaded["sha256"],
                        "size_bytes": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "verified_at": timestamp.isoformat(),
                    }
                    external_uploads.append(uploaded)
                    remote_keys.add(uploaded["key"])
                    logger.info(
                        "Off-host backup verified: key=%s size=%s sha256=%s",
                        uploaded["key"], uploaded["size_bytes"], uploaded["sha256"],
                    )
                except (BackupError, OSError, ValueError, TypeError) as exc:
                    external_failures.append({
                        "source": local_backup.name,
                        "error": str(exc),
                    })
                    logger.exception("Off-host backup failed for %s", local_backup)

    external_status = "disabled"
    if external_enabled:
        external_status = "failed" if external_failures else "verified"
    status = {
        "last_cycle_at": timestamp.isoformat(),
        "local": {
            "status": "failed" if local_failures else ("created" if created else "no_new_sources"),
            "created": [path.name for path in created],
            "failures": local_failures,
            "retention_days": retention_days,
        },
        "external": {
            "enabled": external_enabled,
            "status": external_status,
            "uploaded": external_uploads,
            "failures": external_failures,
            "uploaded_sources": uploaded_sources,
            "last_verified_at": timestamp.isoformat() if external_enabled and not external_failures else previous_external.get("last_verified_at"),
        },
    }
    try:
        _write_status(status_path, status)
    except OSError:
        logger.exception("Unable to persist backup cycle status at %s", status_path)
    return created


def run_forever() -> None:
    database_paths = [
        Path(settings.database_path).expanduser().resolve(),
        Path(settings.twelve_data_state_path).expanduser().resolve(),
    ]
    backup_directory = Path(settings.backup_directory).expanduser().resolve()
    interval = settings.backup_interval_seconds
    retention = settings.backup_retention_days
    external_enabled = settings.backup_external_enabled
    logger.info(
        "Starting scheduled SQLite backups: interval=%ss retention=%sd directory=%s external=%s",
        interval,
        retention,
        backup_directory,
        external_enabled,
    )
    while True:
        external_store = None
        external_error = None
        if external_enabled:
            try:
                external_store = S3BackupStore.from_settings(settings)
            except BackupError as exc:
                external_error = str(exc)
                logger.exception("External backup configuration failed; local backups will continue")
        try:
            run_backup_cycle(
                database_paths,
                backup_directory,
                retention_days=retention,
                external_store=external_store,
                external_enabled=external_enabled,
                external_error=external_error,
            )
        except Exception:
            logger.exception("Unexpected error during SQLite backup cycle")
        time.sleep(interval)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
