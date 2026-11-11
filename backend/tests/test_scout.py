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
        def research(self, candles, **kwargs):
            return {"status": "ok", "bars_seen": len(candles), "research_kwargs": kwargs}

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
    assert research["research_kwargs"]["expected_interval_seconds"] == 3600
    assert research["research_kwargs"]["calendar_model"] == "crypto_24x7"


def test_scout_falls_back_to_recent_provider_history_when_store_is_unavailable():
    import sqlite3

    class UnavailableStore:
        def get(self, *args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

    class Research:
        def research(self, candles, **kwargs):
            return {"status": "ok", "bars_seen": len(candles), "research_kwargs": kwargs}

    result = ScoutAgent(
        FakeMarketData(),
        historical_store=UnavailableStore(),
        strategy_research=Research(),
    ).scan()
    research = result["analyses"][0]["strategy_research"]
    assert research["bars_seen"] == 60
    assert research["history_source"] == "provider_recent_store_unavailable"
    assert research["research_kwargs"]["calendar_model"] == "crypto_24x7"


def test_scout_persists_and_passes_observed_bid_ask_history_to_research():
    timestamp = 1_700_000_000_000 + 10 * 60_000

    class QuotedMarketData(FakeMarketData):
        def discover_spot_symbols(self, quote="EUR", limit=5):
            return [{
                "symbol": "BTC/EUR",
                "provider_id": "ccxt:kraken",
                "instrument_id": "ccxt:kraken:spot:BTC/EUR",
                "last": 100.0,
                "bid": 99.9,
                "ask": 100.1,
                "spread_fraction": 0.002,
                "quote_observed": True,
                "ticker_timestamp_ms": timestamp,
                "ticker_timestamp_source": "retrieved_at",
                "quote_volume_24h": 10000,
            }]

    class QuoteStore:
        def __init__(self):
            self.rows = []

        def record(self, provider_id, instrument_id, symbol, timestamp_ms, bid, ask, **kwargs):
            self.rows.append({
                "provider_id": provider_id,
                "instrument_id": instrument_id,
                "symbol": symbol,
                "timestamp_ms": timestamp_ms,
                "bid": str(bid),
                "ask": str(ask),
                "timestamp_source": kwargs["timestamp_source"],
                "source": "public_ticker",
            })
            return {"status": "inserted"}

        def get(self, provider_id, instrument_id, **kwargs):
            return list(self.rows)

        def coverage(self, provider_id, instrument_id):
            return {"observations": len(self.rows), "status": "available"}

    class Research:
        def research(self, candles, **kwargs):
            return {
                "status": "ok",
                "quote_count": len(kwargs["quote_snapshots"]),
                "research_kwargs": kwargs,
            }

    quote_store = QuoteStore()
    result = ScoutAgent(
        QuotedMarketData(),
        quote_store=quote_store,
        strategy_research=Research(),
    ).scan()

    research = result["analyses"][0]["strategy_research"]
    assert len(quote_store.rows) == 1
    assert quote_store.rows[0]["timestamp_source"] == "retrieved_at"
    assert research["quote_capture_status"] == "inserted"
    assert research["quote_history_status"] == "available"
    assert research["quote_observations_available"] == 1
    assert research["quote_count"] == 1


def test_scout_does_not_record_synthetic_zero_spread_for_missing_bid_ask():
    from datetime import datetime, timezone

    class HeldWithoutQuote(FakeMarketData):
        def discover_spot_symbols(self, quote="EUR", limit=5):
            return []

        def ticker(self, symbol):
            return {
                "symbol": symbol,
                "last": 101.0,
                "bid": None,
                "ask": None,
                "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
            }

        @staticmethod
        def is_stale(ticker, max_age_seconds):
            return False

    class QuoteStore:
        def __init__(self):
            self.records = []

        def record(self, *args, **kwargs):
            self.records.append((args, kwargs))
            return {"status": "inserted"}

        def get(self, *args, **kwargs):
            return []

        def coverage(self, *args, **kwargs):
            return {"observations": 0, "status": "empty"}

    quote_store = QuoteStore()
    result = ScoutAgent(HeldWithoutQuote(), quote_store=quote_store).scan(
        include_symbols=["BTC/EUR"],
    )
    market = result["analyses"][0]["market"]
    assert market["quote_observed"] is False
    assert market["spread_fraction"] is None
    assert quote_store.records == []


def test_scout_does_not_assign_missing_quote_identity_to_default_provider():
    class MissingIdentityMarketData(FakeMarketData):
        def discover_spot_symbols(self, quote="EUR", limit=5):
            return [{
                "symbol": "BTC/EUR",
                "last": 100.0,
                "bid": 99.9,
                "ask": 100.1,
                "spread_fraction": 0.002,
                "quote_observed": True,
                "ticker_timestamp_ms": 1_700_000_000_000,
                "ticker_timestamp_source": "exchange",
                "quote_volume_24h": 10000,
            }]

    class QuoteStore:
        def __init__(self):
            self.records = []

        def record(self, *args, **kwargs):
            self.records.append((args, kwargs))
            return {"status": "inserted"}

        def get(self, *args, **kwargs):
            raise AssertionError("Quote history must not be queried without provider identity")

        def coverage(self, *args, **kwargs):
            raise AssertionError("Quote coverage must not be queried without provider identity")

    class Research:
        def research(self, candles, **kwargs):
            assert kwargs["quote_snapshots"] == []
            return {"status": "ok"}

    quote_store = QuoteStore()
    result = ScoutAgent(
        MissingIdentityMarketData(),
        quote_store=quote_store,
        strategy_research=Research(),
    ).scan()

    research = result["analyses"][0]["strategy_research"]
    assert quote_store.records == []
    assert research["quote_capture_status"] == "identity_missing"
    assert research["quote_history_status"] == "identity_missing"
