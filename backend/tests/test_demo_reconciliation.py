from app.accounts.binance_demo import BinanceDemoError
from app.accounts.demo_order_store import BinanceDemoOrderStore
from app.demo_reconciliation import BinanceDemoOrderReconciler


def create_open_order(store, client_order_id="periodic-demo-1"):
    store.prepare_intent({
        "client_order_id": client_order_id,
        "symbol": "BTC/USDT",
        "side": "buy",
        "amount": "0.001",
        "price": "20000",
        "quote_asset": "USDT",
    })
    store.transition(client_order_id, "submission_unknown")
    store.transition(
        client_order_id, "open",
        exchange_order_id="exchange-periodic-1",
        exchange_status="NEW",
        filled="0",
        remaining="0.001",
    )


def partial_order_response():
    return {
        "status": "partially_filled",
        "exchange_order_id": "exchange-periodic-1",
        "exchange_status": "PARTIALLY_FILLED",
        "filled": "0.0004",
        "remaining": "0.0006",
        "average": "19999",
        "error": None,
    }


def partial_fill():
    return {
        "trade_id": "periodic-trade-1",
        "exchange_order_id": "exchange-periodic-1",
        "symbol": "BTC/USDT",
        "side": "buy",
        "amount": "0.0004",
        "price": "19999",
        "cost": "7.9996",
        "fee_cost": "0.000004",
        "fee_currency": "BNB",
        "timestamp_ms": 1794312000000,
    }


def test_periodic_reconciler_updates_order_and_persists_fills_without_duplicate_replay(tmp_path):
    store = BinanceDemoOrderStore(str(tmp_path / "periodic-reconcile.db"))
    create_open_order(store)

    class FakeAccount:
        def __init__(self):
            self.order_lookups = 0
            self.fill_lookups = 0

        def fetch_order_by_client_id(self, **kwargs):
            self.order_lookups += 1
            return partial_order_response()

        def fetch_order_fills(self, **kwargs):
            self.fill_lookups += 1
            return {
                "fills": [partial_fill()],
                "complete": True,
                "returned_count": 1,
                "limit": 1000,
            }

    account = FakeAccount()
    reconciler = BinanceDemoOrderReconciler(
        store, account, interval_seconds=15, lease_seconds=30,
    )

    first = reconciler.run_once()
    assert first["claimed"] is True
    assert first["processed"] == 1
    assert first["results"][0]["status"] == "partially_filled"
    assert first["results"][0]["fills"]["accounting_complete"] is True
    assert store.get_order("periodic-demo-1")["status"] == "partially_filled"
    assert len(store.list_fills("periodic-demo-1")) == 1

    not_due = reconciler.run_once()
    assert not_due["claimed"] is False
    assert account.order_lookups == 1
    assert account.fill_lookups == 1

    with store.connect() as db:
        db.execute(
            "UPDATE binance_demo_reconciliation_control SET next_due_at='2000-01-01T00:00:00+00:00' WHERE id=1"
        )
    second = reconciler.run_once()
    assert second["claimed"] is True
    assert second["results"][0]["fills"]["inserted_fills"] == 0
    assert len(store.list_fills("periodic-demo-1")) == 1


def test_periodic_reconciler_recovers_interrupted_intent_without_resubmitting(tmp_path):
    store = BinanceDemoOrderStore(str(tmp_path / "periodic-unknown.db"))
    store.prepare_intent({
        "client_order_id": "periodic-unknown-1",
        "symbol": "BTC/USDT", "side": "buy",
        "amount": "0.001", "price": "20000", "quote_asset": "USDT",
    })

    class MissingOrderAccount:
        def __init__(self):
            self.lookups = 0

        def fetch_order_by_client_id(self, **kwargs):
            self.lookups += 1
            return None

    account = MissingOrderAccount()
    reconciler = BinanceDemoOrderReconciler(
        store, account, interval_seconds=15, lease_seconds=30,
    )
    result = reconciler.run_once()

    assert result["claimed"] is True
    assert store.get_order("periodic-unknown-1")["status"] == "submission_unknown"
    assert account.lookups == 1
    assert not hasattr(account, "submit_limit_order")


def test_periodic_reconciler_quarantines_acknowledged_order_missing_from_exchange(tmp_path):
    store = BinanceDemoOrderStore(str(tmp_path / "periodic-missing.db"))
    create_open_order(store)

    class MissingOrderAccount:
        def fetch_order_by_client_id(self, **kwargs):
            return None

    reconciler = BinanceDemoOrderReconciler(
        store, MissingOrderAccount(), interval_seconds=15, lease_seconds=30,
    )
    result = reconciler.run_once()

    assert result["results"][0]["status"] == "reconciliation_required"
    assert store.get_order("periodic-demo-1")["status"] == "reconciliation_required"


