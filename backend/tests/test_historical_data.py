from pathlib import Path

import pytest

from app.historical_data import (
    HistoricalCandleStore, HistoricalDataConflict, HistoricalDataError,
    PaginatedCandleCollector,
)


def row(ts, close=10):
    return [ts, close, close + 2, close - 1, close + 1, 100]


def store(tmp_path):
    return HistoricalCandleStore(str(tmp_path / "history.sqlite"))


def test_store_is_idempotent_and_scoped_by_provider_instrument_and_timeframe(tmp_path):
    db = store(tmp_path)
    candles = [row(1000), row(2000)]
    first = db.upsert("ccxt:kraken", "kraken:spot:BTC/EUR", "BTC/EUR", "1s",
                      candles, finalized_before_ms=3000)
    replay = db.upsert("ccxt:kraken", "kraken:spot:BTC/EUR", "BTC/EUR", "1s",
                       candles, finalized_before_ms=3000)
    assert first["inserted"] == 2
    assert replay["unchanged"] == 2
    assert len(db.get("ccxt:kraken", "kraken:spot:BTC/EUR", "1s")) == 2
    assert db.get("ccxt:other", "kraken:spot:BTC/EUR", "1s") == []
    assert db.get("ccxt:kraken", "kraken:spot:BTC/EUR", "1m") == []


def test_provisional_candle_can_be_refreshed_then_finalized(tmp_path):
    db = store(tmp_path)
    db.upsert("p", "i", "BTC/EUR", "1m", [row(1000)], finalized_before_ms=1000)
    changed = db.upsert("p", "i", "BTC/EUR", "1m", [row(1000, 11)], finalized_before_ms=3000)
    assert changed["updated"] == 1
    assert db.get("p", "i", "1m")[0]["close"] == "12"
    assert db.get("p", "i", "1m")[0]["is_final"] is True


def test_conflicting_finalized_candle_is_rejected_atomically(tmp_path):
    db = store(tmp_path)
    db.upsert("p", "i", "BTC/EUR", "1m", [row(1000), row(2000)],
              finalized_before_ms=3000)
    with pytest.raises(HistoricalDataConflict):
        db.upsert("p", "i", "BTC/EUR", "1m", [row(1000, 12), row(3000)],
                  finalized_before_ms=4000)
    assert len(db.get("p", "i", "1m")) == 2


@pytest.mark.parametrize("bad", [
    [1000, 0, 2, 1, 1, 1],
    [1000, 1, 0, 0, 1, 1],
    [1000, 1, 2, 0, 1, -1],
    [1000, 1, 2, 3, 1, 1],
    [1000, 1, float("nan"), 0, 1, 1],
])
def test_invalid_ohlcv_is_rejected(tmp_path, bad):
    with pytest.raises(HistoricalDataError):
        store(tmp_path).upsert("p", "i", "BTC/EUR", "1m", [bad],
                               finalized_before_ms=2000)


def test_pagination_fetches_forward_without_duplicate_rows(tmp_path):
    db = store(tmp_path)
    all_rows = [row(ts) for ts in (1000, 2000, 3000, 4000, 5000)]
    calls = []

    def fetch(symbol, timeframe, since, limit):
        calls.append(since)
        return [item for item in all_rows if item[0] >= since][:2]

    collector = PaginatedCandleCollector(db, page_limit=2, max_pages=10)
    result = collector.collect("p", "i", "BTC/EUR", "1s", 1000, 6000, fetch,
                               candle_interval_ms=1000, finalized_before_ms=7000)
    assert result["complete"] is True
    assert result["pages"] == 3
    assert calls == [1000, 3000, 5000]
    assert result["inserted"] == 5
    assert len(db.get("p", "i", "1s")) == 5


