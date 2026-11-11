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
    assert result["universe"] == "configured_provider_crypto_spot_EUR"
    assert result["discovered"] == 1
    assert result["analysed"] == 1
    assert result["analyses"][0]["analysis"]["symbol"] == "BTC/EUR"
    assert result["analyses"][0]["strategy_research"]["status"] == "insufficient_or_invalid_history"
    assert result["analyses"][0]["strategy_research"]["diagnostic_code"] == "insufficient_history"
    assert result["analyses"][0]["strategy_research"]["failure_category"] == "sample"
    assert result["analyses"][0]["strategy_research"]["sample_adequacy"]["available_bars"] == 60
    assert result["execution"] == "analysis_only"


def test_scout_records_market_with_insufficient_history():
    class ShortHistory(FakeMarketData):
        def ohlcv(self, symbol, timeframe="1h", limit=100):
            return []

    result = ScoutAgent(ShortHistory()).scan()
    assert result["analysed"] == 0
    assert result["rejected"][0]["symbol"] == "BTC/EUR"


def test_scout_includes_open_positions_for_exit_monitoring():
    from datetime import datetime, timezone

    class HeldPositionMarketData(FakeMarketData):
        def discover_spot_symbols(self, quote="EUR", limit=5):
            return []

        def ticker(self, symbol):
            return {
                "symbol": symbol,
                "last": 101.0,
                "bid": 100.9,
                "ask": 101.1,
                "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
            }

        @staticmethod
        def is_stale(ticker, max_age_seconds):
            return False

    result = ScoutAgent(HeldPositionMarketData()).scan(include_symbols=["BTC/EUR"])
    assert result["discovered"] == 1
    assert result["analysed"] == 1
    assert result["analyses"][0]["market"]["included_open_position"] is True


def test_scout_uses_deeper_finalized_persisted_history_for_strategy_research():
    class HistoricalStore:
        def get(self, provider_id, instrument_id, timeframe, *, limit):
            assert provider_id == "ccxt:kraken"
            assert instrument_id == "ccxt:kraken:spot:BTC/EUR"
            assert timeframe == "1h"
            assert limit == 5000
            return [
                {
                    "timestamp_ms": 1_700_000_000_000 + index * 3_600_000,
                    "open": "100", "high": "102", "low": "99",
                    "close": "101", "volume": "10", "is_final": True,
                }
                for index in range(80)
            ]

    class Research:
        def research(self, candles):
            return {"status": "ok", "bars_seen": len(candles)}

    result = ScoutAgent(
        FakeMarketData(),
        historical_store=HistoricalStore(),
        strategy_research=Research(),
    ).scan()

    research = result["analyses"][0]["strategy_research"]
    assert research["bars_seen"] == 80
    assert research["history_bars"] == 80
    assert research["persisted_finalized_bars_available"] == 80
    assert research["history_source"] == "persistent_finalized_ohlcv"


def test_scout_falls_back_to_recent_provider_history_when_store_is_unavailable():
    import sqlite3

    class UnavailableStore:
        def get(self, *args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

    class Research:
        def research(self, candles):
            return {"status": "ok", "bars_seen": len(candles)}

    result = ScoutAgent(
        FakeMarketData(),
        historical_store=UnavailableStore(),
        strategy_research=Research(),
    ).scan()
    research = result["analyses"][0]["strategy_research"]
    assert research["bars_seen"] == 60
    assert research["history_source"] == "provider_recent_store_unavailable"
