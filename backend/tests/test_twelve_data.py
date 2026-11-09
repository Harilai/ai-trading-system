import io
import json
from urllib.parse import parse_qs, urlparse

import pytest

from app.market_data import MarketDataError
from app.providers.base import AssetClass, MarketType
from app.providers.twelve_data import TwelveDataProvider


class FakeTwelveDataTransport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, url, timeout=15):
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        endpoint = parsed.path.rsplit("/", 1)[-1]
        self.calls.append((endpoint, params, timeout))
        if params.get("apikey") != ["test-key"]:
            raise AssertionError("API key was not passed through the request")
        payload = self.responses.get(endpoint)
        if isinstance(payload, Exception):
            raise payload
        if payload is None:
            payload = {"status": "error", "message": f"Unexpected endpoint {endpoint}"}
        return io.BytesIO(json.dumps(payload).encode("utf-8"))


def test_twelve_data_capabilities_are_market_data_only():
    provider = TwelveDataProvider("test-key", opener=FakeTwelveDataTransport({}))
    caps = provider.capabilities()
    assert caps.provider_id == "twelve_data"
    assert caps.asset_classes == (AssetClass.EQUITY, AssetClass.ETF)
    assert caps.market_types == (MarketType.CASH_EQUITY,)
    assert caps.public_market_data is True
    assert caps.requires_authentication_for_market_data is True
    assert caps.order_management is False
    assert caps.balances is False
    assert caps.supports_withdrawals is False
    assert caps.supports_wallet_signing is False


def test_twelve_data_requires_an_api_key():
    with pytest.raises(ValueError, match="API key is required"):
        TwelveDataProvider("")


def test_discovery_returns_us_equities_and_etfs_with_distinct_asset_classes():
    transport = FakeTwelveDataTransport({
        "stocks": {
            "data": [{
                "symbol": "AAPL", "name": "Apple Inc", "currency": "USD",
                "exchange": "NASDAQ", "country": "United States", "type": "Common Stock",
            }],
            "status": "ok",
        },
        "list": {
            "result": {
                "list": [{
                    "symbol": "SPY", "name": "SPDR S&P 500 ETF Trust",
                    "country": "United States", "mic_code": "ARCX",
                    "fund_family": "SPDR", "fund_type": "Large Blend",
                }]
            },
            "status": "ok",
        },
    })
    provider = TwelveDataProvider("test-key", opener=transport)
    instruments = provider.discover_markets()
    assert len(instruments) == 2
    equity, etf = instruments
    assert equity.symbol == "AAPL"
    assert equity.asset_class is AssetClass.EQUITY
    assert equity.market_type is MarketType.CASH_EQUITY
    assert equity.quote == "USD"
    assert etf.symbol == "SPY"
    assert etf.asset_class is AssetClass.ETF
    assert etf.metadata["fund_family"] == "SPDR"
    assert transport.calls[0][0] == "stocks"
    assert transport.calls[0][1]["country"] == ["United States"]
    assert transport.calls[1][0] == "list"
    assert transport.calls[1][1]["country"] == ["United States"]


def test_discovery_can_be_limited_to_one_asset_class():
    transport = FakeTwelveDataTransport({
        "stocks": {"data": [{"symbol": "MSFT", "currency": "USD"}], "status": "ok"},
    })
    provider = TwelveDataProvider("test-key", opener=transport)
    instruments = provider.discover_markets((AssetClass.EQUITY,))
    assert [item.symbol for item in instruments] == ["MSFT"]
    assert [call[0] for call in transport.calls] == ["stocks"]


def test_ticker_preserves_missing_bid_ask_instead_of_fabricating_spread():
    transport = FakeTwelveDataTransport({
        "quote": {
            "symbol": "AAPL", "close": "212.50", "timestamp": 1_800_000_000,
            "volume": "1000000", "exchange": "NASDAQ", "currency": "USD",
        }
    })
    provider = TwelveDataProvider("test-key", opener=transport)
    quote = provider.ticker("AAPL")
    assert quote["last"] == pytest.approx(212.5)
    assert quote["bid"] is None
    assert quote["ask"] is None
    assert quote["timestamp"] == 1_800_000_000_000
    assert quote["baseVolume"] == 1_000_000


def test_ohlcv_converts_exchange_local_timestamps_to_utc_epoch():
    transport = FakeTwelveDataTransport({
        "time_series": {
            "meta": {"exchange_timezone": "America/New_York"},
            "values": [
                {"datetime": "2026-11-06 10:00:00", "open": "10", "high": "12", "low": "9", "close": "11", "volume": "500"},
                {"datetime": "2026-11-06 09:00:00", "open": "9", "high": "11", "low": "8", "close": "10", "volume": "400"},
            ],
            "status": "ok",
        }
    })
    provider = TwelveDataProvider("test-key", opener=transport)
    rows = provider.ohlcv("AAPL", "1h", 2)
    assert len(rows) == 2
    assert rows[0][4] == pytest.approx(10)
    assert rows[1][4] == pytest.approx(11)
    assert rows[0][0] < rows[1][0]
    assert transport.calls[0][1]["interval"] == ["1h"]
    assert transport.calls[0][1]["outputsize"] == ["2"]


def test_provider_errors_are_reported_without_exposing_api_key():
    transport = FakeTwelveDataTransport({
        "quote": {"status": "error", "message": "Invalid API key"},
    })
    provider = TwelveDataProvider("secret-value", opener=transport)
    with pytest.raises(MarketDataError, match="Invalid API key") as error:
        provider.ticker("AAPL")
    assert "secret-value" not in str(error.value)