def test_pagination_is_bounded_and_returns_resume_cursor(tmp_path):
    db = store(tmp_path)
    all_rows = [row(ts) for ts in range(1000, 10000, 1000)]
    collector = PaginatedCandleCollector(db, page_limit=2, max_pages=2)
    result = collector.collect(
        "p", "i", "BTC/EUR", "1s", 1000, 9000,
        lambda symbol, timeframe, since, limit: [r for r in all_rows if r[0] >= since][:2],
        candle_interval_ms=1000, finalized_before_ms=10000,
    )
    assert result["complete"] is False
    assert result["next_since_ms"] == 5000
    assert result["pages"] == 2


def test_pagination_rejects_provider_that_does_not_progress(tmp_path):
    collector = PaginatedCandleCollector(store(tmp_path), page_limit=2)
    with pytest.raises(HistoricalDataError, match="timestamps"):
        collector.collect(
            "p", "i", "BTC/EUR", "1s", 1000, 5000,
            lambda *args, **kwargs: [row(1000), row(1000)],
            candle_interval_ms=1000, finalized_before_ms=5000,
        )


def test_range_query_and_coverage_report(tmp_path):
    db = store(tmp_path)
    db.upsert("p", "i", "BTC/EUR", "1m", [row(1000), row(2000), row(3000)],
              finalized_before_ms=2500)
    assert [x["timestamp_ms"] for x in db.get("p", "i", "1m", since_ms=2000, until_ms=4000)] == [2000, 3000]
    assert db.coverage("p", "i", "1m") == {
        "bars": 3, "first_timestamp_ms": 1000, "last_timestamp_ms": 3000,
        "provisional_bars": 1, "status": "available",
    }


def test_stale_replay_cannot_demote_finalized_candle(tmp_path):
    db = store(tmp_path)
    db.upsert("p", "i", "BTC/EUR", "1m", [row(1000)],
              finalized_before_ms=2000)
    replay = db.upsert("p", "i", "BTC/EUR", "1m", [row(1000)],
                       finalized_before_ms=1000)
    assert replay["unchanged"] == 1
    assert db.get("p", "i", "1m")[0]["is_final"] is True


def test_pagination_rejects_out_of_order_provider_rows(tmp_path):
    collector = PaginatedCandleCollector(store(tmp_path), page_limit=2)
    with pytest.raises(HistoricalDataError, match="non-increasing"):
        collector.collect(
            "p", "i", "BTC/EUR", "1s", 1000, 5000,
            lambda *args, **kwargs: [row(2000), row(1000)],
            candle_interval_ms=1000, finalized_before_ms=5000,
        )


def test_pagination_completes_when_provider_reaches_requested_end(tmp_path):
    collector = PaginatedCandleCollector(store(tmp_path), page_limit=2)
    result = collector.collect(
        "p", "i", "BTC/EUR", "1s", 1000, 3000,
        lambda *args, **kwargs: [row(3000), row(4000)],
        candle_interval_ms=1000, finalized_before_ms=5000,
    )
    assert result["complete"] is True
    assert result["next_since_ms"] is None


def test_limited_history_query_returns_latest_rows_in_chronological_order(tmp_path):
    db = store(tmp_path)
    db.upsert("p", "i", "BTC/EUR", "1m",
              [row(1000), row(2000), row(3000), row(4000)],
              finalized_before_ms=5000)
    assert [item["timestamp_ms"] for item in db.get("p", "i", "1m", limit=2)] == [3000, 4000]


def test_decimal_formatting_is_canonical_for_idempotent_replays(tmp_path):
    db = store(tmp_path)
    db.upsert("p", "i", "BTC/EUR", "1m",
              [[1000, "10.000", "12.00", "9.0", "11.000", "100.0"]],
              finalized_before_ms=2000)
    replay = db.upsert("p", "i", "BTC/EUR", "1m",
                       [[1000, 10, 12, 9, 11, 100]],
                       finalized_before_ms=2000)
    assert replay["unchanged"] == 1


def test_fractional_candle_timestamp_is_rejected(tmp_path):
    with pytest.raises(HistoricalDataError, match="timestamp must be an integer"):
        store(tmp_path).upsert("p", "i", "BTC/EUR", "1m",
                               [[1000.5, 10, 12, 9, 11, 100]],
                               finalized_before_ms=2000)
