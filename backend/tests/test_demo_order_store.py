import sqlite3

import pytest

from app.accounts.demo_order_store import (
    BinanceDemoOrderStore,
    DemoOrderConflict,
    DemoOrderStoreError,
)


def request(client_order_id="demo-order-1", amount="0.001", price="20000"):
    return {
        "client_order_id": client_order_id,
        "symbol": "BTC/USDT",
        "side": "buy",
        "amount": amount,
        "price": price,
        "quote_asset": "USDT",
    }


def test_demo_order_store_persists_immutable_intent_and_idempotent_replay(tmp_path):
    store = BinanceDemoOrderStore(str(tmp_path / "demo-orders.db"))
    first, created = store.prepare_intent(request())
    replay, replay_created = store.prepare_intent(request())

    assert created is True
    assert replay_created is False
    assert first["status"] == "intent_created"
    assert replay["client_order_id"] == first["client_order_id"]
    assert replay["request"] == first["request"]
    assert store.count_unresolved() == 1
    assert [event["to_status"] for event in store.list_events("demo-order-1")] == ["intent_created"]


def test_demo_order_store_rejects_client_id_reuse_with_different_request(tmp_path):
    store = BinanceDemoOrderStore(str(tmp_path / "demo-orders.db"))
    store.prepare_intent(request())
    with pytest.raises(DemoOrderConflict, match="different immutable intent"):
        store.prepare_intent(request(amount="0.002"))


def test_demo_order_store_serializes_new_orders_while_prior_order_is_unresolved(tmp_path):
    store = BinanceDemoOrderStore(str(tmp_path / "demo-orders.db"))
    store.prepare_intent(request())
    with pytest.raises(DemoOrderConflict, match="previous Demo order"):
        store.prepare_intent(request(client_order_id="demo-order-2"))

    store.transition("demo-order-1", "submission_unknown")
    store.transition(
        "demo-order-1", "open",
        exchange_order_id="exchange-1",
        exchange_status="open",
        filled="0",
        remaining="0.001",
    )
    with pytest.raises(DemoOrderConflict, match="previous Demo order"):
        store.prepare_intent(request(client_order_id="demo-order-2"))

    store.transition(
        "demo-order-1", "filled",
        exchange_status="closed",
        filled="0.001",
        remaining="0",
        average="20000",
    )
    second, created = store.prepare_intent(request(client_order_id="demo-order-2"))
    assert created is True
    assert second["status"] == "intent_created"


def test_demo_order_store_enforces_legal_transitions_and_audits_partial_fills(tmp_path):
    store = BinanceDemoOrderStore(str(tmp_path / "demo-orders.db"))
    store.prepare_intent(request())
    store.transition("demo-order-1", "submission_unknown")
    partial = store.transition(
        "demo-order-1", "partially_filled",
        exchange_order_id="exchange-1",
        exchange_status="open",
        filled="0.0004",
        remaining="0.0006",
        average="19999",
    )
    assert partial["status"] == "partially_filled"
    assert partial["filled"] == "0.0004"
    with pytest.raises(DemoOrderStoreError, match="Illegal"):
        store.transition("demo-order-1", "intent_created")
    events = store.list_events("demo-order-1")
    assert [event["to_status"] for event in events] == [
        "intent_created", "submission_unknown", "partially_filled",
    ]


def test_demo_order_store_preserves_submission_unknown_across_restart(tmp_path):
    path = str(tmp_path / "demo-orders.db")
    store = BinanceDemoOrderStore(path)
    store.prepare_intent(request())
    store.transition(
        "demo-order-1", "submission_unknown",
        error="Network timeout; exchange acceptance is unknown",
    )

    restarted = BinanceDemoOrderStore(path)
    order = restarted.get_order("demo-order-1")
    assert order["status"] == "submission_unknown"
    assert "timeout" in order["error"].lower()
    assert restarted.count_unresolved() == 1


def test_demo_order_store_order_intents_and_events_are_append_only(tmp_path):
    store = BinanceDemoOrderStore(str(tmp_path / "demo-orders.db"))
    store.prepare_intent(request())
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with store.connect() as db:
            db.execute(
                "UPDATE binance_demo_orders SET amount='0.2' WHERE client_order_id=?",
                ("demo-order-1",),
            )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with store.connect() as db:
            db.execute(
                "UPDATE binance_demo_order_events SET to_status='filled' WHERE client_order_id=?",
                ("demo-order-1",),
            )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with store.connect() as db:
            db.execute(
                "DELETE FROM binance_demo_orders WHERE client_order_id=?",
                ("demo-order-1",),
            )


def test_demo_order_store_validates_client_ids_symbols_and_decimal_strings(tmp_path):
    store = BinanceDemoOrderStore(str(tmp_path / "demo-orders.db"))
    with pytest.raises(DemoOrderStoreError, match="client_order_id"):
        store.prepare_intent(request(client_order_id="bad id"))
    invalid_symbol = request()
    invalid_symbol["symbol"] = "BTCUSDT"
    with pytest.raises(DemoOrderStoreError, match="symbol"):
        store.prepare_intent(invalid_symbol)
    invalid_amount = request()
    invalid_amount["amount"] = 0.001
    with pytest.raises(DemoOrderStoreError, match="decimal string"):
        store.prepare_intent(invalid_amount)


def test_demo_order_store_lists_orders_with_bounded_limit(tmp_path):
    store = BinanceDemoOrderStore(str(tmp_path / "demo-orders.db"))
    store.prepare_intent(request())
    assert len(store.list_orders(limit=1)) == 1
    with pytest.raises(DemoOrderStoreError, match="limit"):
        store.list_orders(limit=0)


def test_demo_order_store_quarantines_fill_regressions_and_preserves_last_good_snapshot(tmp_path):
    store = BinanceDemoOrderStore(str(tmp_path / "demo-fill-regression.db"))
    store.prepare_intent(request())
    store.transition("demo-order-1", "submission_unknown")
    store.transition(
        "demo-order-1", "open",
        exchange_order_id="exchange-1", exchange_status="open",
        filled="0", remaining="0.001",
    )
    store.transition(
        "demo-order-1", "partially_filled",
        exchange_status="open", filled="0.0005", remaining="0.0005",
        average="20000",
    )

    quarantined = store.transition(
        "demo-order-1", "partially_filled",
        exchange_status="open", filled="0.0004", remaining="0.0006",
        average="20000",
    )

    assert quarantined["status"] == "reconciliation_required"
    assert quarantined["filled"] == "0.0005"
    assert quarantined["remaining"] == "0.0005"
    assert "regressed" in quarantined["error"]
    event = store.list_events("demo-order-1")[-1]
    assert event["to_status"] == "reconciliation_required"
    assert event["payload"]["observed_filled"] == "0.0004"


def test_demo_order_store_quarantines_exchange_order_id_changes(tmp_path):
    store = BinanceDemoOrderStore(str(tmp_path / "demo-order-id-change.db"))
    store.prepare_intent(request())
    store.transition("demo-order-1", "submission_unknown")
    store.transition(
        "demo-order-1", "open",
        exchange_order_id="exchange-1", exchange_status="open",
        filled="0", remaining="0.001",
    )

    quarantined = store.transition(
        "demo-order-1", "open",
        exchange_order_id="exchange-2", exchange_status="open",
        filled="0", remaining="0.001",
    )

    assert quarantined["status"] == "reconciliation_required"
    assert quarantined["exchange_order_id"] == "exchange-1"
    assert "ID changed" in quarantined["error"]
