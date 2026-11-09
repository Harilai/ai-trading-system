from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import time

from .backup import BackupError, backup_sqlite
from .config import settings


logger = logging.getLogger("sqlite-backup")


def run_backup_cycle(
    database_paths: list[str | Path],
    backup_directory: str | Path,
    *,
    retention_days: int = 14,
    now: datetime | None = None,
) -> list[Path]:
    if retention_days < 1:
        raise ValueError("retention_days must be positive")
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = timestamp.strftime("%Y%m%dT%H%M%SZ")
    destination_dir = Path(backup_directory)
    destination_dir.mkdir(parents=True, exist_ok=True)
    created = []

    for database_path in database_paths:
        source = Path(database_path).expanduser().resolve()
        if not source.is_file():
            logger.info("Skipping backup source that does not exist: %s", source)
            continue
        destination = destination_dir / f"{source.stem}-{stamp}.sqlite3"
        try:
            created.append(backup_sqlite(source, destination))
            logger.info("Verified SQLite backup created: %s", destination)
        except BackupError:
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
    return created


def run_forever() -> None:
    database_paths = [
        Path(settings.database_path).expanduser().resolve(),
        Path(settings.twelve_data_state_path).expanduser().resolve(),
    ]
    backup_directory = Path(settings.backup_directory).expanduser().resolve()
    interval = settings.backup_interval_seconds
    retention = settings.backup_retention_days
    logger.info(
        "Starting scheduled SQLite backups: interval=%ss retention=%sd directory=%s",
        interval,
        retention,
        backup_directory,
    )
    while True:
        try:
            run_backup_cycle(
                database_paths,
                backup_directory,
                retention_days=retention,
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
