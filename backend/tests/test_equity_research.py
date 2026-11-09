from datetime import datetime, timezone

import pytest

from app.equity_research import EquityEtfResearchAgent
from app.providers.base import AssetClass, MarketInstrument, MarketType


def make_instrument(symbol="AAPL", asset_class=AssetClass.EQUITY, quote="USD"):
    return MarketInstrument(
        provider_id="twelve_data",
        symbol=symbol,
        asset_class=asset_class,
        market_type=MarketType.CASH_EQUITY,
        base=symbol,
        quote=quote,
        active=True,
        metadata={"exchange": "NASDAQ", "country": "United States"},
    )


def make_candles(count=240):
    return [
        [
            1_700_000_000_000 + i * 86_400_000,
            100 + i * 0.05,
            102 + i * 0.05,
            99 + i * 0.05,
            101 + i * 0.05,
            10000,
        ]
        for i in range(count)
    ]


class FakeEquityProvider:
    provider_id = "twelve_data"

    def __init__(self, instruments=None, quote=None, candles=None, states=None):
        self.instruments = instruments or [make_instrument()]
        self.quote = quote or {
            "last": 210.0,
            "bid": 209.9,
            "ask": 210.1,
            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
            "averageVolume": 100000,
            "is_market_open": True,
        }
        self.candles = candles if candles is not None else make_candles()
        self.calls = []
        self.states = states

    def market_state(self):
        if self.states is None:
            return {}
        return self.states

    def discover_markets(self, asset_classes=None):
        self.calls.append(("discover", asset_classes))
        return self.instruments

    def ticker(self, symbol):
        self.calls.append(("ticker", symbol))
        return self.quote

    def ohlcv(self, symbol, timeframe="1day", limit=250):
        self.calls.append(("ohlcv", symbol, timeframe, limit))
        return self.candles


def test_equity_research_analyses_stock_and_etf_without_execution():
    provider = FakeEquityProvider([
        make_instrument("AAPL", AssetClass.EQUITY),
        make_instrument("SPY", AssetClass.ETF),
    ])
    result = EquityEtfResearchAgent(provider, max_instruments=2).scan()
    assert result["universe"] == "twelve_data_us_equities_and_etfs"
    assert result["discovered"] == 2
    assert result["analysed"] == 2
    assert result["execution"] == "disabled_research_only"
    assert all(item["research_eligible"] is True for item in result["analyses"])
    assert all(item["execution_eligible"] is False for item in result["analyses"])
    assert all(item["strategy_research"]["candidate_count"] == 3 for item in result["analyses"])
    assert {item["instrument"]["asset_class"] for item in result["analyses"]} == {"equity", "etf"}


def test_missing_bid_ask_does_not_block_historical_research_but_blocks_execution():
    provider = FakeEquityProvider(quote={
        "last": 210.0, "bid": None, "ask": None,
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        "is_market_open": True,
    })
    result = EquityEtfResearchAgent(provider).scan()
    assert result["analysed"] == 1
    analysis = result["analyses"][0]
    assert analysis["research_eligible"] is True
    assert analysis["execution_eligible"] is False
    assert "Two-sided bid/ask quote is unavailable" in analysis["execution_blockers"]


def test_closed_or_unknown_market_is_reported_as_execution_blocker():
    closed = FakeEquityProvider(quote={
        "last": 210.0, "bid": 209.9, "ask": 210.1,
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        "is_market_open": False,
        "averageVolume": 100000,
    })
    result = EquityEtfResearchAgent(closed).scan()
    assert result["analysed"] == 1
    assert "US exchange is currently reported closed" in result["analyses"][0]["execution_blockers"]

    unknown = FakeEquityProvider(quote={
        "last": 210.0, "bid": 209.9, "ask": 210.1,
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        "averageVolume": 100000,
    })
    result = EquityEtfResearchAgent(unknown).scan()
    assert "Exchange session state is unknown" in result["analyses"][0]["execution_blockers"]


def test_wide_spread_blocks_execution_without_discarding_research():
    provider = FakeEquityProvider(quote={
        "last": 210.0, "bid": 200.0, "ask": 220.0,
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        "averageVolume": 100000,
        "is_market_open": True,
    })
    result = EquityEtfResearchAgent(provider).scan()
    assert result["analysed"] == 1
    assert "Bid/ask spread exceeds the execution-quality threshold" in result["analyses"][0]["execution_blockers"]


def test_equity_research_can_strictly_require_two_sided_quote():
    provider = FakeEquityProvider(quote={
        "last": 210.0, "bid": None, "ask": None,
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
    })
    result = EquityEtfResearchAgent(provider, require_two_sided_quote=True).scan()
    assert result["analysed"] == 0
    assert "Two-sided bid/ask quote required" in result["rejected"][0]["reason"]


def test_equity_research_rejects_stale_quotes():
    provider = FakeEquityProvider(quote={
        "last": 210.0, "bid": 209.9, "ask": 210.1,
        "timestamp": 1,
    })
    result = EquityEtfResearchAgent(provider).scan()
    assert result["analysed"] == 0
    assert result["rejected"][0]["reason"] == "Quote is stale"


def test_equity_research_rejects_non_usd_instruments():
    provider = FakeEquityProvider([make_instrument("SAP", AssetClass.EQUITY, "EUR")])
    result = EquityEtfResearchAgent(provider).scan()
    assert result["analysed"] == 0
    assert result["rejected"][0]["reason"] == "Only USD listings are enabled"


def test_equity_research_rejects_insufficient_history():
    provider = FakeEquityProvider(candles=make_candles(80))
    result = EquityEtfResearchAgent(provider).scan()
    assert result["analysed"] == 0
    assert "At least 120 candles" in result["rejected"][0]["reason"]


def test_equity_research_respects_instrument_scan_limit():
    provider = FakeEquityProvider([
        make_instrument("AAPL"),
        make_instrument("MSFT"),
        make_instrument("SPY", AssetClass.ETF),
    ])
    result = EquityEtfResearchAgent(provider, max_instruments=2).scan()
    assert result["discovered"] == 3
    assert result["considered"] == 2
    assert result["analysed"] == 2


def test_equity_research_rejects_invalid_configuration():
    with pytest.raises(ValueError, match="max_instruments"):
        EquityEtfResearchAgent(FakeEquityProvider(), max_instruments=0)
    with pytest.raises(ValueError, match="spread"):
        EquityEtfResearchAgent(FakeEquityProvider(), max_execution_spread_fraction=0)


def test_exchange_market_state_endpoint_overrides_quote_flag():
    provider = FakeEquityProvider(
        quote={
            "last": 210.0, "bid": 209.9, "ask": 210.1,
            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
            "averageVolume": 100000,
            "is_market_open": False,
        },
        states={"nasdaq": {"name": "NASDAQ", "code": "XNAS", "is_market_open": True}},
    )
    result = EquityEtfResearchAgent(provider).scan()
    analysis = result["analyses"][0]
    assert analysis["quote"]["is_market_open"] is True
    assert analysis["quote"]["exchange_session"]["code"] == "XNAS"
    assert "US exchange is currently reported closed" not in analysis["execution_blockers"]
