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


def history_capabilities(provider_id="ccxt:kraken", timeframes=("1m", "1M")):
    return SimpleNamespace(
        provider_id=provider_id,
        supports_historical_ohlcv=True,
        supported_ohlcv_timeframes=timeframes,
        ohlcv_pagination_mode="since",
        max_ohlcv_request_limit=None,
        max_historical_ohlcv_bars=None,
    )


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
        capabilities=lambda: history_capabilities(),
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
        capabilities=lambda: history_capabilities(),
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
        capabilities=lambda: history_capabilities(),
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


def test_sync_cycle_applies_kraken_720_candle_history_cap_and_request_limit(tmp_path):
    path = str(tmp_path / "trading.sqlite")
    store = HistoricalCandleStore(path)
    state = HistoricalSyncStateStore(path)
    target = HistoricalSyncTarget("ccxt:kraken", "BTC/EUR", "1m")
    calls = []

    class Exchange:
        @staticmethod
        def parse_timeframe(timeframe):
            return 60

        def fetch_ohlcv(self, symbol, timeframe, since, limit):
            calls.append((since, limit))
            return []

    provider = SimpleNamespace(
        exchange=Exchange(),
        capabilities=lambda: SimpleNamespace(
            provider_id="ccxt:kraken",
            supports_historical_ohlcv=True,
            ohlcv_pagination_mode="since",
            supported_ohlcv_timeframes=("1m", "1h"),
            max_ohlcv_request_limit=720,
            max_historical_ohlcv_bars=720,
        ),
        discover_markets=lambda: [SimpleNamespace(
            symbol="BTC/EUR", active=True,
            instrument_id="ccxt:kraken:spot:BTC/EUR",
        )],
    )
    now_ms = 86_500_000
    result = run_sync_cycle(
        [target], {"ccxt:kraken": provider}, store, state,
        now_ms=now_ms, lookback_days=365, page_limit=1000,
    )
    outcome = result[0]
    assert outcome["history_window_limited"] is True
    assert outcome["max_history_bars"] == 720
    assert outcome["history_limit_status"] == "provider_limit_applied"
    assert outcome["effective_page_limit"] == 720
    assert outcome["effective_since_ms"] >= now_ms - 720 * 60_000
    assert outcome["requested_since_ms"] < outcome["effective_since_ms"]
    assert calls == [(outcome["effective_since_ms"], 720)]
    saved = state.list_targets()[0]
    assert saved["max_history_bars"] == 720
    assert saved["history_window_limited"] == 1
    assert saved["initial_since_ms"] == outcome["effective_since_ms"]


