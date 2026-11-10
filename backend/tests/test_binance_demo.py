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
