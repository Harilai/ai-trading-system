from datetime import datetime, timezone

import pytest

from app.providers.base import (
    AssetClass,
    MarketInstrument,
    MarketType,
    ProviderCapabilities,
    VenueType,
)
from app.providers.registry import ProviderRegistry, ProviderRegistryError


class FakeProvider:
    def __init__(self, provider_id, symbol="BTC/EUR"):
        self.provider_id = provider_id
        self.symbol = symbol

    def capabilities(self):
        return ProviderCapabilities(
            provider_id=self.provider_id,
            venue_type=VenueType.CEX,
            asset_classes=(AssetClass.CRYPTO,),
            market_types=(MarketType.SPOT,),
            public_market_data=True,
        )

    def discover_markets(self, asset_classes=None):
        return [
            MarketInstrument(
                provider_id=self.provider_id,
                symbol=self.symbol,
                asset_class=AssetClass.CRYPTO,
                market_type=MarketType.SPOT,
                base="BTC",
                quote="EUR",
                active=True,
                min_cost=1.0,
            )
        ]

    def ticker(self, symbol):
        if symbol != self.symbol:
            raise ValueError("unknown symbol")
        return {"symbol": symbol, "last": 100.0, "timestamp": 1_800_000_000_000}

    def ohlcv(self, symbol, timeframe="1h", limit=100):
        if symbol != self.symbol:
            raise ValueError("unknown symbol")
        return [[1, 1, 2, 0.5, 1.5, 10]]


def test_registry_registers_and_describes_capabilities():
    registry = ProviderRegistry()
    registry.register(FakeProvider("fake-one"))
    description = registry.describe()
    assert description["providers"] == ["fake-one"]
    capability = description["capabilities"][0]
    assert capability["asset_classes"] == ["crypto"]
    assert capability["market_types"] == ["spot"]
    assert capability["order_management"] is False


def test_registry_rejects_duplicate_provider():
    registry = ProviderRegistry()
    registry.register(FakeProvider("fake-one"))
    import pytest
    with pytest.raises(ProviderRegistryError, match="already registered"):
        registry.register(FakeProvider("fake-one"))


def test_registry_discovery_retains_provider_identity():
    registry = ProviderRegistry()
    registry.register(FakeProvider("fake-one"))
    registry.register(FakeProvider("fake-two"))
    instruments, failures = registry.discover_markets((AssetClass.CRYPTO,))
    assert failures == []
    assert {item.provider_id for item in instruments} == {"fake-one", "fake-two"}
    assert {item.instrument_id for item in instruments} == {
        "fake-one:spot:BTC/EUR",
        "fake-two:spot:BTC/EUR",
    }


def test_registry_routes_ticker_and_ohlcv_to_explicit_provider():
    registry = ProviderRegistry()
    registry.register(FakeProvider("fake-one"))
    registry.register(FakeProvider("fake-two"))
    assert registry.ticker("BTC/EUR", "fake-two")["last"] == 100
    assert registry.ohlcv("BTC/EUR", provider_id="fake-one")[0][4] == 1.5


def test_registry_does_not_invent_unknown_provider_access():
    registry = ProviderRegistry()
    import pytest
    with pytest.raises(ProviderRegistryError, match="not configured"):
        registry.get("unknown-broker")


def test_registry_filters_asset_classes():
    registry = ProviderRegistry()
    registry.register(FakeProvider("fake-one"))
    instruments, failures = registry.discover_markets((AssetClass.EQUITY,))
    assert instruments == []
    assert failures == []


def test_initial_ccxt_adapter_is_public_data_only():
    from app.providers.ccxt_spot import CcxtSpotProvider

    provider = CcxtSpotProvider("kraken")
    capabilities = provider.capabilities()
    assert capabilities.provider_id == "ccxt:kraken"
    assert capabilities.public_market_data is True
    assert capabilities.requires_authentication_for_market_data is False
    assert capabilities.requires_authentication_for_trading is True
    assert capabilities.order_management is False
    assert capabilities.balances is False
    assert capabilities.supports_withdrawals is False
    assert capabilities.connectivity_status == "not_checked"



