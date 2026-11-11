from datetime import datetime, timezone, timedelta
import sqlite3

import pytest

from app.accounts.coordinated_cash_snapshot_store import (
    CoordinatedCashSnapshotError, CoordinatedCashSnapshotStore,
)
from app.accounts.financial_metrics_store import AccountFinancialMetricsStore
from app.accounts.position_snapshot_store import PositionSnapshotStore


def stamp(offset=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()


def financial(**overrides):
    value = {
        "provider_id": "alpaca:paper",
        "account_alias": "alpaca-paper-readonly",
        "environment": "paper",
        "account_id": "paper-account-123",
        "currency": "USD",
        "account_status": "ACTIVE",
        "fetched_at": stamp(),
        "cash": "1000",
        "equity": "1250.50",
        "buying_power": "2000",
        "trading_blocked": False,
        "transfers_blocked": False,
        "account_blocked": False,
    }
    value.update(overrides)
    return value


def cash_positions(**overrides):
    value = {
        "provider_id": "alpaca:paper",
        "account_alias": "alpaca-paper-readonly",
        "environment": "paper",
        "market_type": "cash",
        "fetched_at": stamp(),
        "read_only": True,
        "execution_enabled": False,
        "withdrawals_enabled": False,
        "coverage_status": "complete",
        "positions": [{
            "instrument_id": "alpaca:asset:abc",
            "symbol": "ABC",
            "asset_class": "equity",
            "market_type": "cash",
            "quantity": "2.5",
            "side": "long",
            "quote_currency": "USD",
            "average_entry_price": "10",
            "mark_price": "12",
            "market_value": "30",
            "unrealized_pnl": "5",
        }],
    }
    value.update(overrides)
    return value


@pytest.fixture
def stores(tmp_path):
    path = str(tmp_path / "cash.db")
    metrics = AccountFinancialMetricsStore(path)
    positions = PositionSnapshotStore(path)
    coordinated = CoordinatedCashSnapshotStore(path, metrics, positions)
    return path, metrics, positions, coordinated


def test_cash_capture_atomically_links_typed_metrics_and_positions(stores):
    _, metrics, positions, coordinated = stores
    result = coordinated.record_snapshot("cash-1", financial(), cash_positions())
    assert result["capture_status"] == "complete"
    assert result["market_type"] == "cash"
    assert result["financial_metrics"]["metrics"]["cash"] == "1000"
    assert result["financial_metrics"]["metrics"]["equity"] == "1250.5"
    assert result["financial_metrics"]["metrics"]["buying_power"] == "2000"
    assert result["positions"]["positions"][0]["quantity"] == "2.5"
    assert metrics.get_snapshot_by_id(result["financial_metrics_snapshot_id"]) is not None
    assert positions.get_snapshot(result["position_snapshot_id"]) is not None


def test_cash_capture_rejects_scope_mismatch_before_writes(stores):
    path, _, _, coordinated = stores
    with pytest.raises(CoordinatedCashSnapshotError, match="identity/scope"):
        coordinated.record_snapshot(
            "scope", financial(), cash_positions(account_alias="another"),
        )
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM account_financial_metric_snapshots").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM account_position_snapshots").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM coordinated_cash_account_snapshots").fetchone()[0] == 0


def test_cash_capture_rejects_excessive_skew(stores):
    path, _, _, coordinated = stores
    with pytest.raises(CoordinatedCashSnapshotError, match="skew"):
        coordinated.record_snapshot(
            "skew", financial(fetched_at=stamp(-10)), cash_positions(),
        )
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM account_financial_metric_snapshots").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM account_position_snapshots").fetchone()[0] == 0


def test_cash_capture_rolls_back_both_ledgers_when_second_insert_fails(stores, monkeypatch):
    path, _, positions, coordinated = stores

    def fail(_snapshot, _db):
        raise RuntimeError("injected position write failure")

    monkeypatch.setattr(positions, "_record_snapshot_in_transaction", fail)
    with pytest.raises(RuntimeError, match="injected"):
        coordinated.record_snapshot("rollback", financial(), cash_positions())
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM account_financial_metric_snapshots").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM account_position_snapshots").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM coordinated_cash_account_snapshots").fetchone()[0] == 0


def test_cash_capture_is_idempotent_and_append_only(stores):
    _, _, _, coordinated = stores
    metrics, positions = financial(), cash_positions()
    first = coordinated.record_snapshot("same", metrics, positions)
    replay = coordinated.record_snapshot("same", metrics, positions)
    assert replay["capture_id"] == first["capture_id"]
    with coordinated.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM account_financial_metric_snapshots").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM account_position_snapshots").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM coordinated_cash_account_snapshots").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute(
                "UPDATE coordinated_cash_account_snapshots SET capture_status='partial' WHERE capture_id=?",
                (first["capture_id"],),
            )
