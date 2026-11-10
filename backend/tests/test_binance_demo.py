import pytest

from app.accounts.binance_demo import BinanceDemoError, BinanceSpotDemoAccount


class FakeBinanceDemoExchange:
    def __init__(self, response=None, markets=None, production_endpoint=False):
        self.response = response or {}
        self.markets = markets or {}
        self.calls = []
        self.urls = {
            "api": {
                "public": "https://api.binance.com/api/v3",
                "private": "https://api.binance.com/api/v3",
            }
        }
        self.production_endpoint = production_endpoint

    def enable_demo_trading(self, enabled):
        self.calls.append(("enable_demo_trading", enabled))
        host = "https://api.binance.com/api/v3" if self.production_endpoint else "https://demo-api.binance.com/api/v3"
        self.urls = {"api": {"public": host, "private": host}}

    def fetch_balance(self):
        self.calls.append(("fetch_balance",))
        return self.response

    def load_markets(self):
        self.calls.append(("load_markets",))
        return self.markets


def test_binance_demo_connector_enables_demo_before_any_exchange_call_and_never_uses_production():
    exchange = FakeBinanceDemoExchange(
        response={
            "free": {"BTC": "0.12345678901234567890", "EUR": "0"},
            "used": {"BTC": "0.01", "EUR": "0"},
            "total": {"BTC": "0.13345678901234567890", "EUR": "0"},
        },
    )
    config = {}

    def factory(options):
        config.update(options)
        return exchange

    provider = BinanceSpotDemoAccount(
        "demo-api-key", "demo-api-secret",
        enabled=True,
        exchange_factory=factory,
    )
    balances = provider.fetch_balances()

    assert exchange.calls == [
        ("enable_demo_trading", True),
        ("fetch_balance",),
    ]
    assert config["apiKey"] == "demo-api-key"
    assert config["secret"] == "demo-api-secret"
    assert config["enableRateLimit"] is True
    assert config["timeout"] == 10000
    assert config["options"]["defaultType"] == "spot"
    assert balances["environment"] == "demo"
    assert balances["provider_id"] == "ccxt:binance-demo"
    assert balances["balances"] == [
        {
            "asset": "BTC",
            "free": "0.1234567890123456789",
            "used": "0.01",
            "total": "0.1334567890123456789",
        }
    ]
    description = provider.describe()
    assert description["demo_only"] is True
    assert description["production_endpoint_allowed"] is False
    assert description["order_submission"] is False
    assert description["withdrawals"] is False


def test_binance_demo_connector_rejects_missing_enable_demo_trading_method():
    class LegacyOnlyExchange:
        urls = {"api": {"private": "https://testnet.binance.vision/api"}}

        def set_sandbox_mode(self, enabled):
            raise AssertionError("legacy sandbox mode must not be called")

    with pytest.raises(BinanceDemoError, match="does not support Binance Demo Trading"):
        BinanceSpotDemoAccount(
            "key", "secret", enabled=True,
            exchange_factory=lambda _: LegacyOnlyExchange(),
        )


def test_binance_demo_connector_fails_closed_if_ccxt_keeps_production_rest_endpoint():
    exchange = FakeBinanceDemoExchange(production_endpoint=True)
    with pytest.raises(BinanceDemoError, match="Production Binance REST endpoint"):
        BinanceSpotDemoAccount(
            "key", "secret", enabled=True,
            exchange_factory=lambda _: exchange,
        )
    assert exchange.calls == [("enable_demo_trading", True)]


def test_binance_demo_connector_fails_closed_if_demo_endpoint_is_not_present():
    exchange = FakeBinanceDemoExchange()
    exchange.urls = {"api": {"public": "https://api.other-venue.example", "private": "https://api.other-venue.example"}}
    exchange.enable_demo_trading = lambda enabled: exchange.calls.append(("enable_demo_trading", enabled))
    with pytest.raises(BinanceDemoError, match="did not route"):
        BinanceSpotDemoAccount(
            "key", "secret", enabled=True,
            exchange_factory=lambda _: exchange,
        )