def test_ccxt_provider_discovery_skips_malformed_tickers():
    from datetime import datetime, timezone
    from app.providers.ccxt_spot import CcxtSpotProvider

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    class FakeExchange:
        has = {"fetchTickers": True}

        def fetch_tickers(self, symbols):
            return {
                "BTC/EUR": {
                    "timestamp": now_ms,
                    "last": 100.0,
                    "bid": 99.9,
                    "ask": 100.1,
                    "quoteVolume": 10000,
                },
                "ETH/EUR": {
                    "timestamp": now_ms,
                    "last": float("nan"),
                    "bid": 99.9,
                    "ask": 100.1,
                    "quoteVolume": 10000,
                },
            }

    provider = CcxtSpotProvider.__new__(CcxtSpotProvider)
    provider.provider_id = "ccxt:fake"
    provider._markets = {
        "BTC/EUR": {"spot": True, "active": True, "quote": "EUR"},
        "ETH/EUR": {"spot": True, "active": True, "quote": "EUR"},
        "INACTIVE/EUR": {"spot": True, "active": False, "quote": "EUR"},
    }
    provider.exchange = FakeExchange()

    results = provider.discover_spot_symbols()

    assert [item["symbol"] for item in results] == ["BTC/EUR"]
    assert results[0]["provider_id"] == "ccxt:fake"


def test_ccxt_provider_marks_missing_exchange_time_as_local_retrieval_time():
    from app.providers.ccxt_spot import CcxtSpotProvider

    class FakeExchange:
        has = {"fetchTickers": True}

        def fetch_tickers(self, symbols):
            return {
                "BTC/EUR": {
                    "timestamp": None,
                    "datetime": None,
                    "last": 100.0,
                    "bid": 99.9,
                    "ask": 100.1,
                    "quoteVolume": 10000,
                }
            }

        def fetch_ticker(self, symbol):
            return {
                "timestamp": None,
                "datetime": None,
                "last": 100.0,
                "bid": 99.9,
                "ask": 100.1,
                "quoteVolume": 10000,
            }

    provider = CcxtSpotProvider.__new__(CcxtSpotProvider)
    provider.provider_id = "ccxt:fake"
    provider._markets = {"BTC/EUR": {"spot": True, "active": True, "quote": "EUR"}}
    provider.exchange = FakeExchange()

    result = provider.discover_spot_symbols()
    ticker = provider.ticker("BTC/EUR")
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    assert len(result) == 1
    assert result[0]["ticker_timestamp_source"] == "retrieved_at"
    assert 0 <= now_ms - result[0]["ticker_timestamp_ms"] < 10_000
    assert ticker["timestamp_source"] == "retrieved_at"
    assert 0 <= now_ms - ticker["timestamp"] < 10_000



def test_registry_discovery_continues_when_one_provider_fails():
    class HealthyDiscoveryProvider(FakeProvider):
        def discover_spot_symbols(self, quote, limit, max_spread_fraction, min_quote_volume):
            return [{
                "symbol": "BTC/EUR",
                "provider_id": self.provider_id,
                "instrument_id": f"{self.provider_id}:spot:BTC/EUR",
                "quote_volume_24h": 5000,
            }]

    class FailingDiscoveryProvider(FakeProvider):
        def discover_spot_symbols(self, quote, limit, max_spread_fraction, min_quote_volume):
            raise RuntimeError("temporary exchange outage")

    registry = ProviderRegistry()
    registry.register(FailingDiscoveryProvider("unavailable"))
    registry.register(HealthyDiscoveryProvider("healthy"))

    results = registry.discover_spot_symbols()

    assert len(results) == 1
    assert results[0]["provider_id"] == "healthy"
    assert registry.last_discovery_failures == [{
        "provider_id": "unavailable",
        "reason": "temporary exchange outage",
    }]


def test_provider_registry_rejects_future_and_malformed_timestamps():
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    assert ProviderRegistry.is_stale({"timestamp": now_ms + 1}, 60) is True
    assert ProviderRegistry.is_stale({"timestamp": float("nan")}, 60) is True
    assert ProviderRegistry.is_stale({}, 60) is True
    assert ProviderRegistry.is_stale({"timestamp": now_ms - 1000}, 60) is False


def test_market_instrument_normalizes_full_contract_constraints():
    from app.providers.base import PrecisionMode

    instrument = MarketInstrument(
        provider_id="ccxt:example",
        symbol="BTC/EUR",
        asset_class="crypto",
        market_type="spot",
        base="BTC",
        quote="EUR",
        active=True,
        venue_id="example",
        native_symbol="BTC/EUR",
        precision_mode="tick_size",
        price_tick="0.01",
        amount_step="0.0001",
        min_amount="0.00001",
        max_amount="10",
        min_cost="5",
        max_cost="100000",
        contract_type="spot",
        trading_status="active",
    )

    assert instrument.precision_mode is PrecisionMode.TICK_SIZE
    assert instrument.price_tick == pytest.approx(0.01)
    assert instrument.amount_step == pytest.approx(0.0001)
    assert instrument.min_amount == pytest.approx(0.00001)
    assert instrument.max_amount == pytest.approx(10)
    assert instrument.venue_id == "example"
    assert instrument.native_symbol == "BTC/EUR"
    assert instrument.instrument_id == "ccxt:example:spot:BTC/EUR"


