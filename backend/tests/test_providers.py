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
