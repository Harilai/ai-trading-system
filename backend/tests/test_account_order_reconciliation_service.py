from datetime import datetime, timedelta, timezone

import pytest

from app.accounts.order_ledger import AccountOrderLedger
from app.accounts.reconciliation import (
    AccountOrderReconciliationError,
    AccountOrderReconciliationService,
)


def _order(**updates):
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
        "observed_at": datetime.now(timezone.utc).isoformat(),
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


class FakeProvider:
    def __init__(self, order, history=None):
        self.order = order
        self.history = history
        self.order_calls = []
        self.fill_calls = []

    def fetch_order(self, order_id, instrument_id):
        self.order_calls.append((order_id, instrument_id))
        return self.order

    def fetch_fills(self, order_id, instrument_id):
        self.fill_calls.append((order_id, instrument_id))
        return self.history


def _history(**updates):
    result = {
        "provider_id": "ccxt:example-demo",
        "account_alias": "paper-test",
        "environment": "demo",
        "market_type": "spot",
        "instrument_id": "example:spot:BTC/USD",
        "order_id": "venue-1",
        "client_order_id": "client-1",
        "observed_at": (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat(),
        "history_complete": False,
        "fills": [_fill()],
    }
    result.update(updates)
    return result


def test_service_records_verified_order_and_incomplete_fills(tmp_path):
    ledger = AccountOrderLedger(str(tmp_path / "ledger.sqlite"))
    saved = ledger.record_order(_order())
    provider = FakeProvider(_order(
        observed_at=(datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
    ), _history())

    result = AccountOrderReconciliationService(provider, ledger).reconcile_order(saved["order_key"])

    assert result["status"] == "partially_filled"
    assert result["updated"] is True
    assert result["fills"]["inserted_fills"] == 1
    assert result["fills"]["fill_coverage_complete"] is False
    assert ledger.get_order(saved["order_key"])["fill_history_complete"] is False
    assert provider.order_calls == [("venue-1", "example:spot:BTC/USD")]
    assert provider.fill_calls == [("venue-1", "example:spot:BTC/USD")]


def test_service_marks_fill_coverage_only_from_explicit_complete_history(tmp_path):
    ledger = AccountOrderLedger(str(tmp_path / "ledger.sqlite"))
    saved = ledger.record_order(_order())
    provider = FakeProvider(_order(
        observed_at=(datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
    ), _history(history_complete=True))

    result = AccountOrderReconciliationService(provider, ledger).reconcile_order(saved["order_key"])

    assert result["fills"]["fill_coverage_complete"] is True
    assert ledger.get_order(saved["order_key"])["fill_history_complete"] is True


def test_missing_order_does_not_infer_cancellation_or_query_fills(tmp_path):
    ledger = AccountOrderLedger(str(tmp_path / "ledger.sqlite"))
    saved = ledger.record_order(_order())
    provider = FakeProvider(None, _history())

    result = AccountOrderReconciliationService(provider, ledger).reconcile_order(saved["order_key"])

    assert result["reason"] == "provider_order_not_found"
    assert result["updated"] is False
    assert ledger.get_order(saved["order_key"])["status"] == "partially_filled"
    assert len(ledger.list_events(saved["order_key"])) == 1
    assert provider.fill_calls == []


@pytest.mark.parametrize("updates", [
    {"order_id": "other-venue-order"},
    {"provider_id": "other-provider"},
    {"side": "sell"},
])
def test_service_rejects_discordant_order_evidence_without_persisting_it(tmp_path, updates):
    ledger = AccountOrderLedger(str(tmp_path / "ledger.sqlite"))
    saved = ledger.record_order(_order())
    bad = _order(
        observed_at=(datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
        **updates,
    )
    provider = FakeProvider(bad, _history())

    with pytest.raises(AccountOrderReconciliationError):
        AccountOrderReconciliationService(provider, ledger).reconcile_order(saved["order_key"])
    assert len(ledger.list_events(saved["order_key"])) == 1
    assert provider.fill_calls == []


def test_service_does_not_lookup_an_order_without_exchange_id(tmp_path):
    ledger = AccountOrderLedger(str(tmp_path / "ledger.sqlite"))
    saved = ledger.record_order(_order(order_id=None))
    provider = FakeProvider(_order(), _history())

    result = AccountOrderReconciliationService(provider, ledger).reconcile_order(saved["order_key"])

    assert result["reason"] == "exchange_order_id_missing"
    assert provider.order_calls == []
    assert provider.fill_calls == []


def test_service_rejects_unknown_local_order(tmp_path):
    service = AccountOrderReconciliationService(FakeProvider(None), AccountOrderLedger(str(tmp_path / "ledger.sqlite")))
    with pytest.raises(AccountOrderReconciliationError, match="does not exist"):
        service.reconcile_order("missing")

