from app.scout import ScoutAgent


class FakeMarketData:
    def discover_spot_symbols(self, quote="EUR", limit=5):
        assert quote == "EUR"
        return [{"symbol": "BTC/EUR", "quote_volume_24h": 10000}][:limit]

    def ohlcv(self, symbol, timeframe="1h", limit=100):
        return [
            [1_700_000_000_000 + i * 60_000, 100 + i, 102 + i, 99 + i, 101 + i, 10]
            for i in range(60)
        ]


def test_scout_discovers_and_analyses_markets():
    result = ScoutAgent(FakeMarketData()).scan()
    assert result["universe"] == "active_spot_EUR"
    assert result["discovered"] == 1
    assert result["analysed"] == 1
    assert result["analyses"][0]["analysis"]["symbol"] == "BTC/EUR"
    assert result["analyses"][0]["strategy_research"]["status"] == "insufficient_or_invalid_history"
    assert result["execution"] == "analysis_only"


def test_scout_records_market_with_insufficient_history():
    class ShortHistory(FakeMarketData):
        def ohlcv(self, symbol, timeframe="1h", limit=100):
            return []

    result = ScoutAgent(ShortHistory()).scan()
    assert result["analysed"] == 0
    assert result["rejected"][0]["symbol"] == "BTC/EUR"
