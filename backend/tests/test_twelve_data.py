import io
import json
from urllib.parse import parse_qs, urlparse

import pytest

from app.market_data import MarketDataError
from app.providers.base import AssetClass, MarketType
from app.providers.twelve_data import TwelveDataProvider


class FakeTwelveDataTransport:
    def __init__(self, responses, api_key="test-key"):
        self.responses = responses
        self.api_key = api_key
        self.calls = []

    def __call__(self, url, timeout=15):
        request_url = url.full_url if hasattr(url, "full_url") else url
        request_headers = url.headers if hasattr(url, "headers") else {}
        parsed = urlparse(request_url)
        params = parse_qs(parsed.query)
        endpoint = parsed.path.rsplit("/", 1)[-1]
        self.calls.append((endpoint, params, timeout))
        authorization = request_headers.get("Authorization") or request_headers.get("Authorization".lower())
        if authorization != f"apikey {self.api_key}":
            raise AssertionError("API key was not passed through the Authorization header")
        if "apikey" in params:
            raise AssertionError("API key must not be placed in the URL query string")
        payload = self.responses.get(endpoint)
        if isinstance(payload, Exception):
            raise payload
        if payload is None:
            payload = {"status": "error", "message": f"Unexpected endpoint {endpoint}"}
        return io.BytesIO(json.dumps(payload).encode("utf-8"))


def test_twelve_data_capabilities_are_market_data_only(tmp_path):
    provider = TwelveDataProvider("test-key", opener=FakeTwelveDataTransport({}), state_path=str(tmp_path / "state.db"), min_request_interval_seconds=0)
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


def test_twelve_data_requires_an_api_key(tmp_path):
    with pytest.raises(ValueError, match="API key is required"):
        TwelveDataProvider("")


def test_discovery_returns_us_equities_and_etfs_with_distinct_asset_classes(tmp_path):
    transport = FakeTwelveDataTransport({
        "stocks": {
            "data": [{
                "symbol": "AAPL", "name": "Apple Inc", "currency": "USD",
                "exchange": "NASDAQ", "mic_code": "XNAS", "country": "United States", "type": "Common Stock",
            }],
            "status": "ok",
        },
        "list": {
            "result": {
                "list": [{
                    "symbol": "SPY", "name": "SPDR S&P 500 ETF Trust",
                    "country": "United States", "exchange": "NYSE Arca", "mic_code": "ARCX",
                    "fund_family": "SPDR", "fund_type": "Large Blend",
                }]
            },
            "status": "ok",
        },
    })
    provider = TwelveDataProvider("test-key", opener=transport, state_path=str(tmp_path / "state.db"), min_request_interval_seconds=0)
    instruments = provider.discover_markets()
    assert len(instruments) == 2
    equity, etf = instruments
    assert equity.symbol == "AAPL"
    assert equity.asset_class is AssetClass.EQUITY
    assert equity.market_type is MarketType.CASH_EQUITY
    assert equity.quote == "USD"
    assert equity.metadata["mic_code"] == "XNAS"
    assert etf.symbol == "SPY"
    assert etf.metadata["exchange"] == "NYSE Arca"
    assert etf.metadata["mic_code"] == "ARCX"
    assert etf.asset_class is AssetClass.ETF
    assert etf.metadata["fund_family"] == "SPDR"
    assert transport.calls[0][0] == "stocks"
    assert transport.calls[0][1]["country"] == ["United States"]
    assert transport.calls[1][0] == "list"
    assert transport.calls[1][1]["country"] == ["United States"]


def test_discovery_can_be_limited_to_one_asset_class(tmp_path):
    transport = FakeTwelveDataTransport({
        "stocks": {"data": [{"symbol": "MSFT", "currency": "USD"}], "status": "ok"},
    })
    provider = TwelveDataProvider("test-key", opener=transport, state_path=str(tmp_path / "state.db"), min_request_interval_seconds=0)
    instruments = provider.discover_markets((AssetClass.EQUITY,))
    assert [item.symbol for item in instruments] == ["MSFT"]
    assert [call[0] for call in transport.calls] == ["stocks"]


def test_ticker_preserves_missing_bid_ask_instead_of_fabricating_spread(tmp_path):
    transport = FakeTwelveDataTransport({
        "quote": {
            "symbol": "AAPL", "close": "212.50", "timestamp": 1_800_000_000,
            "volume": "1000000", "exchange": "NASDAQ", "currency": "USD",
        }
    })
    provider = TwelveDataProvider("test-key", opener=transport, state_path=str(tmp_path / "state.db"), min_request_interval_seconds=0)
    quote = provider.ticker("AAPL")
    assert quote["last"] == pytest.approx(212.5)
    assert quote["bid"] is None
    assert quote["ask"] is None
    assert quote["timestamp"] == 1_800_000_000_000
    assert quote["baseVolume"] == 1_000_000


