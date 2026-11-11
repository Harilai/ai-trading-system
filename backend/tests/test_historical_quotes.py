import sqlite3

import pytest

from app.historical_data import (
    HistoricalDataConflict,
    HistoricalDataError,
    HistoricalQuoteStore,
)


def test_quote_store_persists_ordered_decimal_observations_and_provenance(tmp_path):
    store = HistoricalQuoteStore(str(tmp_path / "trading.sqlite"))
    first = store.record(
        "ccxt:kraken", "ccxt:kraken:spot:BTC/EUR", "BTC/EUR", 1_700_000_000_000,
        "100.10", "100.30", timestamp_source="retrieved_at",
    )
    second = store.record(
        "ccxt:kraken", "ccxt:kraken:spot:BTC/EUR", "BTC/EUR", 1_700_000_060_000,
        "100.20", "100.40", timestamp_source="exchange",
    )

    assert first["status"] == "inserted"
    assert second["status"] == "inserted"
    rows = store.get(
        "ccxt:kraken", "ccxt:kraken:spot:BTC/EUR",
        since_ms=1_700_000_000_000, until_ms=1_700_000_120_000,
    )
    assert [row["timestamp_ms"] for row in rows] == [
        1_700_000_000_000, 1_700_000_060_000,
    ]
    assert rows[0]["bid"] == "100.1"
    assert rows[0]["ask"] == "100.3"
    assert rows[0]["timestamp_source"] == "retrieved_at"
    assert isinstance(rows[0]["retrieved_at_ms"], int) and rows[0]["retrieved_at_ms"] > 0
    assert rows[1]["timestamp_source"] == "exchange"
    assert store.coverage("ccxt:kraken", "ccxt:kraken:spot:BTC/EUR") == {
        "observations": 2,
        "first_timestamp_ms": 1_700_000_000_000,
        "last_timestamp_ms": 1_700_000_060_000,
        "status": "available",
    }


def test_quote_store_is_idempotent_and_rejects_conflicting_same_timestamp(tmp_path):
    store = HistoricalQuoteStore(str(tmp_path / "trading.sqlite"))
    args = (
        "ccxt:kraken", "ccxt:kraken:spot:BTC/EUR", "BTC/EUR",
        1_700_000_000_000, "100", "101",
    )
    assert store.record(*args, timestamp_source="exchange")["status"] == "inserted"
    assert store.record(*args, timestamp_source="exchange")["status"] == "unchanged"
    with pytest.raises(HistoricalDataConflict, match="Conflicting quote snapshot"):
        store.record(
            *args[:4], "100", "102", timestamp_source="exchange",
        )


@pytest.mark.parametrize(
    "timestamp,bid,ask,timestamp_source",
    [
        (True, "100", "101", "exchange"),
        (0, "100", "101", "exchange"),
        (1_700_000_000_000, "0", "101", "exchange"),
        (1_700_000_000_000, "100", "99", "exchange"),
        (1_700_000_000_000, "NaN", "101", "exchange"),
        (1_700_000_000_000, "100", "101", "unknown"),
    ],
)
def test_quote_store_rejects_invalid_observations(
    tmp_path, timestamp, bid, ask, timestamp_source,
):
    store = HistoricalQuoteStore(str(tmp_path / "trading.sqlite"))
    with pytest.raises(HistoricalDataError):
        store.record(
            "ccxt:kraken", "ccxt:kraken:spot:BTC/EUR", "BTC/EUR",
            timestamp, bid, ask, timestamp_source=timestamp_source,
        )


def test_quote_store_validates_query_ranges_and_limits(tmp_path):
    store = HistoricalQuoteStore(str(tmp_path / "trading.sqlite"))
    with pytest.raises(HistoricalDataError, match="limit"):
        store.get("ccxt:kraken", "ccxt:kraken:spot:BTC/EUR", limit=0)
    with pytest.raises(HistoricalDataError, match="until_ms"):
        store.get(
            "ccxt:kraken", "ccxt:kraken:spot:BTC/EUR",
            since_ms=200, until_ms=100,
        )


def test_quote_store_filters_by_receipt_time_independently_of_exchange_time(tmp_path):
    store = HistoricalQuoteStore(str(tmp_path / "trading.sqlite"))
    store.record(
        "ccxt:kraken", "ccxt:kraken:spot:BTC/EUR", "BTC/EUR",
        1_700_000_000_000, "100", "101",
        timestamp_source="exchange", retrieved_at_ms=1_700_000_060_000,
    )

    rows = store.get(
        "ccxt:kraken", "ccxt:kraken:spot:BTC/EUR",
        received_since_ms=1_700_000_030_000,
        received_until_ms=1_700_000_090_000,
    )
    assert len(rows) == 1
    assert rows[0]["timestamp_ms"] == 1_700_000_000_000
    assert rows[0]["retrieved_at_ms"] == 1_700_000_060_000

    before_receipt = store.get(
        "ccxt:kraken", "ccxt:kraken:spot:BTC/EUR",
        received_until_ms=1_700_000_030_000,
    )
    assert before_receipt == []


def test_quote_store_migration_does_not_backdate_unknown_retrieval_time(tmp_path):
    path = str(tmp_path / "legacy.sqlite")
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE historical_quotes ("
            "provider_id TEXT NOT NULL, instrument_id TEXT NOT NULL, symbol TEXT NOT NULL, "
            "timestamp_ms INTEGER NOT NULL, bid TEXT NOT NULL, ask TEXT NOT NULL, "
            "timestamp_source TEXT NOT NULL, source TEXT NOT NULL, retrieved_at TEXT NOT NULL, "
            "PRIMARY KEY(provider_id,instrument_id,timestamp_ms))"
        )
        db.execute(
            "INSERT INTO historical_quotes VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "ccxt:kraken", "ccxt:kraken:spot:BTC/EUR", "BTC/EUR",
                1_700_000_000_000, "100", "101", "exchange", "public_ticker",
                "not-a-valid-retrieval-time",
            ),
        )

    store = HistoricalQuoteStore(path)
    row = store.get("ccxt:kraken", "ccxt:kraken:spot:BTC/EUR")[0]
    assert row["retrieved_at_ms"] == 0
