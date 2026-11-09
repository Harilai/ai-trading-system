from datetime import datetime, timezone

from app.market_data import CcxtMarketData


class FakeExchange:
    has = {"fetchTickers": True}

    def __init__(self, markets, tickers):
        self.markets = markets
        self.tickers = tickers

    def load_markets(self):
        return self.markets

    def fetch_tickers(self, symbols):
        return {symbol: self.tickers.get(symbol) for symbol in symbols}


def ticker(now_ms, **overrides):
    values = {
        "timestamp": now_ms,
        "last": 100.0,
        "bid": 99.9,
        "ask": 100.1,
        "quoteVolume": 5000.0,
    }
    values.update(overrides)
    return values


def test_discovery_skips_missing_inactive_stale_wide_and_malformed_tickers():
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    symbols = [
        "GOOD/EUR",
        "MISSING/EUR",
        "INACTIVE/EUR",
        "WIDE/EUR",
        "STALE/EUR",
        "FUTURE/EUR",
        "NAN/EUR",
        "MALFORMED/EUR",
        "LOWVOL/EUR",
        "NEGATIVE/EUR",
        "CROSSED/EUR",
    ]
    markets = {
        symbol: {
            "spot": True,
            "active": symbol != "INACTIVE/EUR",
            "quote": "EUR",
        }
        for symbol in symbols
    }
    tickers = {
        "GOOD/EUR": ticker(now_ms),
        "MISSING/EUR": {"timestamp": now_ms, "last": 100, "bid": 99.9},
        "WIDE/EUR": ticker(now_ms, bid=90.0, ask=110.0),
        "STALE/EUR": ticker(now_ms - 121_000),
        "FUTURE/EUR": ticker(now_ms + 31_000),
        "NAN/EUR": ticker(now_ms, last=float("nan")),
        "MALFORMED/EUR": ticker(now_ms, ask="not-a-price"),
        "LOWVOL/EUR": ticker(now_ms, quoteVolume=10),
        "NEGATIVE/EUR": ticker(now_ms, quoteVolume=-1),
        "CROSSED/EUR": ticker(now_ms, bid=101, ask=100),
    }
    adapter = CcxtMarketData.__new__(CcxtMarketData)
    adapter.exchange = FakeExchange(markets, tickers)

    results = adapter.discover_spot_symbols(
        quote="EUR",
        limit=20,
        max_spread_fraction=0.02,
        min_quote_volume=1000,
    )

    assert [item["symbol"] for item in results] == ["GOOD/EUR"]


def test_discovery_can_estimate_quote_volume_from_base_volume():
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    adapter = CcxtMarketData.__new__(CcxtMarketData)
    adapter.exchange = FakeExchange(
        {"BASEVOL/EUR": {"spot": True, "active": True, "quote": "EUR"}},
        {"BASEVOL/EUR": ticker(now_ms, quoteVolume=None, baseVolume=50)},
    )

    results = adapter.discover_spot_symbols()

    assert len(results) == 1
    assert results[0]["quote_volume_24h"] == 5000.0


def test_discovery_fails_closed_when_provider_returns_non_mapping_ticker():
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    adapter = CcxtMarketData.__new__(CcxtMarketData)
    adapter.exchange = FakeExchange(
        {"BROKEN/EUR": {"spot": True, "active": True, "quote": "EUR"}},
        {"BROKEN/EUR": ["unexpected", "payload"]},
    )

    assert adapter.discover_spot_symbols() == []