def test_binance_demo_connector_requires_explicit_enable_and_separate_credentials():
    with pytest.raises(BinanceDemoError, match="disabled"):
        BinanceSpotDemoAccount("key", "secret", enabled=False)
    with pytest.raises(BinanceDemoError, match="API key"):
        BinanceSpotDemoAccount("", "secret", enabled=True)
    with pytest.raises(BinanceDemoError, match="API secret"):
        BinanceSpotDemoAccount("key", "", enabled=True)


def test_binance_demo_market_discovery_returns_only_active_spot_markets():
    exchange = FakeBinanceDemoExchange(markets={
        "BTC/USDT": {
            "spot": True, "active": True, "base": "BTC", "quote": "USDT",
            "precision": {"amount": 0.00001, "price": 0.01},
            "limits": {"amount": {"min": 0.00001}},
        },
        "ETH/USDT": {"spot": True, "active": False, "base": "ETH", "quote": "USDT"},
        "BTC/USDT:USDT": {"spot": False, "swap": True, "active": True},
    })
    provider = BinanceSpotDemoAccount(
        "key", "secret", enabled=True,
        exchange_factory=lambda _: exchange,
    )

    result = provider.discover_spot_markets(limit=1)

    assert result["environment"] == "demo"
    assert result["demo_only"] is True
    assert result["discovered"] == 1
    assert result["markets"][0]["symbol"] == "BTC/USDT"
    assert exchange.calls == [("enable_demo_trading", True), ("load_markets",)]


def test_binance_demo_market_discovery_validates_limit():
    exchange = FakeBinanceDemoExchange()
    provider = BinanceSpotDemoAccount(
        "key", "secret", enabled=True,
        exchange_factory=lambda _: exchange,
    )
    with pytest.raises(BinanceDemoError, match="limit"):
        provider.discover_spot_markets(limit=0)


def test_binance_demo_connector_sanitizes_balance_errors():
    secret = "never-return-this-secret"
    exchange = FakeBinanceDemoExchange()
    def fail():
        exchange.calls.append(("fetch_balance",))
        raise RuntimeError(f"auth header secret={secret}")
    exchange.fetch_balance = fail
    provider = BinanceSpotDemoAccount(
        "key", secret, enabled=True,
        exchange_factory=lambda _: exchange,
    )

    with pytest.raises(BinanceDemoError) as error:
        provider.fetch_balances()

    assert secret not in str(error.value)
    assert "auth header" not in str(error.value)


def test_pinned_ccxt_binance_demo_mode_routes_to_demo_rest_host_without_network():
    import ccxt

    exchange = ccxt.binance()
    enable_demo = getattr(exchange, "enable_demo_trading", None)
    assert callable(enable_demo), f"CCXT {ccxt.__version__} lacks Binance Demo Trading support"
    enable_demo(True)
    BinanceSpotDemoAccount._verify_demo_endpoints(exchange)


def make_demo_execution(exchange):
    from app.accounts.binance_demo import BinanceSpotDemoExecution
    return BinanceSpotDemoExecution(
        "demo-key", "demo-secret", enabled=True,
        exchange_factory=lambda _: exchange,
    )


def demo_market(quote="USDT"):
    return {
        "spot": True,
        "active": True,
        "base": "BTC",
        "quote": quote,
        "precision": {"amount": 0.0001, "price": 0.01},
        "limits": {
            "amount": {"min": 0.0001, "max": 10},
            "cost": {"min": 10, "max": 10000},
        },
    }