def test_market_instrument_requires_complete_option_contract_identity():
    import pytest

    with pytest.raises(ValueError, match="require expiry"):
        MarketInstrument(
            provider_id="options:venue", symbol="BTC-20270101-100000-C",
            asset_class=AssetClass.OPTION, market_type=MarketType.OPTIONS,
            base="BTC", quote="USD", active=True,
        )

    option = MarketInstrument(
        provider_id="options:venue", symbol="BTC-20270101-100000-C",
        asset_class=AssetClass.OPTION, market_type=MarketType.OPTIONS,
        base="BTC", quote="USD", active=True,
        expiry="2027-01-01", strike=100000, option_type="CALL",
        underlying="BTC", contract_size=1,
    )
    assert option.option_type == "call"
    assert option.strike == pytest.approx(100000)


def test_market_instrument_rejects_invalid_precision_and_market_limits():
    import pytest

    with pytest.raises(ValueError, match="price_tick must be positive"):
        MarketInstrument(
            provider_id="ccxt:example", symbol="BTC/EUR",
            asset_class=AssetClass.CRYPTO, market_type=MarketType.SPOT,
            base="BTC", quote="EUR", active=True, price_tick=0,
        )

    with pytest.raises(ValueError, match="max_amount cannot be below min_amount"):
        MarketInstrument(
            provider_id="ccxt:example", symbol="BTC/EUR",
            asset_class=AssetClass.CRYPTO, market_type=MarketType.SPOT,
            base="BTC", quote="EUR", active=True, min_amount=2, max_amount=1,
        )


def test_ccxt_discovery_maps_tick_size_and_order_limits():
    import ccxt
    from app.providers.ccxt_spot import CcxtSpotProvider
    from app.providers.base import PrecisionMode

    class FakeExchange:
        precisionMode = ccxt.TICK_SIZE

    provider = CcxtSpotProvider.__new__(CcxtSpotProvider)
    provider.provider_id = "ccxt:fake"
    provider.exchange_id = "fake"
    provider._markets = {
        "BTC/EUR": {
            "spot": True, "active": True, "base": "BTC", "quote": "EUR",
            "precision": {"price": 0.01, "amount": 0.0001},
            "limits": {
                "amount": {"min": 0.00001, "max": 5},
                "cost": {"min": 5, "max": 50000},
            },
            "maker": 0.001, "taker": 0.002, "contractSize": None,
        },
    }
    provider.exchange = FakeExchange()

    instruments = provider.discover_markets()
    assert len(instruments) == 1
    instrument = instruments[0]
    assert instrument.precision_mode is PrecisionMode.TICK_SIZE
    assert instrument.price_tick == pytest.approx(0.01)
    assert instrument.amount_step == pytest.approx(0.0001)
    assert instrument.min_amount == pytest.approx(0.00001)
    assert instrument.max_amount == pytest.approx(5)
    assert instrument.min_cost == pytest.approx(5)
    assert instrument.max_cost == pytest.approx(50000)
    assert instrument.venue_id == "fake"
    assert instrument.contract_type == "spot"


def test_market_instrument_rejects_non_finite_contract_values():
    with pytest.raises(ValueError, match="price_tick must be finite"):
        MarketInstrument(
            provider_id="ccxt:example", symbol="BTC/EUR",
            asset_class=AssetClass.CRYPTO, market_type=MarketType.SPOT,
            base="BTC", quote="EUR", active=True, price_tick=float("nan"),
        )
    with pytest.raises(ValueError, match="taker_fee must be finite"):
        MarketInstrument(
            provider_id="ccxt:example", symbol="BTC/EUR",
            asset_class=AssetClass.CRYPTO, market_type=MarketType.SPOT,
            base="BTC", quote="EUR", active=True, taker_fee=float("inf"),
        )


def test_ccxt_discovery_maps_decimal_places_without_inventing_tick_sizes():
    import ccxt
    from app.providers.base import PrecisionMode
    from app.providers.ccxt_spot import CcxtSpotProvider

    class FakeExchange:
        precisionMode = ccxt.DECIMAL_PLACES

    provider = CcxtSpotProvider.__new__(CcxtSpotProvider)
    provider.provider_id = "ccxt:fake"
    provider.exchange_id = "fake"
    provider._markets = {
        "ETH/EUR": {
            "spot": True, "active": True, "base": "ETH", "quote": "EUR",
            "precision": {"price": 2, "amount": 5},
            "limits": {},
        },
    }
    provider.exchange = FakeExchange()

    instrument = provider.discover_markets()[0]
    assert instrument.precision_mode is PrecisionMode.DECIMAL_PLACES
    assert instrument.price_precision == 2
    assert instrument.amount_precision == 5
    assert instrument.price_tick is None
    assert instrument.amount_step is None

