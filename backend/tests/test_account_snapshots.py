from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from app.accounts.snapshot_store import AccountSnapshotError, AccountSnapshotStore


def make_snapshot(
    *, balances=None, positions=None, fetched_at=None, account_alias="binance-spot-readonly",
    market_type="spot", environment="unspecified", venue_type="unknown",
):
    return {
        "provider_id": "ccxt:binance",
        "account_alias": account_alias,
        "market_type": market_type,
        "environment": environment,
        "venue_type": venue_type,
        "read_only": True,
        "execution_enabled": False,
        "withdrawals_enabled": False,
        "fetched_at": (
            fetched_at
            if isinstance(fetched_at, str)
            else (fetched_at or datetime.now(timezone.utc)).isoformat()
        ),
        "balances": balances if balances is not None else [
            {"asset": "BTC", "free": "0.1", "used": "0.02", "total": "0.12"},
            {"asset": "EUR", "free": "20", "used": "0", "total": "20"},
        ],
        "positions": positions if positions is not None else [],
        "transfers_enabled": False,
        "wallet_signing_enabled": False,
        "permissions_verified": False,
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


def make_position(
    instrument_id="binance:cash_equity:AAPL",
    symbol="AAPL",
    *,
    quantity="2",
    side="long",
    average_entry_price="180",
    mark_price="185",
    market_value="370",
    unrealized_pnl="10",
    market_type="cash_equity",
    asset_class="equity",
    quote_currency="USD",
):
    return {
        "instrument_id": instrument_id,
        "symbol": symbol,
        "asset_class": asset_class,
        "market_type": market_type,
        "quantity": quantity,
        "side": side,
        "quote_currency": quote_currency,
        "average_entry_price": average_entry_price,
        "mark_price": mark_price,
        "market_value": market_value,
        "unrealized_pnl": unrealized_pnl,
    }


def test_account_snapshot_store_persists_positions_for_cash_equity_accounts(tmp_path):
    store = AccountSnapshotStore(str(tmp_path / "accounts.db"))
    saved = store.record_snapshot(make_snapshot(
        market_type="cash_equity",
        environment="production",
        venue_type="broker",
        balances=[{"asset": "USD", "free": "630", "used": "370", "total": "1000"}],
        positions=[make_position()],
    ))

    assert saved["market_type"] == "cash_equity"
    assert saved["environment"] == "production"
    assert saved["venue_type"] == "broker"
    assert saved["position_count"] == 1
    assert saved["position_change_count"] == 0
    assert saved["positions"][0]["instrument_id"] == "binance:cash_equity:AAPL"
    assert saved["positions"][0]["unrealized_pnl"] == "10"
    assert saved["positions"][0]["quantity"] == "2"
    assert saved["position_changes"] == []


def test_account_snapshot_store_reconciles_added_changed_and_removed_positions(tmp_path):
    store = AccountSnapshotStore(str(tmp_path / "accounts.db"))
    first = store.record_snapshot(make_snapshot(
        market_type="cash_equity",
        positions=[make_position(), make_position(
            instrument_id="binance:cash_equity:MSFT", symbol="MSFT", quantity="1",
        )],
    ))
    second = store.record_snapshot(make_snapshot(
        market_type="cash_equity",
        positions=[
            make_position(quantity="3", mark_price="190", market_value="570", unrealized_pnl="-20"),
            make_position(instrument_id="binance:cash_equity:NVDA", symbol="NVDA", quantity="1"),
        ],
    ))

    assert second["previous_snapshot_id"] == first["snapshot_id"]
    assert second["reconciliation_status"] == "changed"
    assert second["position_count"] == 2
    assert second["position_change_count"] == 3
    assert second["change_count"] == 3
    changes = {(item["instrument_id"], item["side"]): item for item in second["position_changes"]}
    assert changes[("binance:cash_equity:AAPL", "long")]["state"] == "changed"
    assert changes[("binance:cash_equity:MSFT", "long")]["state"] == "absent_from_latest_snapshot"
    assert changes[("binance:cash_equity:NVDA", "long")]["state"] == "added"
    assert changes[("binance:cash_equity:AAPL", "long")]["current"]["unrealized_pnl"] == "-20"


def test_account_snapshot_store_keeps_environments_and_market_types_separate(tmp_path):
    store = AccountSnapshotStore(str(tmp_path / "accounts.db"))
    production = store.record_snapshot(make_snapshot(
        market_type="cash_equity", environment="production",
        positions=[make_position()],
    ))
    demo = store.record_snapshot(make_snapshot(
        market_type="cash_equity", environment="demo",
        positions=[make_position(quantity="5")],
    ))
    spot = store.record_snapshot(make_snapshot(market_type="spot", environment="production"))

    assert production["previous_snapshot_id"] is None
    assert demo["previous_snapshot_id"] is None
    assert spot["previous_snapshot_id"] is None
    assert len(store.list_snapshots(environment="production", market_type="cash_equity")) == 1


def test_account_snapshot_store_rejects_invalid_position_identity_quantity_and_market(tmp_path):
    store = AccountSnapshotStore(str(tmp_path / "accounts.db"))
    invalid_positions = [
        make_position(quantity="0"),
        make_position(quantity="-1"),
        make_position(side="buy"),
        make_position(asset_class="unknown"),
        make_position(market_type="spot"),
        make_position(quote_currency="USD/USDT"),
        make_position(instrument_id=""),
    ]
    for position in invalid_positions:
        with pytest.raises(AccountSnapshotError):
            store.record_snapshot(make_snapshot(market_type="cash_equity", positions=[position]))


def test_account_snapshot_store_rejects_duplicate_instrument_side_and_invalid_pnl(tmp_path):
    store = AccountSnapshotStore(str(tmp_path / "accounts.db"))
    with pytest.raises(AccountSnapshotError, match="Duplicate instrument"):
        store.record_snapshot(make_snapshot(
            market_type="cash_equity",
            positions=[make_position(), make_position(quantity="4")],
        ))
    with pytest.raises(AccountSnapshotError, match="P&L"):
        store.record_snapshot(make_snapshot(
            market_type="cash_equity",
            positions=[make_position(unrealized_pnl=10)],
        ))


def test_account_snapshot_positions_are_append_only(tmp_path):
    store = AccountSnapshotStore(str(tmp_path / "accounts.db"))
    saved = store.record_snapshot(make_snapshot(
        market_type="cash_equity", positions=[make_position()],
    ))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with store.connect() as db:
            db.execute(
                "UPDATE account_snapshot_positions SET quantity='999' WHERE snapshot_id=?",
                (saved["snapshot_id"],),
            )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with store.connect() as db:
            db.execute(
                "DELETE FROM account_snapshot_positions WHERE snapshot_id=?",
                (saved["snapshot_id"],),
            )


def test_account_snapshot_store_migrates_existing_balance_only_database(tmp_path):
    path = str(tmp_path / "legacy.db")
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE account_snapshots (
                snapshot_id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, account_alias TEXT NOT NULL,
                market_type TEXT NOT NULL, fetched_at TEXT NOT NULL, recorded_at TEXT NOT NULL,
                previous_snapshot_id TEXT, reconciliation_status TEXT NOT NULL,
                change_count INTEGER NOT NULL, balance_count INTEGER NOT NULL, content_hash TEXT NOT NULL
            );
            CREATE TABLE account_snapshot_balances (
                snapshot_id TEXT NOT NULL, asset TEXT NOT NULL, free TEXT, used TEXT, total TEXT,
                PRIMARY KEY(snapshot_id,asset)
            );
        """)
    store = AccountSnapshotStore(path)
    columns = {row["name"] for row in store.connect().__enter__().execute("PRAGMA table_info(account_snapshots)")}
    assert {"environment", "venue_type", "position_count", "position_change_count"} <= columns
    saved = store.record_snapshot(make_snapshot(market_type="cash_equity", positions=[make_position()]))
    assert saved["position_count"] == 1


def test_account_snapshot_store_rejects_transfer_or_wallet_signing_permission_flags(tmp_path):
    store = AccountSnapshotStore(str(tmp_path / "accounts.db"))
    snapshot = make_snapshot()
    snapshot["transfers_enabled"] = True
    with pytest.raises(AccountSnapshotError, match="transfer"):
        store.record_snapshot(snapshot)
    snapshot = make_snapshot()
    snapshot["wallet_signing_enabled"] = True
    with pytest.raises(AccountSnapshotError, match="signing"):
        store.record_snapshot(snapshot)