def test_ohlcv_converts_exchange_local_timestamps_to_utc_epoch(tmp_path):
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
    provider = TwelveDataProvider("test-key", opener=transport, state_path=str(tmp_path / "state.db"), min_request_interval_seconds=0)
    rows = provider.ohlcv("AAPL", "1h", 2)
    assert len(rows) == 2
    assert rows[0][4] == pytest.approx(10)
    assert rows[1][4] == pytest.approx(11)
    assert rows[0][0] < rows[1][0]
    assert transport.calls[0][1]["interval"] == ["1h"]
    assert transport.calls[0][1]["outputsize"] == ["2"]


def test_provider_errors_are_reported_without_exposing_api_key(tmp_path):
    transport = FakeTwelveDataTransport({
        "quote": {"status": "error", "message": "Invalid API key"},
    }, api_key="secret-value")
    provider = TwelveDataProvider("secret-value", opener=transport, state_path=str(tmp_path / "state.db"), min_request_interval_seconds=0)
    with pytest.raises(MarketDataError, match="Invalid API key") as error:
        provider.ticker("AAPL")
    assert "secret-value" not in str(error.value)


def test_response_cache_avoids_duplicate_quote_requests(tmp_path):
    transport = FakeTwelveDataTransport({
        "quote": {
            "symbol": "AAPL", "close": "212.50", "timestamp": 1_800_000_000,
            "bid": "212.40", "ask": "212.60",
        }
    })
    provider = TwelveDataProvider(
        "test-key", opener=transport, state_path=str(tmp_path / "state.db"),
        min_request_interval_seconds=0,
    )
    first = provider.ticker("AAPL")
    second = provider.ticker("AAPL")
    assert first == second
    assert len(transport.calls) == 1


def test_daily_credit_budget_is_persisted_and_blocks_excess_calls(tmp_path):
    state_path = str(tmp_path / "state.db")
    first_transport = FakeTwelveDataTransport({
        "quote": {"symbol": "AAPL", "close": "212.50", "timestamp": 1_800_000_000},
    })
    first = TwelveDataProvider(
        "test-key", opener=first_transport, state_path=state_path,
        daily_credit_budget=1, min_request_interval_seconds=0,
    )
    first.ticker("AAPL")

    second_transport = FakeTwelveDataTransport({
        "quote": {"symbol": "MSFT", "close": "500", "timestamp": 1_800_000_000},
    })
    second = TwelveDataProvider(
        "test-key", opener=second_transport, state_path=state_path,
        daily_credit_budget=1, min_request_interval_seconds=0,
    )
    with pytest.raises(MarketDataError, match="daily safety budget exhausted"):
        second.ticker("MSFT")
    assert second_transport.calls == []


def test_rate_limiter_waits_for_reserved_slot(tmp_path):
    delays = []
    transport = FakeTwelveDataTransport({
        "quote": {"symbol": "AAPL", "close": "212.50", "timestamp": 1_800_000_000},
    })
    provider = TwelveDataProvider(
        "test-key", opener=transport, state_path=str(tmp_path / "state.db"),
        min_request_interval_seconds=7.5, sleeper=delays.append,
    )
    provider.ticker("AAPL")
    provider.ticker("MSFT")
    assert len(delays) == 1
    assert delays[0] > 0


def test_provider_credit_header_reconciles_daily_usage(tmp_path):
    class Response(io.BytesIO):
        headers = {"api-credits-used": "3"}

    def opener(url, timeout=15):
        return Response(json.dumps({
            "symbol": "AAPL", "close": "212.50", "timestamp": 1_800_000_000,
        }).encode("utf-8"))

    state_path = str(tmp_path / "state.db")
    provider = TwelveDataProvider(
        "test-key", opener=opener, state_path=state_path,
        daily_credit_budget=2, min_request_interval_seconds=0,
    )
    provider.ticker("AAPL")

    # The provider reported three credits although the local estimate was one.
    # The reconciled ledger must block another request against a two-credit budget.
    other = TwelveDataProvider(
        "test-key", opener=opener, state_path=state_path,
        daily_credit_budget=2, min_request_interval_seconds=0,
    )
    with pytest.raises(MarketDataError, match="daily safety budget exhausted"):
        other.ticker("MSFT")


def test_market_state_returns_exchange_lookup_and_uses_cache(tmp_path):
    transport = FakeTwelveDataTransport({
        "market_state": [
            {
                "name": "NASDAQ", "code": "XNAS", "country": "United States",
                "is_market_open": True, "time_to_open": "00:00:00",
                "time_to_close": "03:12:00", "time_after_open": "03:48:00",
            },
            {
                "name": "NYSE", "code": "XNYS", "country": "United States",
                "is_market_open": False, "time_to_open": "12:00:00",
                "time_to_close": "00:00:00", "time_after_open": "00:00:00",
            },
        ]
    })
    provider = TwelveDataProvider(
        "test-key", opener=transport, state_path=str(tmp_path / "state.db"),
        min_request_interval_seconds=0,
    )
    first = provider.market_state()
    second = provider.market_state()
    assert first["nasdaq"]["is_market_open"] is True
    assert first["xnas"]["time_to_close"] == "03:12:00"
    assert first["xnys"]["is_market_open"] is False
    assert second == first
    assert len(transport.calls) == 1
    assert transport.calls[0][0] == "market_state"
    assert transport.calls[0][1]["country"] == ["United States"]


