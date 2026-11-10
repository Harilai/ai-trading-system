from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from app.accounts.snapshot_store import AccountSnapshotError, AccountSnapshotStore


def make_snapshot(*, balances=None, fetched_at=None, account_alias="binance-spot-readonly"):
    return {
        "provider_id": "ccxt:binance",
        "account_alias": account_alias,
        "market_type": "spot",
        "read_only": True,
        "execution_enabled": False,
        "withdrawals_enabled": False,
        "fetched_at": (fetched_at or datetime.now(timezone.utc)).isoformat(),
        "balances": balances if balances is not None else [
            {"asset": "BTC", "free": "0.1", "used": "0.02", "total": "0.12"},
            {"asset": "EUR", "free": "20", "used": "0", "total": "20"},
        ],
    }


def test_account_snapshot_store_persists_initial_snapshot_and_exact_decimal_balances(tmp_path):
    path = str(tmp_path / "accounts.db")
    store = AccountSnapshotStore(path)
    precise = "0.00000001234567890123456789"

    saved = store.record_snapshot(make_snapshot(balances=[
        {"asset": "BTC", "free": precise, "used": "0.00000000000000000001", "total": precise},
    ]))

    assert saved["reconciliation_status"] == "initial"
    assert saved["previous_snapshot_id"] is None
    assert saved["balance_count"] == 1
    assert saved["balances"][0]["free"] == precise
    assert saved["changes"] == []

    reloaded = AccountSnapshotStore(path)
    persisted = reloaded.get_snapshot(saved["snapshot_id"])
    assert persisted["content_hash"] == saved["content_hash"]
    assert persisted["balances"][0]["used"] == "0.00000000000000000001"


def test_account_snapshot_store_detects_changed_added_and_absent_assets(tmp_path):
    store = AccountSnapshotStore(str(tmp_path / "accounts.db"))
    first = store.record_snapshot(make_snapshot(balances=[
        {"asset": "BTC", "free": "0.1", "used": "0", "total": "0.1"},
        {"asset": "BNB", "free": "2", "used": "0", "total": "2"},
    ]))
    second = store.record_snapshot(make_snapshot(balances=[
        {"asset": "BTC", "free": "0.15", "used": "0", "total": "0.15"},
        {"asset": "ETH", "free": "1", "used": "0", "total": "1"},
    ]))

    assert second["previous_snapshot_id"] == first["snapshot_id"]
    assert second["reconciliation_status"] == "changed"
    assert second["change_count"] == 3
    changes = {item["asset"]: item for item in second["changes"]}
    assert changes["BTC"]["state"] == "changed"
    assert changes["BTC"]["delta_total"] == "0.05"
    assert changes["ETH"]["state"] == "added"
    assert changes["BNB"]["state"] == "absent_from_latest_snapshot"
    assert changes["BNB"]["current_total"] is None
    assert changes["BNB"]["delta_total"] is None


def test_account_snapshot_store_detects_unchanged_balances_and_separates_accounts(tmp_path):
    store = AccountSnapshotStore(str(tmp_path / "accounts.db"))
    first = store.record_snapshot(make_snapshot())
    second = store.record_snapshot(make_snapshot(
        fetched_at=datetime.now(timezone.utc) + timedelta(milliseconds=1),
    ))
    other_account = store.record_snapshot(make_snapshot(account_alias="binance-second-key"))

    assert second["previous_snapshot_id"] == first["snapshot_id"]
    assert second["reconciliation_status"] == "unchanged"
    assert second["change_count"] == 0
    assert all(item["state"] == "unchanged" for item in second["changes"])
    assert other_account["previous_snapshot_id"] is None
    assert other_account["reconciliation_status"] == "initial"


def test_account_snapshot_store_rejects_float_balances_and_malformed_amounts(tmp_path):
    store = AccountSnapshotStore(str(tmp_path / "accounts.db"))
    with pytest.raises(AccountSnapshotError, match="decimal string"):
        store.record_snapshot(make_snapshot(balances=[
            {"asset": "BTC", "free": 0.1, "used": "0", "total": "0.1"},
        ]))
    with pytest.raises(AccountSnapshotError, match="invalid"):
        store.record_snapshot(make_snapshot(balances=[
            {"asset": "BTC", "free": "-1", "used": "0", "total": "-1"},
        ]))
    with pytest.raises(AccountSnapshotError, match="malformed"):
        store.record_snapshot(make_snapshot(balances=[
            {"asset": "BTC", "free": "not-a-number", "used": "0", "total": "0"},
        ]))