def test_sync_cycle_rejects_timeframe_not_declared_by_provider(tmp_path):
    path = str(tmp_path / "trading.sqlite")
    store = HistoricalCandleStore(path)
    state = HistoricalSyncStateStore(path)
    target = HistoricalSyncTarget("ccxt:kraken", "BTC/EUR", "4h")

    class Exchange:
        @staticmethod
        def parse_timeframe(timeframe):
            return 14_400

        def fetch_ohlcv(self, *args, **kwargs):
            raise AssertionError("Unsupported timeframe must not fetch")

    provider = SimpleNamespace(
        exchange=Exchange(),
        capabilities=lambda: SimpleNamespace(
            supports_historical_ohlcv=True,
            ohlcv_pagination_mode="since",
            supported_ohlcv_timeframes=("1m", "1h"),
            max_ohlcv_request_limit=720,
            max_historical_ohlcv_bars=720,
        ),
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
    assert state.list_targets() == []


def test_sync_state_store_migrates_existing_scheduler_schema(tmp_path):
    import sqlite3

    path = str(tmp_path / "legacy.sqlite")
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE historical_sync_state ("
        "provider_id TEXT NOT NULL, instrument_id TEXT NOT NULL, symbol TEXT NOT NULL, "
        "timeframe TEXT NOT NULL, initial_since_ms INTEGER NOT NULL, next_since_ms INTEGER, "
        "last_started_at TEXT, last_finished_at TEXT, last_success_at TEXT, "
        "last_status TEXT NOT NULL DEFAULT 'pending', last_error TEXT, "
        "last_pages INTEGER NOT NULL DEFAULT 0, last_received INTEGER NOT NULL DEFAULT 0, "
        "last_inserted INTEGER NOT NULL DEFAULT 0, last_updated INTEGER NOT NULL DEFAULT 0, "
        "updated_at TEXT NOT NULL, PRIMARY KEY(provider_id,instrument_id,timeframe))"
    )
    db.commit()
    db.close()

    state = HistoricalSyncStateStore(path)
    row, created = state.ensure_target(
        "ccxt:kraken", "ccxt:kraken:spot:BTC/EUR", "BTC/EUR", "1h", 1000,
        requested_since_ms=500, max_history_bars=720,
        history_window_limited=True, history_limit_status="provider_limit_applied",
    )
    assert created is True
    assert row["requested_since_ms"] == 500
    assert row["max_history_bars"] == 720
    assert row["history_window_limited"] == 1
    assert row["history_limit_status"] == "provider_limit_applied"


def test_provider_history_cap_clamps_existing_cursor_without_restarting_backfill(tmp_path):
    path = str(tmp_path / "trading.sqlite")
    state = HistoricalSyncStateStore(path)
    instrument_id = "ccxt:kraken:spot:BTC/EUR"
    state.ensure_target(
        "ccxt:kraken", instrument_id, "BTC/EUR", "1h", 1000,
    )
    state.record_result(
        "ccxt:kraken", instrument_id, "1h",
        {"next_since_ms": 2000, "pages": 1, "received": 500, "inserted": 500, "updated": 0},
        status="backfill_in_progress",
    )
    updated, created = state.ensure_target(
        "ccxt:kraken", instrument_id, "BTC/EUR", "1h", 5000,
        requested_since_ms=1000, max_history_bars=720,
        history_window_limited=True, history_limit_status="provider_limit_applied",
    )
    assert created is False
    assert updated["initial_since_ms"] == 5000
    assert updated["next_since_ms"] == 5000
    assert updated["history_window_limited"] == 1


@pytest.mark.parametrize(
    "capabilities",
    [
        SimpleNamespace(
                provider_id="ccxt:kraken",
                supports_historical_ohlcv=1,
                supported_ohlcv_timeframes=("1m",),
                ohlcv_pagination_mode="since",
                max_ohlcv_request_limit=720,
                max_historical_ohlcv_bars=720,
        ),
        SimpleNamespace(
                provider_id="ccxt:kraken",
                supports_historical_ohlcv=True,
                supported_ohlcv_timeframes=(),
                ohlcv_pagination_mode="since",
                max_ohlcv_request_limit=720,
                max_historical_ohlcv_bars=720,
        ),
        SimpleNamespace(
                provider_id="ccxt:kraken",
                supports_historical_ohlcv=True,
                supported_ohlcv_timeframes=("1m",),
                ohlcv_pagination_mode="cursor",
                max_ohlcv_request_limit=720,
                max_historical_ohlcv_bars=720,
        ),
        SimpleNamespace(
                provider_id="ccxt:kraken",
                supports_historical_ohlcv=True,
                supported_ohlcv_timeframes=("1m",),
                ohlcv_pagination_mode="since",
                max_ohlcv_request_limit=1.5,
                max_historical_ohlcv_bars=720,
        ),
        SimpleNamespace(
                provider_id="ccxt:kraken",
                supports_historical_ohlcv=True,
                supported_ohlcv_timeframes=("1m",),
                ohlcv_pagination_mode="since",
                max_ohlcv_request_limit=720,
                max_historical_ohlcv_bars=True,
        ),
    ],
)
def test_sync_cycle_fails_closed_on_incomplete_or_invalid_capabilities(
    tmp_path, capabilities,
):
    path = str(tmp_path / "trading.sqlite")
    store = HistoricalCandleStore(path)
    state = HistoricalSyncStateStore(path)
    target = HistoricalSyncTarget("ccxt:kraken", "BTC/EUR", "1m")

    class Exchange:
        @staticmethod
        def parse_timeframe(timeframe):
            return 60

        def fetch_ohlcv(self, *args, **kwargs):
            raise AssertionError("Invalid provider capabilities must block OHLCV requests")

    provider = SimpleNamespace(
        exchange=Exchange(),
        capabilities=lambda: capabilities,
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
    assert state.list_targets() == []


@pytest.mark.parametrize("capability_mode", ["missing_method", "returns_none", "wrong_identity"])
def test_sync_cycle_fails_closed_when_provider_capability_contract_is_missing(
    tmp_path, capability_mode,
):
    path = str(tmp_path / "trading.sqlite")
    store = HistoricalCandleStore(path)
    state = HistoricalSyncStateStore(path)
    target = HistoricalSyncTarget("ccxt:kraken", "BTC/EUR", "1m")

    class Exchange:
        @staticmethod
        def parse_timeframe(timeframe):
            return 60

        def fetch_ohlcv(self, *args, **kwargs):
            raise AssertionError("Missing provider capabilities must block OHLCV requests")

    provider = SimpleNamespace(
        exchange=Exchange(),
        discover_markets=lambda: [SimpleNamespace(
            symbol="BTC/EUR", active=True,
            instrument_id="ccxt:kraken:spot:BTC/EUR",
        )],
    )
    if capability_mode == "returns_none":
        provider.capabilities = lambda: None
    elif capability_mode == "wrong_identity":
        provider.capabilities = lambda: history_capabilities("ccxt:other")
    result = run_sync_cycle(
        [target], {"ccxt:kraken": provider}, store, state,
        now_ms=86_500_000, lookback_days=1,
    )
    assert result[0]["status"] == "error"
    assert "historical sync target failed" in result[0]["error"]
    assert state.list_targets() == []

