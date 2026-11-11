from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.accounts.order_ledger import (
    AccountOrderLedger,
    AccountOrderLedgerConflict,
    AccountOrderLedgerError,
)


def _order(**updates):
    observed = datetime.now(timezone.utc)
    result = {
        "provider_id": "ccxt:example-demo",
        "account_alias": "paper-test",
        "environment": "demo",
        "market_type": "spot",
        "instrument_id": "example:spot:BTC/USD",
        "symbol": "BTC/USD",
        "side": "buy",
        "order_type": "limit",
        "status": "partially_filled",
        "requested_quantity": "1",
        "filled_quantity": "0.4",
        "remaining_quantity": "0.6",
        "average_fill_price": "100",
        "quote_currency": "USD",
        "observed_at": observed.isoformat(),
        "order_id": "venue-1",
        "client_order_id": "client-1",
    }
    result.update(updates)
    return result


def _fill(**updates):
    result = {
        "provider_id": "ccxt:example-demo",
        "account_alias": "paper-test",
        "environment": "demo",
        "market_type": "spot",
        "instrument_id": "example:spot:BTC/USD",
        "symbol": "BTC/USD",
        "side": "buy",
        "trade_id": "trade-1",
        "amount": "0.4",
        "price": "100",
        "cost": "40",
        "quote_currency": "USD",
        "timestamp_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
        "order_id": "venue-1",
        "client_order_id": "client-1",
        "fee_amount": "0.04",
        "fee_currency": "USD",
    }
    result.update(updates)
    return result


def test_order_observations_are_idempotent_audited_and_persistent(tmp_path):
    path = str(tmp_path / "ledger.sqlite")
    ledger = AccountOrderLedger(path)
    first = ledger.record_order(_order())
    replay = ledger.record_order(_order(**first["order"]))

    assert replay["idempotent_replay"] is True
    assert len(ledger.list_events(first["order_key"])) == 1

    newer = _order(
        observed_at=(datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
        status="filled", filled_quantity="1", remaining_quantity="0",
        average_fill_price="101",
    )
    ledger.record_order(newer)
    reopened = AccountOrderLedger(path)
    stored = reopened.get_order(first["order_key"])
    assert stored["status"] == "filled"
    assert stored["filled_quantity"] == "1"
    assert stored["fill_history_complete"] is False
    assert [event["event_type"] for event in reopened.list_events(first["order_key"])] == [
        "order_observed", "order_observed",
    ]


def test_order_rejects_stale_conflicting_and_regressive_observations(tmp_path):
    ledger = AccountOrderLedger(str(tmp_path / "ledger.sqlite"))
    saved = ledger.record_order(_order())
    key = saved["order_key"]

    with pytest.raises(AccountOrderLedgerConflict, match="Stale"):
        ledger.record_order(_order(observed_at="2020-01-01T00:00:00+00:00"))
    with pytest.raises(AccountOrderLedgerConflict, match="Immutable"):
        ledger.record_order(_order(
            observed_at=(datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
            symbol="BTC-USD",
        ))
    with pytest.raises(AccountOrderLedgerConflict, match="regress"):
        ledger.record_order(_order(
            observed_at=(datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat(),
            filled_quantity="0.2", remaining_quantity="0.8",
        ))
    assert len(ledger.list_events(key)) == 1


def test_fills_are_idempotent_fee_aware_and_require_exact_coverage(tmp_path):
    ledger = AccountOrderLedger(str(tmp_path / "ledger.sqlite"))
    order = ledger.record_order(_order())
    key = order["order_key"]

    fill = _fill()
    result = ledger.record_fills(key, [fill], history_complete=True)
    assert result["inserted_fills"] == 1
    assert result["fill_coverage_complete"] is True
    assert ledger.get_order(key)["fill_history_complete"] is True
    replay = ledger.record_fills(key, [fill], history_complete=True)
    assert replay["inserted_fills"] == 0
    assert len(ledger.list_fills(key)) == 1
    assert ledger.list_fills(key)[0]["fee_currency"] == "USD"


def test_fills_reject_scope_side_duplicates_and_incomplete_complete_claims(tmp_path):
    ledger = AccountOrderLedger(str(tmp_path / "ledger.sqlite"))
    order = ledger.record_order(_order())
    key = order["order_key"]

    with pytest.raises(AccountOrderLedgerConflict, match="side"):
        ledger.record_fills(key, [_fill(side="sell")], history_complete=False)
    with pytest.raises(AccountOrderLedgerConflict, match="scope"):
        ledger.record_fills(key, [_fill(environment="production")], history_complete=False)
    with pytest.raises(AccountOrderLedgerConflict, match="does not equal"):
        ledger.record_fills(key, [_fill(amount="0.2")], history_complete=True)

    # The failed complete-history transaction must not leave a partial fill behind.
    assert ledger.list_fills(key) == []
    assert ledger.record_fills(key, [_fill(amount="0.2")], history_complete=False)[
        "fill_coverage_complete"
    ] is False
    with pytest.raises(AccountOrderLedgerConflict, match="exceed"):
        ledger.record_fills(key, [_fill(trade_id="trade-2", amount="0.3")], history_complete=False)
    assert len(ledger.list_fills(key)) == 1


def test_order_and_fill_history_cannot_be_mutated_or_deleted(tmp_path):
    ledger = AccountOrderLedger(str(tmp_path / "ledger.sqlite"))
    order = ledger.record_order(_order())
    key = order["order_key"]
    ledger.record_fills(key, [_fill()], history_complete=True)

    with ledger.connect() as db:
        with pytest.raises(Exception, match="append-only"):
            db.execute("DELETE FROM account_order_events")
        with pytest.raises(Exception, match="append-only"):
            db.execute("UPDATE account_order_fills SET amount='9'")
    assert len(ledger.list_events(key)) == 2
    assert len(ledger.list_fills(key)) == 1


def test_order_limits_and_unknown_keys_fail_closed(tmp_path):
    ledger = AccountOrderLedger(str(tmp_path / "ledger.sqlite"))
    with pytest.raises(AccountOrderLedgerError):
        ledger.list_orders(limit=0)
    with pytest.raises(AccountOrderLedgerError, match="not found"):
        ledger.record_fills("missing", [], history_complete=True)


def test_independent_connections_converge_on_one_order_and_one_fill(tmp_path):
    path = str(tmp_path / "ledger.sqlite")
    observations = [_order() for _ in range(8)]
    # Keep the provider observation identical across workers so this is an
    # idempotency race rather than conflicting timestamp evidence.
    observations = [observations[0]] * len(observations)
    fills = [_fill() for _ in range(8)]
    fills = [fills[0]] * len(fills)

    def store_order(item):
        return AccountOrderLedger(path).record_order(item)

    with ThreadPoolExecutor(max_workers=8) as pool:
        stored = list(pool.map(store_order, observations))
    order_keys = {item["order_key"] for item in stored}
    assert len(order_keys) == 1
    assert sum(not item["idempotent_replay"] for item in stored) == 1
    key = order_keys.pop()

    def store_fill(item):
        return AccountOrderLedger(path).record_fills(key, [item], history_complete=True)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(store_fill, fills))
    assert sum(item["inserted_fills"] for item in results) == 1
    assert len(AccountOrderLedger(path).list_fills(key)) == 1

