from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

from .backup_scheduler import read_backup_status
from .config import settings


def check_backup_health(
    backup_directory: str | Path,
    *,
    interval_seconds: int,
    now: datetime | None = None,
) -> tuple[bool, str]:
    if interval_seconds < 60:
        return False, "backup interval is invalid"
    status = read_backup_status(backup_directory)
    last_cycle = status.get("last_cycle_at")
    if not isinstance(last_cycle, str) or not last_cycle:
        return False, "no completed backup cycle has been recorded"
    try:
        completed = datetime.fromisoformat(last_cycle)
    except ValueError:
        return False, "last backup cycle timestamp is invalid"
    if completed.tzinfo is None or completed.utcoffset() is None:
        return False, "last backup cycle timestamp has no timezone"

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        return False, "healthcheck clock must be timezone-aware"
    age = (current.astimezone(timezone.utc) - completed.astimezone(timezone.utc)).total_seconds()
    if age < -60:
        return False, "last backup cycle timestamp is in the future"
    maximum_age = interval_seconds * 2 + 300
    if age > maximum_age:
        return False, f"last backup cycle is stale ({int(age)} seconds)"

    local = status.get("local")
    if not isinstance(local, dict) or local.get("status") != "created":
        return False, "latest local backup cycle did not create a verified snapshot"
    if not local.get("created"):
        return False, "latest local backup cycle contains no verified snapshots"

    external = status.get("external")
    if isinstance(external, dict) and external.get("enabled") is True:
        if external.get("status") != "verified":
            return False, "external backup replication is enabled but not verified"

    return True, "backup scheduler is healthy"


def main() -> int:
    healthy, reason = check_backup_health(
        settings.backup_directory,
        interval_seconds=settings.backup_interval_seconds,
    )
    print(reason)
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
