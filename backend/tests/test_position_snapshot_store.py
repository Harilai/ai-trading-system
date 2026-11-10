from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from app.accounts.position_snapshot_store import PositionSnapshotError, PositionSnapshotStore


def position(**overrides):
    values = {
        "instrument_id": "ccxt:kraken:spot:BTC/EUR",
        "symbol": "BTC/EUR",
        "asset_class": "crypto",
        "market_type": "spot",
        "quantity": "0.25",
        "side": "long",
        "quote_currency": "EUR",
        "average_entry_price": "60000",
        "mark_price": "61000",
        "market_value": "15250",
        "unrealized_pnl": "250",
    }
    values.update(overrides)
    return values


def snapshot(*, positions=None, coverage_status="complete", fetched_at=None, environment="production", account_alias="kraken-readonly"):
    return {
        "provider_id": "ccxt:kraken",
        "account_alias": account_alias,
        "environment": environment,
        "market_type": "spot",
        "read_only": True,
        "execution_enabled": False,
        "withdrawals_enabled": False,
        "coverage_status": coverage_status,
        "fetched_at": (fetched_at or datetime.now(timezone.utc)).isoformat(),
        "positions": positions if positions is not None else [position()],
    }


def test_position_snapshot_store_persists_exact_decimal_values_and_hash(tmp_path):
    path = str(tmp_path / "accounts.db")
    store = PositionSnapshotStore(path)
    saved = store.record_snapshot(snapshot(positions=[position(
        quantity="0.0000000000000000123",
        unrealized_pnl="-0.0000000000000000007",
    )]))
    assert saved["reconciliation_status"] == "initial"
    assert saved["positions"][0]["quantity"] == "0.0000000000000000123"
    assert saved["positions"][0]["unrealized_pnl"] == "-0.0000000000000000007"
    assert saved["content_hash"]
    assert PositionSnapshotStore(path).get_snapshot(saved["snapshot_id"])["content_hash"] == saved["content_hash"]


def test_complete_snapshot_marks_omitted_position_absent_without_zeroing_quantity(tmp_path):
    store = PositionSnapshotStore(str(tmp_path / "accounts.db"))
    first = store.record_snapshot(snapshot(positions=[position()]))
    second = store.record_snapshot(snapshot(
        positions=[],
        fetched_at=datetime.now(timezone.utc) + timedelta(milliseconds=1),
    ))
    assert second["previous_snapshot_id"] == first["snapshot_id"]
    assert second["changes"][0]["state"] == "absent"
    assert second["changes"][0]["previous_quantity"] == "0.25"
    assert second["changes"][0]["current_quantity"] is None
    assert second["changes"][0]["delta_quantity"] is None


def test_partial_snapshot_never_interprets_omitted_position_as_closed(tmp_path):
    store = PositionSnapshotStore(str(tmp_path / "accounts.db"))
    store.record_snapshot(snapshot(positions=[position()]))
    partial = store.record_snapshot(snapshot(
        positions=[],
        coverage_status="partial",
        fetched_at=datetime.now(timezone.utc) + timedelta(milliseconds=1),
    ))
    assert partial["reconciliation_status"] == "partial"
    assert partial["change_count"] == 0
    assert partial["changes"][0]["state"] == "not_observed"
    assert partial["changes"][0]["previous_quantity"] == "0.25"
    assert partial["changes"][0]["current_quantity"] is None


def test_position_snapshot_store_separates_accounts_and_environments(tmp_path):
    store = PositionSnapshotStore(str(tmp_path / "accounts.db"))
    first = store.record_snapshot(snapshot())
    other_account = store.record_snapshot(snapshot(account_alias="kraken-secondary"))
    demo = store.record_snapshot(snapshot(environment="demo"))
    assert first["previous_snapshot_id"] is None
    assert other_account["previous_snapshot_id"] is None
    assert demo["previous_snapshot_id"] is None


def test_position_snapshot_store_rejects_duplicate_instruments_and_float_amounts(tmp_path):
    store = PositionSnapshotStore(str(tmp_path / "accounts.db"))
    with pytest.raises(PositionSnapshotError, match="Duplicate instrument"):
        store.record_snapshot(snapshot(positions=[position(), position()]))
    with pytest.raises(PositionSnapshotError, match="decimal string"):
        store.record_snapshot(snapshot(positions=[position(quantity=0.25)]))


def test_position_snapshot_store_requires_readonly_flags_and_coverage(tmp_path):
    store = PositionSnapshotStore(str(tmp_path / "accounts.db"))
    invalid = snapshot()
    invalid["execution_enabled"] = True
    with pytest.raises(PositionSnapshotError, match="execution"):
        store.record_snapshot(invalid)
    invalid = snapshot()
    invalid["coverage_status"] = "unknown"
    with pytest.raises(PositionSnapshotError, match="coverage_status"):
        store.record_snapshot(invalid)


def test_position_snapshot_store_rejects_stale_future_and_out_of_order_data(tmp_path):
    store = PositionSnapshotStore(str(tmp_path / "accounts.db"))
    stale = datetime.now(timezone.utc) - timedelta(minutes=10)
    with pytest.raises(PositionSnapshotError, match="stale"):
        store.record_snapshot(snapshot(fetched_at=stale))
    future = datetime.now(timezone.utc) + timedelta(minutes=5)
    with pytest.raises(PositionSnapshotError, match="future"):
        store.record_snapshot(snapshot(fetched_at=future))
    now = datetime.now(timezone.utc)
    store.record_snapshot(snapshot(fetched_at=now))
    with pytest.raises(PositionSnapshotError, match="older"):
        store.record_snapshot(snapshot(fetched_at=now - timedelta(seconds=1)))


def test_position_snapshot_rows_are_append_only(tmp_path):
    store = PositionSnapshotStore(str(tmp_path / "accounts.db"))
    saved = store.record_snapshot(snapshot())
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with store.connect() as db:
            db.execute(
                "UPDATE account_position_snapshots SET reconciliation_status='changed' WHERE snapshot_id=?",
                (saved["snapshot_id"],),
            )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with store.connect() as db:
            db.execute(
                "DELETE FROM account_position_snapshot_items WHERE snapshot_id=?",
                (saved["snapshot_id"],),
            )


def test_position_snapshot_history_filters_by_account_and_environment(tmp_path):
    store = PositionSnapshotStore(str(tmp_path / "accounts.db"))
    store.record_snapshot(snapshot())
    store.record_snapshot(snapshot(account_alias="kraken-secondary"))
    store.record_snapshot(snapshot(environment="demo"))
    assert len(store.list_snapshots(provider_id="ccxt:kraken", limit=10)) == 3
    assert len(store.list_snapshots(account_alias="kraken-secondary", limit=10)) == 1
    assert len(store.list_snapshots(environment="demo", limit=10)) == 1
    with pytest.raises(PositionSnapshotError, match="limit"):
        store.list_snapshots(limit=0)