def test_periodic_reconciler_retries_provider_errors_with_persistent_backoff(tmp_path):
    store = BinanceDemoOrderStore(str(tmp_path / "periodic-retry.db"))
    create_open_order(store)

    class FailingAccount:
        def fetch_order_by_client_id(self, **kwargs):
            raise BinanceDemoError("Demo order status request failed (NetworkError)")

    reconciler = BinanceDemoOrderReconciler(
        store, FailingAccount(), interval_seconds=15, lease_seconds=30,
    )
    result = reconciler.run_once()
    assert result["failed"] is True
    assert store.get_order("periodic-demo-1")["status"] == "open"
    assert store.claim_reconciliation_lease(interval_seconds=15, lease_seconds=30) is None

    with store.connect() as db:
        row = db.execute(
            "SELECT next_due_at,attempts FROM binance_demo_reconciliation_control WHERE id=1"
        ).fetchone()
    assert row["attempts"] == 1
    assert row["next_due_at"]


def test_periodic_reconciler_quarantines_unexpected_state_after_partial_fill(tmp_path):
    store = BinanceDemoOrderStore(str(tmp_path / "periodic-unexpected-state.db"))
    store.prepare_intent({
        "client_order_id": "periodic-unexpected-1",
        "symbol": "BTC/USDT", "side": "buy",
        "amount": "0.001", "price": "20000", "quote_asset": "USDT",
    })
    store.transition("periodic-unexpected-1", "submission_unknown")
    store.transition(
        "periodic-unexpected-1", "partially_filled",
        exchange_order_id="exchange-unexpected-1",
        exchange_status="PARTIALLY_FILLED",
        filled="0.0004", remaining="0.0006", average="20000",
    )

    class UnexpectedAccount:
        def fetch_order_by_client_id(self, **kwargs):
            return {
                "status": "rejected",
                "exchange_order_id": "exchange-unexpected-1",
                "exchange_status": "REJECTED",
                "filled": "0.0004",
                "remaining": "0.0006",
                "average": "20000",
                "error": None,
            }

    reconciler = BinanceDemoOrderReconciler(
        store, UnexpectedAccount(), interval_seconds=15, lease_seconds=30,
    )
    result = reconciler.run_once()

    assert result["results"][0]["status"] == "reconciliation_required"
    assert store.get_order("periodic-unexpected-1")["status"] == "reconciliation_required"


def test_periodic_reconciler_continues_batch_after_one_order_lookup_fails(tmp_path):
    store = BinanceDemoOrderStore(str(tmp_path / "periodic-batch-isolation.db"))
    create_open_order(store, "periodic-batch-open")
    # Simulate a second terminal legacy order from an older deployment.
    # Current order creation intentionally blocks this state until the first
    # order is reconciled, but the periodic worker must safely drain backlog.
    import json
    from datetime import datetime, timezone

    legacy_request = {
        "client_order_id": "periodic-batch-filled",
        "symbol": "ETH/USDT", "side": "buy",
        "amount": "0.01", "price": "2000", "quote_asset": "USDT",
    }
    canonical = store._canonical_request(legacy_request)
    stamp = datetime.now(timezone.utc).isoformat()
    with store.connect() as db:
        db.execute(
            """
            INSERT INTO binance_demo_orders(
                client_order_id,request_json,symbol,side,amount,price,quote_asset,
                status,exchange_order_id,exchange_status,filled,remaining,average,
                error,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,'filled',?,'FILLED','0.01','0','2000',NULL,?,?)
            """,
            (
                canonical["client_order_id"],
                json.dumps(canonical, sort_keys=True, separators=(",", ":")),
                canonical["symbol"], canonical["side"], canonical["amount"],
                canonical["price"], canonical["quote_asset"],
                "exchange-periodic-filled", stamp, stamp,
            ),
        )

    class PartiallyFailingAccount:
        def fetch_order_by_client_id(self, **kwargs):
            raise BinanceDemoError("temporary status lookup failure")

        def fetch_order_fills(self, **kwargs):
            return {
                "fills": [{
                    "trade_id": "filled-batch-trade",
                    "exchange_order_id": "exchange-periodic-filled",
                    "symbol": "ETH/USDT", "side": "buy",
                    "amount": "0.01", "price": "2000", "cost": "20",
                    "fee_cost": "0", "fee_currency": "USDT",
                    "timestamp_ms": 1794312000000,
                }],
                "complete": True, "returned_count": 1, "limit": 1000,
            }

    reconciler = BinanceDemoOrderReconciler(
        store, PartiallyFailingAccount(),
        interval_seconds=15, lease_seconds=30, max_orders_per_cycle=2,
    )
    result = reconciler.run_once()

    assert result["claimed"] is True
    assert result["processed"] == 2
    assert result["failed"] is True
    assert store.get_order("periodic-batch-open")["status"] == "open"
    assert len(store.list_fills("periodic-batch-filled")) == 1
