from datetime import datetime, timezone
from typing import get_type_hints

import pytest

from app.accounts.contracts import (
    AccountOrder,
    AccountOrderFill,
    AccountProviderError,
    OrderReconciliationProvider,
)


def _order(**updates):
    result = {
        "provider_id": "ccxt:binance-demo",
        "account_alias": "demo-main",
        "environment": "demo",
        "market_type": "spot",
        "instrument_id": "ccxt:binance-demo:spot:BTC/USDT",
        "symbol": "BTC/USDT",
        "side": "BUY",
        "order_type": "LIMIT",
        "status": "partially-filled",
        "requested_quantity": "0.001000",
        "filled_quantity": "0.000400",
        "remaining_quantity": "0.000600",
        "average_fill_price": "20000.00",
        "quote_currency": "usdt",
        "order_id": "exchange-123",
        "client_order_id": "client-123",
        "exchange_status": "PARTIALLY_FILLED",
        "exchange_timestamp_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    result.update(updates)
    return result


def _fill(**updates):
    result = {
        "provider_id": "ccxt:binance-demo",
        "account_alias": "demo-main",
        "environment": "demo",
        "market_type": "spot",
        "instrument_id": "ccxt:binance-demo:spot:BTC/USDT",
        "symbol": "BTC/USDT",
        "side": "buy",
        "trade_id": "trade-987",
        "order_id": "exchange-123",
        "client_order_id": "client-123",
        "amount": "0.0004",
        "price": "20000.00",
        "cost": "8.0000",
        "quote_currency": "USDT",
        "fee_amount": "0.00001",
        "fee_currency": "BTC",
        "timestamp_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
    }
    result.update(updates)
    return result


def test_account_order_normalizes_status_identity_and_decimal_strings():
    order = AccountOrder.normalize(_order())

    assert order.provider_id == "ccxt:binance-demo"
    assert order.account_alias == "demo-main"
    assert order.environment == "demo"
    assert order.side == "buy"
    assert order.status == "partially_filled"
    assert order.requested_quantity == "0.001"
    assert order.filled_quantity == "0.0004"
    assert order.remaining_quantity == "0.0006"
    assert order.average_fill_price == "20000"
    assert order.quote_currency == "USDT"
    assert AccountOrder.normalize(order) == order
    assert AccountOrder.normalize(_order(status="canceled", filled_quantity="0.0004")).status == "cancelled"
    normalized_market = AccountOrder.normalize(_order(environment="DEMO", market_type="SPOT"))
    assert normalized_market.environment == "demo"
    assert normalized_market.market_type == "spot"


@pytest.mark.parametrize("updates", [
    {"requested_quantity": 0.001},
    {"requested_quantity": "1e-3"},
    {"client_order_id": None, "order_id": None},
    {"side": "hold"},
    {"status": "mystery"},
    {"remaining_quantity": "0.0005"},
    {"status": "filled", "filled_quantity": "0.0004", "remaining_quantity": "0.0006"},
    {"status": "partially_filled", "filled_quantity": "0"},
    {"observed_at": "2026-01-01T10:00:00"},
])
def test_account_order_rejects_ambiguous_or_inconsistent_evidence(updates):
    with pytest.raises(AccountProviderError):
        AccountOrder.normalize(_order(**updates))


def test_account_order_rejects_future_provider_timestamps():
    future_ms = int(datetime.now(timezone.utc).timestamp() * 1000) + 600_000
    future_at = datetime.fromtimestamp(future_ms / 1000, timezone.utc).isoformat()
    with pytest.raises(AccountProviderError, match="future"):
        AccountOrder.normalize(_order(exchange_timestamp_ms=future_ms))
    with pytest.raises(AccountProviderError, match="future"):
        AccountOrder.normalize(_order(observed_at=future_at))


def test_account_fill_normalizes_individual_fee_evidence():
    fill = AccountOrderFill.normalize(_fill())

    assert fill.trade_id == "trade-987"
    assert fill.amount == "0.0004"
    assert fill.price == "20000"
    assert fill.cost == "8"
    assert fill.fee_amount == "0.00001"
    assert fill.fee_currency == "BTC"
    assert AccountOrderFill.normalize(fill) == fill


@pytest.mark.parametrize("updates", [
    {"amount": 0.0004},
    {"price": "NaN"},
    {"trade_id": ""},
    {"client_order_id": None, "order_id": None},
    {"side": "hold"},
    {"fee_amount": "0.01", "fee_currency": None},
    {"timestamp_ms": True},
])
def test_account_fill_rejects_missing_malformed_or_unattributable_fields(updates):
    with pytest.raises(AccountProviderError):
        AccountOrderFill.normalize(_fill(**updates))


def test_reconciliation_protocol_resolves_normalized_order_and_fill_types():
    order_hints = get_type_hints(OrderReconciliationProvider.fetch_order)
    fill_hints = get_type_hints(OrderReconciliationProvider.fetch_fills)

    assert order_hints["return"] == AccountOrder | None
    assert fill_hints["return"] == list[AccountOrderFill]
