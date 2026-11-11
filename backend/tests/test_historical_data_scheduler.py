from types import SimpleNamespace

import pytest

from app.historical_data import HistoricalCandleStore
from app.historical_data_scheduler import (
    HistoricalSyncStateStore,
    HistoricalSyncTarget,
    parse_sync_targets,
    run_sync_cycle,
)


def candle(ts, close=10):
    return [ts, close, close + 2, close - 1, close + 1, 100]


def test_parse_sync_targets_requires_explicit_ccxt_allowlist():
    assert parse_sync_targets(
        "ccxt:kraken,BTC/EUR,1h;ccxt:binance,ETH/EUR,4h"
    ) == [
        HistoricalSyncTarget("ccxt:kraken", "BTC/EUR", "1h"),
        HistoricalSyncTarget("ccxt:binance", "ETH/EUR", "4h"),
    ]


@pytest.mark.parametrize("value", [
    "BTC/EUR,1h",
    "ccxt:kraken,BTC/EUR",
    "ccxt:kraken,BTC/EUR,1h,extra",
    "ccxt:kraken,BTC/EUR,1h;ccxt:kraken,BTC/EUR,1h",
    "twelve_data,AAPL,1day",
])
def test_parse_sync_targets_rejects_invalid_or_unsupported_targets(value):
    with pytest.raises(ValueError):
        parse_sync_targets(value)


def test_sync_state_cursor_survives_scheduler_restart(tmp_path):
    path = str(tmp_path / "trading.sqlite")
    first = HistoricalSyncStateStore(path)
    row, created = first.ensure_target(
        "ccxt:kraken", "ccxt:kraken:spot:BTC/EUR", "BTC/EUR", "1h", 1000,
    )
    assert created is True
    assert row["next_since_ms"] == 1000

    first.record_result(
        "ccxt:kraken", "ccxt:kraken:spot:BTC/EUR", "1h",
        {"next_since_ms": 5000, "pages": 2, "received": 100, "inserted": 100, "updated": 0},
        status="backfill_in_progress",
    )
    restarted = HistoricalSyncStateStore(path)
    state = restarted.list_targets()[0]
    assert state["next_since_ms"] == 5000
    assert state["last_status"] == "backfill_in_progress"
    assert state["last_inserted"] == 100


def test_sync_cycle_resumes_backfill_from_persisted_cursor(tmp_path):
    path = str(tmp_path / "trading.sqlite")
    store = HistoricalCandleStore(path)
    state = HistoricalSyncStateStore(path)
    target = HistoricalSyncTarget("ccxt:kraken", "BTC/EUR", "1m")
    rows = [candle(ts) for ts in (60_000, 120_000, 180_000, 240_000, 300_000)]
    calls = []

    class Exchange:
        @staticmethod
        def parse_timeframe(timeframe):
            assert timeframe == "1m"
            return 60

        def fetch_ohlcv(self, symbol, timeframe, since, limit):
            calls.append(since)
            return [row for row in rows if row[0] >= since][:limit]

    provider = SimpleNamespace(
        exchange=Exchange(),
        discover_markets=lambda: [SimpleNamespace(
            symbol="BTC/EUR", active=True,
            instrument_id="ccxt:kraken:spot:BTC/EUR",
        )],
    )
    providers = {"ccxt:kraken": provider}

    first = run_sync_cycle(
        [target], providers, store, state, now_ms=86_500_000,
        lookback_days=1, page_limit=2, max_pages_per_target=1,
    )
    assert first[0]["status"] == "backfill_in_progress"
    assert state.list_targets()[0]["next_since_ms"] == 180_000

    second = run_sync_cycle(
        [target], providers, store, state, now_ms=86_500_000,
        lookback_days=1, page_limit=2, max_pages_per_target=1,
    )
    assert second[0]["status"] == "backfill_in_progress"
    assert calls == [60_000, 180_000]
    assert len(store.get("ccxt:kraken", "ccxt:kraken:spot:BTC/EUR", "1m")) == 4


