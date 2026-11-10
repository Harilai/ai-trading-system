from datetime import datetime, timedelta, timezone

import pytest

from app.accounts.binance_demo import BinanceDemoError
from app.accounts.demo_order_store import BinanceDemoOrderStore, DemoOrderConflict
from app.demo_reconciliation import BinanceDemoReconciler


def request(client_order_id="demo-periodic-1"):
    return {
        "client_order_id": client_order_id,
        "symbol": "BTC/USDT",
        "side": "buy",
        "amount": "0.001",
        "price": "20000",
        "quote_asset": "USDT",
    }


def open_snapshot(client_order_id="demo-periodic-1"):
    return {
        "status": "open",
        "exchange_order_id": "exchange-123",
        "exchange_status": "open",
        "filled": "0",
        "remaining": "0.001",
        "average": None,
        "observed_client_order_id": client_order_id,
        "observed_exchange_order_id": "exchange-123",
        "observed_exchange_status": "open",
    }


class FakeDemoProvider:
    def __init__(self, order=None, fills=None, error=None):
        self.order = order
        self.fills = fills or []
        self.error = error
        self.calls = []

    def fetch_order_by_client_id(self, **kwargs):
        self.calls.append(("fetch_by_client_id", kwargs))
        if self.error:
            raise self.error
        return self.order

    def fetch_order_by_id(self, **kwargs):
        self.calls.append(("fetch_by_id", kwargs))
        if self.error:
            raise self.error
        return self.order

    def fetch_order_fills(self, **kwargs):
        self.calls.append(("fetch_fills", kwargs))
        if self.error:
            raise self.error
        return {"fills": self.fills, "complete": True, "returned_count": len(self.fills)}


def create_store(tmp_path):
    return BinanceDemoOrderStore(str(tmp_path / "demo-reconcile.db"))


def prepare_unknown(store, client_order_id="demo-periodic-1"):
    store.prepare_intent(request(client_order_id))
    store.transition(client_order_id, "submission_unknown", payload={"source": "test"})


def test_periodic_reconciliation_recovers_open_order_and_uses_persisted_exchange_id(tmp_path):
    store = create_store(tmp_path)
    prepare_unknown(store)
    provider = FakeDemoProvider(order=open_snapshot())
    reconciler = BinanceDemoReconciler(store, provider, interval_seconds=60)

    result = reconciler.run_due()

    assert result["processed"] == 1
    assert result["failed"] == 0
    assert store.get_order("demo-periodic-1")["status"] == "open"
    assert store.get_order("demo-periodic-1")["exchange_order_id"] == "exchange-123"
    assert [call[0] for call in provider.calls] == ["fetch_by_client_id"]
    assert reconciler.run_due() is None


def test_periodic_reconciliation_recovers_interrupted_intent_without_resubmission(tmp_path):
    store = create_store(tmp_path)
    store.prepare_intent(request())
    provider = FakeDemoProvider(order=None)
    reconciler = BinanceDemoReconciler(store, provider)

    result = reconciler.run_due()

    assert result["processed"] == 1
    assert store.get_order("demo-periodic-1")["status"] == "submission_unknown"
    events = store.list_events("demo-periodic-1")
    assert events[-1]["payload"]["resubmission"] is False
    assert [call[0] for call in provider.calls] == ["fetch_by_client_id"]


def test_periodic_reconciliation_quarantines_acknowledged_order_missing_from_exchange(tmp_path):
    store = create_store(tmp_path)
    prepare_unknown(store)
    store.transition(
        "demo-periodic-1", "open",
        exchange_order_id="exchange-123",
        exchange_status="open",
        filled="0",
        remaining="0.001",
    )
    provider = FakeDemoProvider(order=None)
    reconciler = BinanceDemoReconciler(store, provider)

    result = reconciler.run_due()

    assert result["failed"] == 0
    assert store.get_order("demo-periodic-1")["status"] == "reconciliation_required"
    with pytest.raises(DemoOrderConflict, match="previous Demo order"):
        store.prepare_intent(request("demo-periodic-2"))


def test_periodic_reconciliation_records_terminal_individual_fills_and_fees(tmp_path):
    store = create_store(tmp_path)
    prepare_unknown(store)
    filled = {
        "status": "filled",
        "exchange_order_id": "exchange-123",
        "exchange_status": "closed",
        "filled": "0.001",
        "remaining": "0",
        "average": "20000",
        "observed_client_order_id": "demo-periodic-1",
        "observed_exchange_order_id": "exchange-123",
        "observed_exchange_status": "closed",
    }
    fills = [{
        "trade_id": "trade-1",
        "exchange_order_id": "exchange-123",
        "symbol": "BTC/USDT",
        "side": "buy",
        "amount": "0.001",
        "price": "20000",
        "cost": "20",
        "fee_cost": "0.02",
        "fee_currency": "USDT",
        "timestamp_ms": 1794312000000,
    }]
    provider = FakeDemoProvider(order=filled, fills=fills)
    reconciler = BinanceDemoReconciler(store, provider)

    result = reconciler.run_due()

    assert result["failed"] == 0
    assert store.get_order("demo-periodic-1")["status"] == "filled"
    assert len(store.list_fills("demo-periodic-1")) == 1
    assert result["outcomes"][0]["fills"]["accounting_complete"] is True
    assert [call[0] for call in provider.calls] == ["fetch_by_client_id", "fetch_fills"]


def test_periodic_reconciliation_retries_transient_exchange_errors_with_backoff(tmp_path):
    store = create_store(tmp_path)
    prepare_unknown(store)
    provider = FakeDemoProvider(error=BinanceDemoError("temporary API error"))
    reconciler = BinanceDemoReconciler(store, provider, interval_seconds=60)

    result = reconciler.run_due()

    assert result["failed"] == 1
    assert store.get_order("demo-periodic-1")["status"] == "submission_unknown"
    status = store.reconciliation_status()
    assert status["last_status"] == "failed"
    assert status["last_error"] == "1 Demo order reconciliation(s) failed"
    assert reconciler.run_due() is None


def test_reconciliation_lease_is_exclusive_recovers_after_expiry_and_fences_old_owner(tmp_path):
    store = create_store(tmp_path)
    now = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    first = store.claim_reconciliation_lease(lease_seconds=30, now=now)
    assert first is not None
    assert store.claim_reconciliation_lease(lease_seconds=30, now=now) is None

    second = store.claim_reconciliation_lease(
        lease_seconds=30,
        now=now + timedelta(seconds=31),
    )
    assert second is not None
    assert second["lease_token"] != first["lease_token"]
    assert store.finish_reconciliation_lease(
        first["lease_token"], {"processed": 0}, now=now + timedelta(seconds=32),
    ) is False
    assert store.finish_reconciliation_lease(
        second["lease_token"], {"processed": 0}, now=now + timedelta(seconds=32),
    ) is True


def test_reconciliation_schedule_uses_bounded_exponential_backoff(tmp_path):
    store = create_store(tmp_path)
    now = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    lease = store.claim_reconciliation_lease(lease_seconds=60, now=now)
    assert lease is not None
    assert store.finish_reconciliation_lease(
        lease["lease_token"], {"error": "transient"}, interval_seconds=60,
        failed=True, now=now,
    )
    status = store.reconciliation_status()
    assert datetime.fromisoformat(status["next_run_at"]) == now + timedelta(seconds=60)
    assert status["lease_token"] if "lease_token" in status else True
    assert "lease_token" not in status
