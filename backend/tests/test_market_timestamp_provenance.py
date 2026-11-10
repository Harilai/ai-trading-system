import sqlite3

import pytest

from app.portfolio import PaperPortfolio, PortfolioError


def test_retrieved_at_provenance_is_persisted_and_part_of_idempotent_intent(tmp_path):
    portfolio = PaperPortfolio(str(tmp_path / "provenance.db"), 20)

    first = portfolio.execute(
        "BTC/EUR",
        "buy",
        0.001,
        10000,
        0.001,
        0,
        1,
        1,
        client_order_id="retrieved-at-order",
        market_timestamp=1_800_000_000_000,
        market_timestamp_source="retrieved_at",
    )

    assert first["market_timestamp_source"] == "retrieved_at"
    assert portfolio.history()[0]["market_timestamp_source"] == "retrieved_at"

    replay = portfolio.execute(
        "BTC/EUR",
        "buy",
        0.001,
        10000,
        0.001,
        0,
        1,
        1,
        client_order_id="retrieved-at-order",
        market_timestamp=1_800_000_000_000,
        market_timestamp_source="retrieved_at",
    )
    assert replay["id"] == first["id"]
    assert replay["idempotent_replay"] is True
    assert len(portfolio.history()) == 1

    with pytest.raises(PortfolioError, match="different order intent"):
        portfolio.execute(
            "BTC/EUR",
            "buy",
            0.001,
            10000,
            0.001,
            0,
            1,
            1,
            client_order_id="retrieved-at-order",
            market_timestamp=1_800_000_000_000,
            market_timestamp_source="exchange",
        )


def test_legacy_trade_migration_marks_unknown_timestamp_provenance(tmp_path):
    path = str(tmp_path / "legacy-provenance.db")
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE positions (symbol TEXT PRIMARY KEY, quantity REAL, average_price REAL)"
        )
        db.execute(
            "CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, side TEXT, "
            "quantity REAL, price REAL, fee REAL, total REAL, created_at TEXT)"
        )
        db.execute(
            "INSERT INTO trades(symbol,side,quantity,price,fee,total,created_at) "
            "VALUES('BTC/EUR','buy',0.001,10000,0.01,10.01,'2026-01-01T00:00:00+00:00')"
        )
        db.execute(
            "CREATE TABLE account (id INTEGER PRIMARY KEY CHECK(id=1), initial_cash REAL, "
            "cash REAL, realised_pnl REAL DEFAULT 0)"
        )
        db.execute(
            "CREATE TABLE withdrawal_requests (id TEXT PRIMARY KEY, amount REAL NOT NULL, "
            "status TEXT NOT NULL, profit_component REAL NOT NULL DEFAULT 0, "
            "requested_at TEXT NOT NULL, decided_at TEXT)"
        )

    portfolio = PaperPortfolio(path, 20)
    trade = portfolio.history()[0]

    assert trade["market_timestamp_source"] == "unknown"
    assert trade["market_timestamp"] is None