def test_sync_cycle_refreshes_latest_provisional_candle(tmp_path):
    path = str(tmp_path / "trading.sqlite")
    store = HistoricalCandleStore(path)
    state = HistoricalSyncStateStore(path)
    target = HistoricalSyncTarget("ccxt:kraken", "BTC/EUR", "1m")
    instrument_id = "ccxt:kraken:spot:BTC/EUR"
    latest_timestamp = 86_460_000
    store.upsert(
        "ccxt:kraken", instrument_id, "BTC/EUR", "1m",
        [candle(latest_timestamp, 10)],
        finalized_before_ms=latest_timestamp,
    )
    state.ensure_target(
        "ccxt:kraken", instrument_id, "BTC/EUR", "1m", 60_000,
    )
    state.record_result(
        "ccxt:kraken", instrument_id, "1m",
        {"next_since_ms": None, "pages": 1, "received": 1, "inserted": 1, "updated": 0},
        status="caught_up",
    )
    calls = []

    class Exchange:
        @staticmethod
        def parse_timeframe(timeframe):
            return 60

        def fetch_ohlcv(self, symbol, timeframe, since, limit):
            calls.append(since)
            return [candle(latest_timestamp, 11)] if since <= latest_timestamp else []

    provider = SimpleNamespace(
        exchange=Exchange(),
        discover_markets=lambda: [SimpleNamespace(
            symbol="BTC/EUR", active=True, instrument_id=instrument_id,
        )],
    )
    result = run_sync_cycle(
        [target], {"ccxt:kraken": provider}, store, state,
        now_ms=86_520_000, lookback_days=1, page_limit=10,
    )
    assert calls == [latest_timestamp]
    assert result[0]["status"] == "caught_up"
    saved = store.get("ccxt:kraken", instrument_id, "1m", limit=1)[0]
    assert saved["close"] == "12"
    assert saved["is_final"] is False


def test_history_storage_stats_make_no_pruning_policy_explicit(tmp_path):
    store = HistoricalCandleStore(str(tmp_path / "history.sqlite"))
    store.upsert(
        "p", "i", "BTC/EUR", "1m", [candle(1000)],
        finalized_before_ms=2000,
    )
    stats = store.storage_stats()
    assert stats["bars"] == 1
    assert stats["series"] == 1
    assert stats["database_size_bytes"] > 0
    assert stats["retention_policy"] == "retain_indefinitely"
    assert stats["automatic_pruning"] is False


def test_parse_sync_targets_preserves_case_sensitive_exchange_timeframes():
    assert parse_sync_targets("ccxt:kraken,BTC/EUR,1M;ccxt:kraken,BTC/EUR,1m") == [
        HistoricalSyncTarget("ccxt:kraken", "BTC/EUR", "1M"),
        HistoricalSyncTarget("ccxt:kraken", "BTC/EUR", "1m"),
    ]


def test_sync_cycle_rejects_calendar_month_timeframe_for_fixed_interval_cursor(tmp_path):
    path = str(tmp_path / "trading.sqlite")
    store = HistoricalCandleStore(path)
    state = HistoricalSyncStateStore(path)
    target = HistoricalSyncTarget("ccxt:kraken", "BTC/EUR", "1M")

    class Exchange:
        @staticmethod
        def parse_timeframe(timeframe):
            return 30 * 86400

        def fetch_ohlcv(self, *args, **kwargs):
            raise AssertionError("Unsupported monthly timeframe must not fetch")

    provider = SimpleNamespace(
        exchange=Exchange(),
        discover_markets=lambda: [SimpleNamespace(
            symbol="BTC/EUR", active=True,
            instrument_id="ccxt:kraken:spot:BTC/EUR",
        )],
    )
    result = run_sync_cycle(
        [target], {"ccxt:kraken": provider}, store, state,
        now_ms=86_500_000, lookback_days=1,
    )
    assert result[0]["status"] == "error"
    assert "historical sync target failed" in result[0]["error"]
    assert state.list_targets() == []
