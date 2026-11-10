import json
from datetime import datetime, timedelta, timezone

from app.backup_healthcheck import check_backup_health


def write_status(directory, *, local_status="created", external_enabled=False, external_status="disabled", completed=None):
    directory.mkdir(parents=True, exist_ok=True)
    status = {
        "last_cycle_at": (completed or datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)).isoformat(),
        "local": {
            "status": local_status,
            "created": ["trading-20261110.sqlite3"] if local_status == "created" else [],
            "failures": [],
        },
        "external": {
            "enabled": external_enabled,
            "status": external_status,
            "uploaded": [],
            "failures": [],
        },
    }
    (directory / "backup_status.json").write_text(json.dumps(status), encoding="utf-8")


def test_backup_healthcheck_accepts_recent_verified_local_cycle(tmp_path):
    now = datetime(2026, 11, 10, 10, 5, tzinfo=timezone.utc)
    write_status(tmp_path, completed=now - timedelta(minutes=5))

    healthy, reason = check_backup_health(tmp_path, interval_seconds=86400, now=now)

    assert healthy is True
    assert "healthy" in reason


def test_backup_healthcheck_rejects_missing_or_invalid_status(tmp_path):
    healthy, reason = check_backup_health(tmp_path, interval_seconds=86400)
    assert healthy is False
    assert "no completed" in reason

    write_status(tmp_path)
    (tmp_path / "backup_status.json").write_text("{invalid", encoding="utf-8")
    healthy, reason = check_backup_health(tmp_path, interval_seconds=86400)
    assert healthy is False
    assert "no completed" in reason


def test_backup_healthcheck_rejects_stale_future_and_naive_timestamps(tmp_path):
    now = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
    write_status(tmp_path, completed=now - timedelta(days=3))
    assert check_backup_health(tmp_path, interval_seconds=86400, now=now)[0] is False

    write_status(tmp_path, completed=now + timedelta(minutes=5))
    healthy, reason = check_backup_health(tmp_path, interval_seconds=86400, now=now)
    assert healthy is False
    assert "future" in reason

    (tmp_path / "backup_status.json").write_text(json.dumps({
        "last_cycle_at": "2026-11-10T10:00:00",
        "local": {"status": "created", "created": ["trading.sqlite3"]},
        "external": {"enabled": False},
    }), encoding="utf-8")
    healthy, reason = check_backup_health(tmp_path, interval_seconds=86400, now=now)
    assert healthy is False
    assert "timezone" in reason


def test_backup_healthcheck_rejects_failed_local_or_enabled_external_replication(tmp_path):
    now = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
    write_status(tmp_path, local_status="failed", completed=now)
    assert check_backup_health(tmp_path, interval_seconds=86400, now=now)[0] is False

    write_status(
        tmp_path,
        external_enabled=True,
        external_status="failed",
        completed=now,
    )
    healthy, reason = check_backup_health(tmp_path, interval_seconds=86400, now=now)
    assert healthy is False
    assert "not verified" in reason


def test_backup_healthcheck_rejects_naive_clock(tmp_path):
    now = datetime(2026, 11, 10, 10, 0)
    write_status(tmp_path, completed=datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc))
    healthy, reason = check_backup_health(tmp_path, interval_seconds=86400, now=now)
    assert healthy is False
    assert "timezone-aware" in reason