def test_account_snapshot_store_rejects_duplicate_or_invalid_assets(tmp_path):
    store = AccountSnapshotStore(str(tmp_path / "accounts.db"))
    duplicate = [
        {"asset": "BTC", "free": "1", "used": "0", "total": "1"},
        {"asset": "BTC", "free": "2", "used": "0", "total": "2"},
    ]
    with pytest.raises(AccountSnapshotError, match="Duplicate"):
        store.record_snapshot(make_snapshot(balances=duplicate))
    with pytest.raises(AccountSnapshotError, match="invalid asset"):
        store.record_snapshot(make_snapshot(balances=[
            {"asset": "BTC/USDT", "free": "1", "used": "0", "total": "1"},
        ]))


def test_account_snapshot_store_requires_timezone_and_rejects_future_timestamps(tmp_path):
    store = AccountSnapshotStore(str(tmp_path / "accounts.db"))
    with pytest.raises(AccountSnapshotError, match="timezone"):
        store.record_snapshot(make_snapshot(fetched_at="2026-11-10T10:00:00"))
    with pytest.raises(AccountSnapshotError, match="future"):
        store.record_snapshot(make_snapshot(
            fetched_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        ))


def test_account_snapshot_store_rejects_out_of_order_observations(tmp_path):
    store = AccountSnapshotStore(str(tmp_path / "accounts.db"))
    now = datetime.now(timezone.utc)
    store.record_snapshot(make_snapshot(fetched_at=now.isoformat()))
    with pytest.raises(AccountSnapshotError, match="older"):
        store.record_snapshot(make_snapshot(fetched_at=(now - timedelta(seconds=1)).isoformat()))


def test_account_snapshot_records_and_balances_are_append_only(tmp_path):
    store = AccountSnapshotStore(str(tmp_path / "accounts.db"))
    saved = store.record_snapshot(make_snapshot())
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with store.connect() as db:
            db.execute(
                "UPDATE account_snapshots SET reconciliation_status='changed' WHERE snapshot_id=?",
                (saved["snapshot_id"],),
            )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with store.connect() as db:
            db.execute(
                "DELETE FROM account_snapshot_balances WHERE snapshot_id=?",
                (saved["snapshot_id"],),
            )


def test_account_snapshot_history_filters_and_limits(tmp_path):
    store = AccountSnapshotStore(str(tmp_path / "accounts.db"))
    store.record_snapshot(make_snapshot())
    store.record_snapshot(make_snapshot(account_alias="binance-secondary"))

    assert len(store.list_snapshots(provider_id="ccxt:binance", limit=10)) == 2
    assert len(store.list_snapshots(account_alias="binance-secondary", limit=10)) == 1
    with pytest.raises(AccountSnapshotError, match="limit"):
        store.list_snapshots(limit=0)


def test_account_snapshot_store_rejects_non_readonly_or_executable_sources(tmp_path):
    store = AccountSnapshotStore(str(tmp_path / "accounts.db"))
    snapshot = make_snapshot()
    snapshot["read_only"] = False
    with pytest.raises(AccountSnapshotError, match="read-only"):
        store.record_snapshot(snapshot)

    snapshot = make_snapshot()
    snapshot["execution_enabled"] = True
    with pytest.raises(AccountSnapshotError, match="execution"):
        store.record_snapshot(snapshot)

    snapshot = make_snapshot()
    snapshot["withdrawals_enabled"] = True
    with pytest.raises(AccountSnapshotError, match="withdrawals"):
        store.record_snapshot(snapshot)


def test_account_snapshot_store_rejects_stale_observations(tmp_path):
    store = AccountSnapshotStore(str(tmp_path / "accounts.db"))
    stale = datetime.now(timezone.utc) - timedelta(minutes=10)
    with pytest.raises(AccountSnapshotError, match="stale"):
        store.record_snapshot(make_snapshot(fetched_at=stale.isoformat()))


def test_account_snapshot_store_canonicalizes_equivalent_decimal_formats(tmp_path):
    store = AccountSnapshotStore(str(tmp_path / "accounts.db"))
    first = store.record_snapshot(make_snapshot(balances=[
        {"asset": "BTC", "free": "1.0000", "used": "0.2300", "total": "1.2300"},
    ]))
    second = store.record_snapshot(make_snapshot(balances=[
        {"asset": "BTC", "free": "1", "used": "0.23", "total": "1.23"},
    ]))

    assert first["balances"][0]["free"] == "1"
    assert first["balances"][0]["total"] == "1.23"
    assert second["reconciliation_status"] == "unchanged"
    assert second["change_count"] == 0