class FakeDemoExecutionExchange(FakeBinanceDemoExchange):
    def __init__(self, *, response=None, markets=None):
        super().__init__(response=response, markets=markets or {"BTC/USDT": demo_market()})
        self.created = []
        self.fetched = []
        self.cancelled = []

    def amount_to_precision(self, symbol, amount):
        self.calls.append(("amount_to_precision", symbol, amount))
        return "0.001"

    def price_to_precision(self, symbol, price):
        self.calls.append(("price_to_precision", symbol, price))
        return str(price)

    def create_order(self, symbol, order_type, side, amount, price, params):
        self.calls.append(("create_order", symbol, order_type, side, amount, price, params))
        self.created.append(params["clientOrderId"])
        return {
            "id": "demo-order-123",
            "clientOrderId": params["clientOrderId"],
            "status": "open",
            "amount": amount,
            "filled": "0",
            "remaining": amount,
            "average": None,
        }

    def fetch_order(self, order_id, symbol, params=None):
        self.calls.append(("fetch_order", order_id, symbol, params))
        self.fetched.append((order_id, symbol, params))
        return {
            "id": order_id or "demo-order-123",
            "clientOrderId": (params or {}).get("origClientOrderId", "demo-order-1234"),
            "status": "closed",
            "amount": "0.001",
            "filled": "0.001",
            "remaining": "0",
            "average": "20000",
        }

    def cancel_order(self, order_id, symbol):
        self.calls.append(("cancel_order", order_id, symbol))
        self.cancelled.append(order_id)
        return {
            "id": order_id,
            "clientOrderId": "demo-order-1234",
            "status": "canceled",
            "amount": "0.001",
            "filled": "0",
            "remaining": "0.001",
            "average": None,
        }


def test_demo_execution_validates_limit_order_precision_limits_and_quote_cap():
    exchange = FakeDemoExecutionExchange()
    provider = make_demo_execution(exchange)

    request = provider.validate_limit_order(
        symbol="BTC/USDT",
        side="buy",
        amount="0.001",
        price="20000",
        client_order_id="demo-order-1234",
        max_order_quote="25",
    )
    assert request == {
        "client_order_id": "demo-order-1234",
        "symbol": "BTC/USDT",
        "side": "buy",
        "amount": "0.001",
        "price": "20000",
        "quote_asset": "USDT",
        "max_order_quote": "25",
    }

    with pytest.raises(BinanceDemoError, match="quote cap"):
        provider.validate_limit_order(
            symbol="BTC/USDT", side="buy", amount="0.001",
            price="30000", client_order_id="demo-order-over-cap",
            max_order_quote="25",
        )


def test_demo_execution_rejects_nonstable_quote_markets_and_precision_rounding():
    exchange = FakeDemoExecutionExchange(markets={
        "BTC/ETH": demo_market(quote="ETH"),
        "BTC/USDT": demo_market(),
    })
    provider = make_demo_execution(exchange)
    with pytest.raises(BinanceDemoError, match="stablecoin/EUR"):
        provider.validate_limit_order(
            symbol="BTC/ETH", side="buy", amount="0.001",
            price="20000", client_order_id="demo-eth-quote",
        )

    exchange.amount_to_precision = lambda symbol, amount: "0.0009"
    with pytest.raises(BinanceDemoError, match="precision"):
        provider.validate_limit_order(
            symbol="BTC/USDT", side="buy", amount="0.001",
            price="20000", client_order_id="demo-rounded",
        )


def test_demo_execution_submits_only_limit_orders_with_stable_client_order_id():
    exchange = FakeDemoExecutionExchange()
    provider = make_demo_execution(exchange)
    request = {
        "client_order_id": "demo-order-1234",
        "symbol": "BTC/USDT",
        "side": "buy",
        "amount": "0.001",
        "price": "20000",
        "quote_asset": "USDT",
    }

    result = provider.submit_limit_order(request)

    assert exchange.created == ["demo-order-1234"]
    call = next(item for item in exchange.calls if item[0] == "create_order")
    assert call[2] == "limit"
    assert call[6]["clientOrderId"] == "demo-order-1234"
    assert result["status"] == "open"
    assert result["exchange_order_id"] == "demo-order-123"


def test_demo_execution_maps_partial_fills_and_rejects_mismatched_exchange_client_id():
    exchange = FakeDemoExecutionExchange()
    provider = make_demo_execution(exchange)
    partial = provider.normalize_order({
        "id": "order-1", "clientOrderId": "client-1", "status": "open",
        "filled": "0.0004", "remaining": "0.0006", "average": "19999",
    }, expected_client_order_id="client-1")
    assert partial["status"] == "partially_filled"
    assert partial["filled"] == "0.0004"
    assert partial["remaining"] == "0.0006"

    mismatch = provider.normalize_order({
        "id": "order-2", "clientOrderId": "other-client", "status": "open",
        "filled": "0", "remaining": "0.001",
    }, expected_client_order_id="client-1")
    assert mismatch["status"] == "reconciliation_required"