def test_market_state_rejects_unexpected_object_response(tmp_path):
    transport = FakeTwelveDataTransport({
        "market_state": {"status": "ok", "data": []},
    })
    provider = TwelveDataProvider(
        "test-key", opener=transport, state_path=str(tmp_path / "state.db"),
        min_request_interval_seconds=0,
    )
    with pytest.raises(MarketDataError, match="invalid response"):
        provider.market_state()


def test_rolling_minute_credit_budget_waits_before_ninth_credit(tmp_path):
    delays = []
    transport = FakeTwelveDataTransport({
        "quote": {"symbol": "AAPL", "close": "212.50", "timestamp": 1_800_000_000},
    })
    provider = TwelveDataProvider(
        "test-key", opener=transport, state_path=str(tmp_path / "state.db"),
        daily_credit_budget=20, per_minute_credit_budget=8,
        min_request_interval_seconds=0, sleeper=delays.append,
    )
    for index in range(8):
        provider.ticker(f"STOCK{index}")
    provider.ticker("NINTH")
    assert len(transport.calls) == 9
    assert len(delays) == 1
    assert delays[0] >= 59


def test_request_cost_above_minute_budget_is_rejected_before_transport(tmp_path):
    transport = FakeTwelveDataTransport({})
    provider = TwelveDataProvider(
        "test-key", opener=transport, state_path=str(tmp_path / "state.db"),
        per_minute_credit_budget=8, min_request_interval_seconds=0,
    )
    with pytest.raises(MarketDataError, match="exceeds the per-minute safety budget"):
        provider._reserve_api_credit(9)
    assert transport.calls == []


def test_exchange_rate_returns_validated_pair_rate_and_timestamp(tmp_path):
    transport = FakeTwelveDataTransport({
        "exchange_rate": {
            "symbol": "EUR/USD", "rate": "1.0854", "timestamp": 1_800_000_000,
        }
    })
    provider = TwelveDataProvider(
        "test-key", opener=transport, state_path=str(tmp_path / "state.db"),
        min_request_interval_seconds=0,
    )
    rate = provider.exchange_rate("eur/usd")
    assert rate == {
        "symbol": "EUR/USD",
        "base_currency": "EUR",
        "quote_currency": "USD",
        "rate": pytest.approx(1.0854),
        "timestamp": 1_800_000_000_000,
        "provider_id": "twelve_data",
    }
    assert transport.calls[0][0] == "exchange_rate"
    assert transport.calls[0][1]["symbol"] == ["EUR/USD"]


def test_exchange_rate_is_cached_to_conserve_provider_credits(tmp_path):
    transport = FakeTwelveDataTransport({
        "exchange_rate": {
            "symbol": "EUR/USD", "rate": "1.08", "timestamp": 1_800_000_000,
        }
    })
    provider = TwelveDataProvider(
        "test-key", opener=transport, state_path=str(tmp_path / "state.db"),
        min_request_interval_seconds=0,
    )
    assert provider.exchange_rate() == provider.exchange_rate()
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"symbol": "USD/EUR", "rate": "1.08", "timestamp": 1_800_000_000},
        {"symbol": "EUR/USD", "rate": "0", "timestamp": 1_800_000_000},
        {"symbol": "EUR/USD", "rate": "-1", "timestamp": 1_800_000_000},
        {"symbol": "EUR/USD", "rate": "nan", "timestamp": 1_800_000_000},
        {"symbol": "EUR/USD", "rate": "inf", "timestamp": 1_800_000_000},
        {"symbol": "EUR/USD", "rate": "1.08"},
    ],
)
def test_exchange_rate_rejects_invalid_provider_observations(tmp_path, payload):
    provider = TwelveDataProvider(
        "test-key", opener=FakeTwelveDataTransport({"exchange_rate": payload}),
        state_path=str(tmp_path / "state.db"), min_request_interval_seconds=0,
    )
    with pytest.raises(MarketDataError):
        provider.exchange_rate()


@pytest.mark.parametrize("symbol", ["EUR", "EURUSD", "EUR/USD/GBP", "EU1/USD"])
def test_exchange_rate_rejects_invalid_pair_identifiers(tmp_path, symbol):
    provider = TwelveDataProvider(
        "test-key", opener=FakeTwelveDataTransport({}),
        state_path=str(tmp_path / "state.db"), min_request_interval_seconds=0,
    )
    with pytest.raises(MarketDataError, match="three-letter currency pair"):
        provider.exchange_rate(symbol)
