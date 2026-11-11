from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from app.accounts.coordinated_snapshot_store import (
    CoordinatedAccountSnapshotStore,
    CoordinatedSnapshotError,
)
from app.accounts.position_snapshot_store import PositionSnapshotStore
from app.accounts.snapshot_store import AccountSnapshotStore


def timestamp(offset_seconds=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


def balance_snapshot(**overrides):
    value = {
        "provider_id": "test:venue",
        "account_alias": "primary",
        "environment": "production",
        "market_type": "spot",
        "fetched_at": timestamp(),
        "timestamp_source": "retrieved_at",
        "coverage_status": "complete",
        "read_only": True,
        "execution_enabled": False,
        "withdrawals_enabled": False,
        "transfers_enabled": False,
        "wallet_signing_enabled": False,
        "balances": [{"asset": "EUR", "free": "100.000", "used": "0", "total": "100"}],
    }
    value.update(overrides)
    return value


def position_snapshot(**overrides):
    value = {
        "provider_id": "test:venue",
        "account_alias": "primary",
        "environment": "production",
        "market_type": "spot",
        "fetched_at": timestamp(),
        "timestamp_source": "retrieved_at",
        "coverage_status": "complete",
        "read_only": True,
        "execution_enabled": False,
        "withdrawals_enabled": False,
        "transfers_enabled": False,
        "wallet_signing_enabled": False,
        "positions": [{
            "instrument_id": "test:spot:BTC/EUR",
            "symbol": "BTC/EUR",
            "asset_class": "crypto",
            "market_type": "spot",
            "quantity": "0.0000000000000000123",
            "side": "long",
            "quote_currency": "EUR",
            "average_entry_price": "60000",
            "mark_price": "61000",
            "market_value": "0.00000000075",
            "unrealized_pnl": "0.00000000001",
        }],
    }
    value.update(overrides)
    return value


@pytest.fixture
def stores(tmp_path):
    database = str(tmp_path / "accounts.db")
    balances = AccountSnapshotStore(database)
    positions = PositionSnapshotStore(database)
    coordinated = CoordinatedAccountSnapshotStore(database, balances, positions)
    return database, balances, positions, coordinated


def test_coordinated_snapshot_links_existing_ledgers_atomically(stores):
    _, balances, positions, coordinated = stores
    result = coordinated.record_snapshot(
        "capture-1", balance_snapshot(), position_snapshot(),
    )
    assert result["capture_status"] == "complete"
    assert result["balances_coverage"] == "complete"
    assert result["positions_coverage"] == "complete"
    assert result["balances"]["snapshot_id"] == result["balance_snapshot_id"]
    assert result["positions"]["snapshot_id"] == result["position_snapshot_id"]
    assert result["positions"]["positions"][0]["quantity"] == "0.0000000000000000123"
    assert balances.get_snapshot(result["balance_snapshot_id"]) is not None
    assert positions.get_snapshot(result["position_snapshot_id"]) is not None


def test_partial_coverage_never_reports_group_complete(stores):
    _, _, _, coordinated = stores
    result = coordinated.record_snapshot(
        "capture-partial",
        balance_snapshot(coverage_status="partial"),
        position_snapshot(coverage_status="complete"),
    )
    assert result["capture_status"] == "partial"


def test_identity_mismatch_is_rejected_before_any_write(stores):
    database, _, _, coordinated = stores
    position = position_snapshot(account_alias="other")
    with pytest.raises(CoordinatedSnapshotError, match="identities"):
        coordinated.record_snapshot("scope-mismatch", balance_snapshot(), position)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM account_snapshots").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM account_position_snapshots").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM coordinated_account_snapshots").fetchone()[0] == 0


def test_timestamp_skew_over_limit_is_rejected(stores):
    database, _, _, coordinated = stores
    balances = balance_snapshot(fetched_at=timestamp(-10))
    positions = position_snapshot(fetched_at=timestamp())
    with pytest.raises(CoordinatedSnapshotError, match="skew"):
        coordinated.record_snapshot("skew", balances, positions, max_skew_seconds=5)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM account_snapshots").fetchone()[0] == 0


def test_unknown_coverage_is_rejected(stores):
    _, _, _, coordinated = stores
    with pytest.raises(CoordinatedSnapshotError, match="coverage"):
        coordinated.record_snapshot(
            "unknown-coverage",
            balance_snapshot(coverage_status="unknown"),
            position_snapshot(),
        )


def test_second_child_failure_rolls_back_first_child_and_group(stores, monkeypatch):
    database, _, positions, coordinated = stores

    def fail_insert(_snapshot, _db):
        raise RuntimeError("injected second-ledger failure")

    monkeypatch.setattr(positions, "_record_snapshot_in_transaction", fail_insert)
    with pytest.raises(RuntimeError, match="injected"):
        coordinated.record_snapshot("rollback", balance_snapshot(), position_snapshot())
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM account_snapshots").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM account_snapshot_balances").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM account_position_snapshots").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM coordinated_account_snapshots").fetchone()[0] == 0


def test_idempotent_replay_returns_existing_capture_without_duplicate_children(stores):
    database, _, _, coordinated = stores
    balances, positions = balance_snapshot(), position_snapshot()
    first = coordinated.record_snapshot("same-request", balances, positions)
    replay = coordinated.record_snapshot("same-request", balances, positions)
    assert replay["capture_id"] == first["capture_id"]
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM account_snapshots").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM account_position_snapshots").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM coordinated_account_snapshots").fetchone()[0] == 1


def test_reusing_request_id_with_different_payload_is_rejected(stores):
    _, _, _, coordinated = stores
    coordinated.record_snapshot("conflict", balance_snapshot(), position_snapshot())
    with pytest.raises(CoordinatedSnapshotError, match="different snapshot data"):
        coordinated.record_snapshot(
            "conflict",
            balance_snapshot(balances=[{"asset": "EUR", "free": "99", "used": "0", "total": "99"}]),
            position_snapshot(),
        )


def test_environment_isolation_in_balance_ledger(stores):
    _, balances, _, _ = stores
    production = balance_snapshot()
    demo = balance_snapshot(environment="demo")
    first = balances.record_snapshot(production)
    second = balances.record_snapshot(demo)
    assert first["previous_snapshot_id"] is None
    assert second["previous_snapshot_id"] is None
    assert first["environment"] == "production"
    assert second["environment"] == "demo"


def test_group_and_child_ledgers_are_append_only(stores):
    database, _, _, coordinated = stores
    result = coordinated.record_snapshot("append-only", balance_snapshot(), position_snapshot())
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with coordinated.connect() as db:
            db.execute(
                "UPDATE coordinated_account_snapshots SET capture_status='partial' WHERE capture_id=?",
                (result["capture_id"],),
            )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with coordinated.connect() as db:
            db.execute(
                "DELETE FROM coordinated_account_snapshots WHERE capture_id=?",
                (result["capture_id"],),
            )