def test_demo_execution_reconciles_by_client_id_and_cancels_by_exchange_id():
    exchange = FakeDemoExecutionExchange()
    provider = make_demo_execution(exchange)
    found = provider.fetch_order_by_client_id(
        symbol="BTC/USDT",
        client_order_id="demo-order-1234",
    )
    assert found["status"] == "filled"
    assert exchange.fetched[0][0] is None
    assert exchange.fetched[0][2] == {"origClientOrderId": "demo-order-1234"}

    cancelled = provider.cancel_limit_order(
        symbol="BTC/USDT",
        exchange_order_id="demo-order-123",
        client_order_id="demo-order-1234",
    )
    assert cancelled["status"] == "cancelled"
    assert exchange.cancelled == ["demo-order-123"]


def test_demo_execution_preserves_zero_fill_and_missing_remaining_without_inventing_completion():
    exchange = FakeDemoExecutionExchange()
    provider = make_demo_execution(exchange)
    order = provider.normalize_order({
        "id": "order-zero",
        "clientOrderId": "client-zero",
        "status": "open",
        "filled": "0.00000000",
        "remaining": None,
        "average": "0.000000",
    }, expected_client_order_id="client-zero")

    assert order["status"] == "open"
    assert order["filled"] == "0"
    assert order["remaining"] is None
    assert order["average"] is None


def test_demo_execution_fails_closed_on_inconsistent_closed_order_response():
    exchange = FakeDemoExecutionExchange()
    provider = make_demo_execution(exchange)
    order = provider.normalize_order({
        "id": "order-invalid",
        "clientOrderId": "client-invalid",
        "status": "closed",
        "filled": "0",
        "remaining": "0.001",
    }, expected_client_order_id="client-invalid")
    assert order["status"] == "reconciliation_required"


def test_demo_execution_rejects_closed_order_without_complete_fill_evidence():
    exchange = FakeDemoExecutionExchange()
    provider = make_demo_execution(exchange)

    missing_remaining = provider.normalize_order({
        "id": "order-missing-remaining",
        "clientOrderId": "client-missing-remaining",
        "status": "closed",
        "amount": "0.001",
        "filled": "0.001",
        "remaining": None,
    }, expected_client_order_id="client-missing-remaining", expected_amount="0.001")
    partial_closed = provider.normalize_order({
        "id": "order-partial-closed",
        "clientOrderId": "client-partial-closed",
        "status": "closed",
        "amount": "0.001",
        "filled": "0.0004",
        "remaining": "0",
    }, expected_client_order_id="client-partial-closed", expected_amount="0.001")

    assert missing_remaining["status"] == "reconciliation_required"
    assert partial_closed["status"] == "reconciliation_required"


def test_demo_execution_rejects_exchange_order_id_mismatch():
    exchange = FakeDemoExecutionExchange()
    provider = make_demo_execution(exchange)
    result = provider.normalize_order({
        "id": "different-exchange-id",
        "clientOrderId": "client-id",
        "status": "open",
        "amount": "0.001",
        "filled": "0",
        "remaining": "0.001",
    }, expected_client_order_id="client-id", expected_amount="0.001",
       expected_exchange_order_id="persisted-exchange-id")
    assert result["status"] == "reconciliation_required"
    assert "exchange order ID" in result["error"]


def test_demo_execution_rejects_exchange_amount_mismatch_and_fill_overrun():
    exchange = FakeDemoExecutionExchange()
    provider = make_demo_execution(exchange)
    wrong_amount = provider.normalize_order({
        "id": "wrong-amount",
        "clientOrderId": "client-amount",
        "status": "open",
        "amount": "0.002",
        "filled": "0",
        "remaining": "0.002",
    }, expected_client_order_id="client-amount", expected_amount="0.001")
    overfill = provider.normalize_order({
        "id": "overfill",
        "clientOrderId": "client-overfill",
        "status": "open",
        "amount": "0.001",
        "filled": "0.0012",
        "remaining": "0",
    }, expected_client_order_id="client-overfill", expected_amount="0.001")

    assert wrong_amount["status"] == "reconciliation_required"
    assert overfill["status"] == "reconciliation_required"
