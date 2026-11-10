from datetime import datetime, timezone

import pytest

from app.portfolio import PaperPortfolio, PortfolioError


@pytest.fixture
def portfolio(tmp_path):
    return PaperPortfolio(str(tmp_path / "paper.db"), 20)


def test_persistent_initial_balance(tmp_path):
    path = str(tmp_path / "paper.db")
    PaperPortfolio(path, 20)
    assert PaperPortfolio(path, 20).snapshot()["cash"] == 20


def test_buy_sell_and_realised_pnl(portfolio):
    portfolio.execute("BTC/EUR", "buy", 0.001, 10000, 0.001, 0, 1, 1)
    assert portfolio.snapshot()["positions"][0]["quantity"] == pytest.approx(0.001)
    portfolio.execute("BTC/EUR", "sell", 0.001, 11000, 0.001, 0, 1, 1)
    snap = portfolio.snapshot()
    assert snap["positions"] == []
    assert snap["realised_pnl"] > 0


def test_realised_pnl_includes_entry_and_exit_fees(portfolio):
    portfolio.execute("BTC/EUR", "buy", 0.001, 10000, 0.001, 0, 1, 1)
    position = portfolio.snapshot()["positions"][0]
    assert position["average_price"] == pytest.approx(10010)
    portfolio.execute("BTC/EUR", "sell", 0.001, 11000, 0.001, 0, 1, 1)
    assert portfolio.snapshot()["realised_pnl"] == pytest.approx(0.979)


def test_partial_sell_preserves_remaining_cost_basis(tmp_path):
    portfolio = PaperPortfolio(str(tmp_path / "partial.db"), 100)
    portfolio.execute("BTC/EUR", "buy", 0.002, 10000, 0.001, 0, 1, 1)
    portfolio.execute("BTC/EUR", "sell", 0.001, 11000, 0.001, 0, 1, 1)
    position = portfolio.snapshot()["positions"][0]
    assert position["quantity"] == pytest.approx(0.001)
    assert position["average_price"] == pytest.approx(10010)
    assert portfolio.snapshot()["realised_pnl"] == pytest.approx(0.979)


def test_rejects_wrong_quote(portfolio):
    with pytest.raises(PortfolioError):
        portfolio.execute("BTC/USDT", "buy", 0.001, 10000, 0, 0, 1, 1)


def test_rejects_overallocation(portfolio):
    with pytest.raises(PortfolioError):
        portfolio.execute("BTC/EUR", "buy", 0.01, 1000, 0, 0, 0.25, 1)


def test_rejects_oversell(portfolio):
    with pytest.raises(PortfolioError):
        portfolio.execute("BTC/EUR", "sell", 0.1, 1000, 0, 0, 1, 1)


@pytest.mark.parametrize(
    "fee_rate,slippage_rate,max_fraction",
    [(-0.01, 0, 1), (0.01, 1, 1), (0.01, 0, 0), (0.01, 0, 1.1)],
)
def test_rejects_invalid_execution_parameters(portfolio, fee_rate, slippage_rate, max_fraction):
    with pytest.raises(PortfolioError):
        portfolio.execute("BTC/EUR", "buy", 0.001, 10000, fee_rate, slippage_rate, max_fraction, 1)


def test_buy_persists_strategy_and_protective_levels(portfolio):
    portfolio.execute(
        "BTC/EUR", "buy", 0.001, 10000, 0.001, 0, 1, 1,
        client_order_id="decision-1",
        strategy_id="ema_12_26",
        stop_loss=9800,
        take_profit=10400,
    )
    position = portfolio.snapshot()["positions"][0]
    assert position["strategy_id"] == "ema_12_26"
    assert position["stop_loss"] == pytest.approx(9800)
    assert position["take_profit"] == pytest.approx(10400)


def test_duplicate_client_order_id_does_not_execute_twice(portfolio):
    first = portfolio.execute(
        "BTC/EUR", "buy", 0.001, 10000, 0.001, 0, 1, 1,
        client_order_id="same-decision",
    )
    cash_after_first = portfolio.snapshot()["cash"]
    replay = portfolio.execute(
        "BTC/EUR", "buy", 0.001, 10000, 0.001, 0, 1, 1,
        client_order_id="same-decision",
    )
    assert replay["id"] == first["id"]
    assert replay["idempotent_replay"] is True
    assert portfolio.snapshot()["cash"] == pytest.approx(cash_after_first)
    assert len(portfolio.history()) == 1


def test_idempotency_key_cannot_be_reused_for_a_different_trade(portfolio):
    portfolio.execute(
        "BTC/EUR", "buy", 0.001, 10000, 0.001, 0, 1, 1,
        client_order_id="immutable-order-key",
    )
    with pytest.raises(PortfolioError, match="different paper order"):
        portfolio.execute(
            "ETH/EUR", "buy", 0.001, 1000, 0.001, 0, 1, 1,
            client_order_id="immutable-order-key",
        )
    assert len(portfolio.history()) == 1


def test_trade_can_be_recovered_by_idempotency_key(portfolio):
    trade = portfolio.execute(
        "BTC/EUR", "buy", 0.001, 10000, 0.001, 0, 1, 1,
        client_order_id="recover-me",
    )
    recovered = portfolio.get_trade_by_client_order_id("recover-me")
    assert recovered["id"] == trade["id"]
    assert recovered["symbol"] == "BTC/EUR"


def test_legacy_database_is_migrated_with_protection_and_idempotency_columns(tmp_path):
    path = str(tmp_path / "legacy.db")
    import sqlite3

    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE positions (symbol TEXT PRIMARY KEY, quantity REAL, average_price REAL)")
        db.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, side TEXT, quantity REAL, price REAL, fee REAL, total REAL, created_at TEXT)")
        db.execute("CREATE TABLE account (id INTEGER PRIMARY KEY CHECK(id=1), initial_cash REAL, cash REAL, realised_pnl REAL DEFAULT 0)")
        db.execute(
            "CREATE TABLE withdrawal_requests (id TEXT PRIMARY KEY, amount REAL NOT NULL, "
            "status TEXT NOT NULL, profit_component REAL NOT NULL DEFAULT 0, "
            "requested_at TEXT NOT NULL, decided_at TEXT)"
        )
    PaperPortfolio(path, 20)
    with sqlite3.connect(path) as db:
        position_columns = {row[1] for row in db.execute("PRAGMA table_info(positions)")}
        trade_columns = {row[1] for row in db.execute("PRAGMA table_info(trades)")}
    assert {"provider_id", "instrument_id", "strategy_id", "stop_loss", "take_profit"} <= position_columns
    assert {"client_order_id", "provider_id", "instrument_id", "quote_currency", "fx_rate_to_eur", "total_eur", "fx_provider_id", "fx_timestamp", "strategy_id", "stop_loss", "take_profit"} <= trade_columns
    with sqlite3.connect(path) as db:
        withdrawal_columns = {row[1] for row in db.execute("PRAGMA table_info(withdrawal_requests)")}
    assert {"profit_component", "non_reserved_component"} <= withdrawal_columns


def test_idempotency_key_cannot_change_protective_levels(portfolio):
    portfolio.execute(
        "BTC/EUR", "buy", 0.001, 10000, 0.001, 0, 1, 1,
        client_order_id="protective-level-key", stop_loss=9500, take_profit=11000,
    )
    with pytest.raises(PortfolioError, match="different paper order"):
        portfolio.execute(
            "BTC/EUR", "buy", 0.001, 10000, 0.001, 0, 1, 1,
            client_order_id="protective-level-key", stop_loss=9400, take_profit=11000,
        )


def test_populated_legacy_database_migrates_eur_balance_and_position_cost_basis(tmp_path):
    path = str(tmp_path / "populated-legacy.db")
    import sqlite3

    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE positions (symbol TEXT PRIMARY KEY, quantity REAL, average_price REAL)")
        db.execute("INSERT INTO positions VALUES('BTC/EUR',0.25,40000)")
        db.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, side TEXT, quantity REAL, price REAL, fee REAL, total REAL, created_at TEXT)")
        db.execute("INSERT INTO trades(symbol,side,quantity,price,fee,total,created_at) VALUES('BTC/EUR','buy',0.25,40000,10,10010,'2026-01-01T00:00:00+00:00')")
        db.execute("CREATE TABLE account (id INTEGER PRIMARY KEY CHECK(id=1), initial_cash REAL, cash REAL, realised_pnl REAL DEFAULT 0)")
        db.execute("INSERT INTO account(id,initial_cash,cash,realised_pnl) VALUES(1,20,17.5,0)")
    portfolio = PaperPortfolio(path, 999)
    snapshot = portfolio.snapshot()
    assert snapshot["cash"] == pytest.approx(17.5)
    assert snapshot["cash_balances"][0]["currency"] == "EUR"
    assert snapshot["cash_balances"][0]["balance"] == pytest.approx(17.5)
    position = snapshot["positions"][0]
    assert position["quote_currency"] == "EUR"
    assert position["average_price_eur"] == pytest.approx(40000)
    trade = portfolio.history()[0]
    assert trade["quote_currency"] == "EUR"
    assert trade["total_eur"] == pytest.approx(10010)


def test_market_valuation_distinguishes_realised_and_unrealised_pnl(portfolio):
    portfolio.execute("BTC/EUR", "buy", 0.001, 10000, 0.001, 0, 1, 1)
    snapshot = portfolio.snapshot({"BTC/EUR": 12000})
    assert snapshot["valuation_complete"] is True
    assert snapshot["market_value"] == pytest.approx(12)
    assert snapshot["unrealised_pnl"] == pytest.approx(1.99)
    assert snapshot["realised_pnl"] == pytest.approx(0)


def test_incomplete_valuation_uses_cost_basis_and_flags_incomplete(portfolio):
    portfolio.execute("BTC/EUR", "buy", 0.001, 10000, 0.001, 0, 1, 1)
    snapshot = portfolio.snapshot({})
    assert snapshot["valuation_complete"] is False
    assert snapshot["valuation_source"] == "cost_basis_or_partial"


def test_manual_withdrawal_uses_profit_reserve_then_available_principal(portfolio):
    portfolio.execute("BTC/EUR", "buy", 0.001, 10000, 0.001, 0, 1, 1)
    portfolio.execute("BTC/EUR", "sell", 0.001, 11000, 0.001, 0, 1, 1)
    reserve = portfolio.snapshot()["withdrawable_reserved_profit"]
    assert reserve == pytest.approx(0.4895)

    request = portfolio.request_withdrawal(0.50)
    assert request["status"] == "pending"
    approved = portfolio.approve_withdrawal(request["id"])
    assert approved["status"] == "approved"
    assert approved["profit_component"] == pytest.approx(0.4895)
    assert approved["non_reserved_component"] == pytest.approx(0.0105)
    snapshot = portfolio.snapshot()
    assert snapshot["withdrawn_profit"] == pytest.approx(0.4895)
    assert snapshot["withdrawable_reserved_profit"] == pytest.approx(0)


def test_owner_can_withdraw_available_initial_principal(portfolio):
    request = portfolio.request_withdrawal(5.0)
    approved = portfolio.approve_withdrawal(request["id"])
    assert approved["profit_component"] == pytest.approx(0)
    assert approved["non_reserved_component"] == pytest.approx(5)
    assert portfolio.snapshot()["cash"] == pytest.approx(15)


def test_pending_withdrawals_reserve_available_cash(portfolio):
    first = portfolio.request_withdrawal(12)
    assert portfolio.snapshot()["available_cash_for_manual_withdrawal"] == pytest.approx(8)
    with pytest.raises(PortfolioError, match="after pending requests"):
        portfolio.request_withdrawal(9)
    with pytest.raises(PortfolioError, match="unreserved cash"):
        portfolio.execute("BTC/EUR", "buy", 0.001, 10000, 0.001, 0, 1, 1)
    portfolio.cancel_withdrawal(first["id"])
    assert portfolio.snapshot()["available_cash_for_manual_withdrawal"] == pytest.approx(20)


def test_withdrawal_cannot_exceed_available_cash_or_liquidate_positions(portfolio):
    with pytest.raises(PortfolioError, match="available cash"):
        portfolio.request_withdrawal(21)
    portfolio.execute("BTC/EUR", "buy", 0.001, 10000, 0.001, 0, 1, 1)
    with pytest.raises(PortfolioError, match="not liquidated automatically"):
        portfolio.request_withdrawal(20)


def test_pending_withdrawal_can_be_cancelled_once(portfolio):
    portfolio.execute("BTC/EUR", "buy", 0.001, 10000, 0.001, 0, 1, 1)
    portfolio.execute("BTC/EUR", "sell", 0.001, 11000, 0.001, 0, 1, 1)
    request = portfolio.request_withdrawal(0.2)
    cancelled = portfolio.cancel_withdrawal(request["id"])
    assert cancelled["status"] == "cancelled"
    with pytest.raises(PortfolioError, match="not pending"):
        portfolio.approve_withdrawal(request["id"])


def test_same_symbol_from_two_providers_has_independent_positions(tmp_path):
    portfolio = PaperPortfolio(str(tmp_path / "multi-provider.db"), 100)
    portfolio.execute(
        "BTC/EUR", "buy", 0.001, 10000, 0.001, 0, 1, 1,
        provider_id="ccxt:kraken",
        instrument_id="ccxt:kraken:spot:BTC/EUR",
    )
    portfolio.execute(
        "BTC/EUR", "buy", 0.001, 12000, 0.001, 0, 1, 1,
        provider_id="ccxt:coinbase",
        instrument_id="ccxt:coinbase:spot:BTC/EUR",
    )
    positions = portfolio.snapshot()["positions"]
    assert len(positions) == 2
    assert {p["provider_id"] for p in positions} == {"ccxt:kraken", "ccxt:coinbase"}
    assert sorted(p["average_price"] for p in positions) == pytest.approx([10010, 12012])


def test_sell_only_reduces_the_matching_provider_instrument(tmp_path):
    portfolio = PaperPortfolio(str(tmp_path / "multi-provider-sell.db"), 100)
    portfolio.execute(
        "BTC/EUR", "buy", 0.001, 10000, 0.001, 0, 1, 1,
        provider_id="ccxt:kraken",
        instrument_id="ccxt:kraken:spot:BTC/EUR",
    )
    portfolio.execute(
        "BTC/EUR", "buy", 0.001, 12000, 0.001, 0, 1, 1,
        provider_id="ccxt:coinbase",
        instrument_id="ccxt:coinbase:spot:BTC/EUR",
    )
    portfolio.execute(
        "BTC/EUR", "sell", 0.001, 11000, 0.001, 0, 1, 1,
        provider_id="ccxt:kraken",
        instrument_id="ccxt:kraken:spot:BTC/EUR",
    )
    positions = portfolio.snapshot()["positions"]
    assert len(positions) == 1
    assert positions[0]["provider_id"] == "ccxt:coinbase"
    assert positions[0]["average_price"] == pytest.approx(12012)


def test_market_valuation_uses_provider_instrument_key_without_symbol_collision(tmp_path):
    portfolio = PaperPortfolio(str(tmp_path / "multi-provider-valuation.db"), 100)
    portfolio.execute(
        "BTC/EUR", "buy", 0.001, 10000, 0.001, 0, 1, 1,
        provider_id="ccxt:kraken",
        instrument_id="ccxt:kraken:spot:BTC/EUR",
    )
    portfolio.execute(
        "BTC/EUR", "buy", 0.001, 12000, 0.001, 0, 1, 1,
        provider_id="ccxt:coinbase",
        instrument_id="ccxt:coinbase:spot:BTC/EUR",
    )
    snapshot = portfolio.snapshot({
        "ccxt:kraken:spot:BTC/EUR": 11000,
        "ccxt:coinbase:spot:BTC/EUR": 13000,
    })
    assert snapshot["valuation_complete"] is True
    assert snapshot["market_value"] == pytest.approx(24)
    assert snapshot["unrealised_pnl"] == pytest.approx(1.978)


def test_legacy_position_migrates_to_default_ccxt_provider(tmp_path):
    path = str(tmp_path / "legacy-position.db")
    import sqlite3
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE positions (symbol TEXT PRIMARY KEY, quantity REAL, average_price REAL)")
        db.execute("INSERT INTO positions VALUES('BTC/EUR', 0.001, 10010)")
        db.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, side TEXT, quantity REAL, price REAL, fee REAL, total REAL, created_at TEXT)")
        db.execute("CREATE TABLE account (id INTEGER PRIMARY KEY CHECK(id=1), initial_cash REAL, cash REAL, realised_pnl REAL DEFAULT 0)")
        db.execute("INSERT INTO account VALUES(1, 20, 10, 0)")
    portfolio = PaperPortfolio(path, 20)
    position = portfolio.snapshot()["positions"][0]
    assert position["provider_id"] == "ccxt:kraken"
    assert position["instrument_id"] == "ccxt:kraken:spot:BTC/EUR"
    assert position["symbol"] == "BTC/EUR"
    assert portfolio.snapshot()["cash"] == pytest.approx(10)



@pytest.mark.parametrize("amount", [0, -1, float("inf"), "not-a-number"])
def test_withdrawal_rejects_invalid_amounts(portfolio, amount):
    with pytest.raises(PortfolioError, match="positive finite"):
        portfolio.request_withdrawal(amount)



@pytest.mark.parametrize(
    "quantity,price,fee_rate,slippage_rate,max_fraction,minimum",
    [
        (float("nan"), 10000, 0.001, 0, 1, 1),
        (0.001, float("inf"), 0.001, 0, 1, 1),
        (0.001, 10000, float("nan"), 0, 1, 1),
        (0.001, 10000, 1.0, 0, 1, 1),
        (0.001, 10000, 0.001, float("inf"), 1, 1),
        (0.001, 10000, 0.001, 0, float("nan"), 1),
        (0.001, 10000, 0.001, 0, 1, float("inf")),
    ],
)
def test_execution_rejects_non_finite_or_out_of_range_numeric_values(
    portfolio, quantity, price, fee_rate, slippage_rate, max_fraction, minimum
):
    with pytest.raises(PortfolioError):
        portfolio.execute(
            "BTC/EUR", "buy", quantity, price, fee_rate, slippage_rate, max_fraction, minimum
        )
    assert portfolio.history() == []
    assert portfolio.snapshot()["positions"] == []


@pytest.mark.parametrize("initial_cash", [float("nan"), float("inf"), -1, "invalid"])
def test_portfolio_rejects_invalid_initial_cash(tmp_path, initial_cash):
    with pytest.raises(PortfolioError, match="Initial cash"):
        PaperPortfolio(str(tmp_path / "invalid-cash.db"), initial_cash)


def test_execution_rejects_non_finite_protective_levels(portfolio):
    with pytest.raises(PortfolioError, match="finite"):
        portfolio.execute(
            "BTC/EUR", "buy", 0.001, 10000, 0.001, 0, 1, 1,
            stop_loss=float("nan"),
        )
    assert portfolio.history() == []


def test_currency_conversion_is_atomic_auditable_and_idempotent(tmp_path):
    portfolio = PaperPortfolio(str(tmp_path / "multi-currency.db"), 100)
    first = portfolio.convert_currency(
        "EUR", "USD", 50, 1.10, 0.01, "fx-conversion-1", "twelve_data", 1_800_000_000_000,
    )
    replay = portfolio.convert_currency(
        "EUR", "USD", 50, 1.10, 0.01, "fx-conversion-1", "twelve_data", 1_800_000_000_000,
    )
    assert first["net_to_amount"] == pytest.approx(54.45)
    assert replay["id"] == first["id"]
    assert replay["idempotent_replay"] is True
    balances = {row["currency"]: row["balance"] for row in portfolio.cash_balances()}
    assert balances["EUR"] == pytest.approx(50)
    assert balances["USD"] == pytest.approx(54.45)
    assert len(portfolio.conversion_history()) == 1
    assert portfolio.snapshot()["realised_pnl"] == pytest.approx(-0.5)
    reopened = PaperPortfolio(str(tmp_path / "multi-currency.db"), 999)
    assert {row["currency"]: row["balance"] for row in reopened.cash_balances()} == {
        "EUR": pytest.approx(50),
        "USD": pytest.approx(54.45),
    }
    assert len(reopened.conversion_history()) == 1
    with pytest.raises(PortfolioError, match="different conversion"):
        reopened.convert_currency(
            "EUR", "USD", 51, 1.10, 0.01, "fx-conversion-1", "twelve_data", 1_800_000_000_000,
        )


def test_currency_conversion_rejects_insufficient_cash_without_mutation(tmp_path):
    portfolio = PaperPortfolio(str(tmp_path / "multi-currency.db"), 20)
    with pytest.raises(PortfolioError, match="Insufficient EUR"):
        portfolio.convert_currency(
            "EUR", "USD", 21, 1.1, 0, "too-large", "twelve_data", 1_800_000_000_000,
        )
    balances = {row["currency"]: row["balance"] for row in portfolio.cash_balances()}
    assert balances == {"EUR": pytest.approx(20)}


def test_currency_conversion_rejects_invalid_fx_and_unsupported_pairs(tmp_path):
    portfolio = PaperPortfolio(str(tmp_path / "multi-currency.db"), 20)
    with pytest.raises(PortfolioError):
        portfolio.convert_currency(
            "EUR", "USD", 5, float("nan"), 0, "bad-rate", "twelve_data", 1_800_000_000_000,
        )
    with pytest.raises(PortfolioError, match="Only explicit EUR/USD"):
        portfolio.convert_currency(
            "EUR", "GBP", 5, 0.8, 0, "unsupported", "twelve_data", 1_800_000_000_000,
        )
    assert portfolio.cash_balances()[0]["balance"] == pytest.approx(20)


def test_usd_paper_fill_requires_timestamped_fx_provenance(portfolio):
    with pytest.raises(PortfolioError, match="FX provider identity"):
        portfolio.execute(
            "AAPL", "buy", 1, 100, 0.001, 0, 1, 1,
            provider_id="twelve_data", instrument_id="twelve_data:cash_equity:AAPL",
            quote_currency="USD", fx_rate_to_eur=0.9, stop_loss=95,
        )


def test_usd_paper_fill_requires_usd_cash_and_keeps_eur_cost_basis(tmp_path):
    portfolio = PaperPortfolio(str(tmp_path / "multi-currency.db"), 300)
    with pytest.raises(PortfolioError, match="Insufficient unreserved USD"):
        portfolio.execute(
            "AAPL", "buy", 1, 100, 0.001, 0, 1, 1,
            provider_id="twelve_data", instrument_id="twelve_data:cash_equity:AAPL",
            quote_currency="USD", fx_rate_to_eur=0.9, fx_provider_id="twelve_data", fx_timestamp=1_800_000_000_000, stop_loss=95,
        )
    portfolio.convert_currency(
        "EUR", "USD", 250, 1.1, 0, "convert-usd", "twelve_data", 1_800_000_000_000,
    )
    trade = portfolio.execute(
        "AAPL", "buy", 2, 100, 0.001, 0, 1, 1,
        provider_id="twelve_data", instrument_id="twelve_data:cash_equity:AAPL",
        quote_currency="USD", fx_rate_to_eur=0.9, fx_provider_id="twelve_data", fx_timestamp=1_800_000_000_000, stop_loss=95,
    )
    assert trade["quote_currency"] == "USD"
    assert trade["total"] == pytest.approx(200.2)
    assert trade["total_eur"] == pytest.approx(180.18)
    assert trade["fx_provider_id"] == "twelve_data"
    assert trade["fx_timestamp"] == 1_800_000_000_000
    position = portfolio.snapshot()["positions"][0]
    assert position["average_price"] == pytest.approx(100.1)
    assert position["average_price_eur"] == pytest.approx(90.09)
    balances = {row["currency"]: row["balance"] for row in portfolio.cash_balances()}
    assert balances["EUR"] == pytest.approx(50)
    assert balances["USD"] == pytest.approx(74.8)


def test_usd_position_valuation_and_realised_pnl_use_fx_in_eur(tmp_path):
    portfolio = PaperPortfolio(str(tmp_path / "multi-currency.db"), 300)
    portfolio.convert_currency(
        "EUR", "USD", 250, 1.1, 0, "convert-usd", "twelve_data", 1_800_000_000_000,
    )
    portfolio.execute(
        "AAPL", "buy", 2, 100, 0.001, 0, 1, 1,
        provider_id="twelve_data", instrument_id="twelve_data:cash_equity:AAPL",
        quote_currency="USD", fx_rate_to_eur=0.9, fx_provider_id="twelve_data", fx_timestamp=1_800_000_000_000, stop_loss=95,
    )
    snapshot = portfolio.snapshot(
        {"twelve_data:cash_equity:AAPL": 110},
        {"USD": {
            "symbol": "USD/EUR", "rate": 0.92,
            "timestamp": 1_800_000_000_000, "provider_id": "twelve_data",
            "inverted": True,
        }},
    )
    position = snapshot["positions"][0]
    assert position["market_value_eur"] == pytest.approx(202.4)
    assert position["unrealised_pnl"] == pytest.approx(22.22)
    assert position["fx_provider_id"] == "twelve_data"
    assert position["fx_timestamp"] == 1_800_000_000_000
    usd_cash = next(item for item in snapshot["cash_balances"] if item["currency"] == "USD")
    assert usd_cash["value_eur"] == pytest.approx(68.816)
    assert usd_cash["fx_provider_id"] == "twelve_data"
    assert snapshot["fx_observations"]["USD"]["inverted"] is True
    assert snapshot["valuation_complete"] is True
    sold = portfolio.execute(
        "AAPL", "sell", 2, 110, 0.001, 0, 1, 1,
        provider_id="twelve_data", instrument_id="twelve_data:cash_equity:AAPL",
        quote_currency="USD", fx_rate_to_eur=0.92, fx_provider_id="twelve_data", fx_timestamp=1_800_000_000_000,
    )
    assert sold["total_eur"] == pytest.approx(202.1976)
    assert portfolio.snapshot()["realised_pnl"] == pytest.approx(22.0176)
    assert portfolio.snapshot()["positions"] == []


def test_usd_cash_without_fx_marks_portfolio_valuation_incomplete(tmp_path):
    portfolio = PaperPortfolio(str(tmp_path / "multi-currency.db"), 100)
    portfolio.convert_currency(
        "EUR", "USD", 10, 1.1, 0, "convert-usd", "twelve_data", 1_800_000_000_000,
    )
    snapshot = portfolio.snapshot()
    assert snapshot["valuation_complete"] is False
    assert snapshot["cash"] == pytest.approx(90)
    assert snapshot["cash_total_eur"] == pytest.approx(90)


def test_usd_to_eur_conversion_uses_inverse_rate_and_charges_eur_fee(tmp_path):
    portfolio = PaperPortfolio(str(tmp_path / "reverse-fx.db"), 100)
    portfolio.convert_currency(
        "EUR", "USD", 50, 1.1, 0, "fund-usd", "twelve_data", 1_800_000_000_000,
    )
    conversion = portfolio.convert_currency(
        "USD", "EUR", 10, 1 / 1.1, 0.01, "return-eur", "twelve_data", 1_800_000_000_000,
    )
    assert conversion["gross_to_amount"] == pytest.approx(10 / 1.1)
    assert conversion["fee_eur"] == pytest.approx((10 / 1.1) * 0.01)
    assert conversion["net_to_amount"] == pytest.approx((10 / 1.1) * 0.99)
    balances = {row["currency"]: row["balance"] for row in portfolio.cash_balances()}
    assert balances["USD"] == pytest.approx(45)
    assert balances["EUR"] == pytest.approx(50 + (10 / 1.1) * 0.99)
    assert portfolio.snapshot()["realised_pnl"] == pytest.approx(-((10 / 1.1) * 0.01))


def test_order_intent_survives_restart_and_fills_exact_approved_request(tmp_path):
    path = str(tmp_path / "order-lifecycle.db")
    request = {
        "symbol": "BTC/EUR", "side": "buy", "quantity": 0.001, "price": 10000,
        "fee_rate": 0.001, "slippage_rate": 0.001, "max_fraction": 1,
        "minimum": 1, "client_order_id": "task-order-1",
        "provider_id": "ccxt:kraken", "instrument_id": "ccxt:kraken:spot:BTC/EUR",
        "strategy_id": "ema_12_26", "stop_loss": 9800, "take_profit": 10400,
        "market_timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        "risk_fx_rate_to_eur": 1.0, "risk_fx_provider_id": None, "risk_fx_timestamp": None,
    }
    portfolio = PaperPortfolio(path, 20)
    created = portfolio.record_order_intent(
        "task-order-1", request, {"action": "buy", "risk_accepted": True},
    )
    assert created["status"] == "risk_approved"
    assert all(created["request"][key] == value for key, value in request.items())
    assert created["request"]["quote_currency"] == "EUR"
    assert created["request"]["fx_rate_to_eur"] == 1.0

    restarted = PaperPortfolio(path, 999)
    recovered_intent = restarted.get_order_intent("task-order-1")
    assert recovered_intent["status"] == "risk_approved"
    assert recovered_intent["decision"]["risk_accepted"] is True

    fill = restarted.execute(**recovered_intent["request"])
    assert fill["idempotent_replay"] is False
    completed = restarted.get_order_intent("task-order-1")
    assert completed["status"] == "filled"
    assert completed["trade_id"] == fill["id"]
    assert completed["fill"]["client_order_id"] == "task-order-1"
    assert len(restarted.history()) == 1

    replay = restarted.execute(**request)
    assert replay["id"] == fill["id"]
    assert replay["idempotent_replay"] is True
    assert len(restarted.history()) == 1
    events = restarted.list_order_intent_events("task-order-1")
    assert [event["to_status"] for event in events] == ["risk_approved", "filled"]


def test_order_intent_rejects_reuse_with_changed_request(portfolio):
    request = {
        "symbol": "BTC/EUR", "side": "buy", "quantity": 0.001, "price": 10000,
        "fee_rate": 0.001, "slippage_rate": 0.001, "max_fraction": 1,
        "minimum": 1, "client_order_id": "immutable-intent",
        "provider_id": "ccxt:kraken", "instrument_id": "ccxt:kraken:spot:BTC/EUR",
        "strategy_id": "ema_12_26", "stop_loss": 9800, "take_profit": 10400,
    }
    portfolio.record_order_intent("immutable-intent", request, {"risk_accepted": True})
    with pytest.raises(PortfolioError, match="different order intent"):
        portfolio.record_order_intent(
            "immutable-intent", {**request, "quantity": 0.002},
            {"risk_accepted": True},
        )


def test_order_intent_reconciles_trade_ledger_and_detects_missing_fill(portfolio):
    request = {
        "symbol": "BTC/EUR", "side": "buy", "quantity": 0.001, "price": 10000,
        "fee_rate": 0.001, "slippage_rate": 0.001, "max_fraction": 1,
        "minimum": 1, "client_order_id": "reconcile-filled",
        "provider_id": "ccxt:kraken", "instrument_id": "ccxt:kraken:spot:BTC/EUR",
        "strategy_id": None, "stop_loss": None, "take_profit": None,
    }
    portfolio.record_order_intent("reconcile-filled", request, {"risk_accepted": True})
    fill = portfolio.execute(**request)
    with portfolio.connect() as db:
        db.execute(
            "UPDATE order_intents SET status='risk_approved',trade_id=NULL WHERE client_order_id=?",
            ("reconcile-filled",),
        )
    result = portfolio.reconcile_order_intents()
    assert result == {"reconciled_filled": 1, "reconciliation_required": 0}
    assert portfolio.get_order_intent("reconcile-filled")["status"] == "filled"

    missing = {**request, "client_order_id": "missing-ledger-fill"}
    portfolio.record_order_intent("missing-ledger-fill", missing, {"risk_accepted": True})
    with portfolio.connect() as db:
        db.execute(
            "UPDATE order_intents SET status='filled',trade_id=999 WHERE client_order_id=?",
            ("missing-ledger-fill",),
        )
    result = portfolio.reconcile_order_intents()
    assert result["reconciliation_required"] == 1
    unresolved = portfolio.get_order_intent("missing-ledger-fill")
    assert unresolved["status"] == "reconciliation_required"
    assert "no matching trade" in unresolved["rejection_reason"].lower()


def test_new_buy_is_blocked_while_another_order_intent_is_unresolved(portfolio):
    first = {
        "symbol": "BTC/EUR", "side": "buy", "quantity": 0.001, "price": 10000,
        "fee_rate": 0.001, "slippage_rate": 0.001, "max_fraction": 1,
        "minimum": 1, "client_order_id": "unresolved-first",
        "provider_id": "ccxt:kraken", "instrument_id": "ccxt:kraken:spot:BTC/EUR",
        "strategy_id": None, "stop_loss": None, "take_profit": None,
    }
    second = {**first, "client_order_id": "blocked-second"}
    portfolio.record_order_intent("unresolved-first", first, {"risk_accepted": True})
    with pytest.raises(PortfolioError, match="blocked by unresolved"):
        portfolio.record_order_intent("blocked-second", second, {"risk_accepted": True})
    with pytest.raises(PortfolioError, match="blocked by unresolved"):
        portfolio.execute(**second)
    assert portfolio.get_order_intent("blocked-second") is None
    portfolio.execute(**first)
    assert portfolio.get_order_intent("unresolved-first")["status"] == "filled"
    assert portfolio.record_order_intent("blocked-second", second, {"risk_accepted": True})["status"] == "risk_approved"


def test_rejected_order_intent_is_terminal(portfolio):
    request = {
        "symbol": "BTC/EUR", "side": "buy", "quantity": 0.001, "price": 10000,
        "fee_rate": 0.001, "slippage_rate": 0.001, "max_fraction": 1,
        "minimum": 1, "client_order_id": "rejected-intent",
        "provider_id": "ccxt:kraken", "instrument_id": "ccxt:kraken:spot:BTC/EUR",
        "strategy_id": None, "stop_loss": None, "take_profit": None,
    }
    portfolio.record_order_intent("rejected-intent", request, {"risk_accepted": True})
    portfolio.reject_order_intent("rejected-intent", "insufficient cash")
    assert portfolio.get_order_intent("rejected-intent")["status"] == "rejected"
    with pytest.raises(PortfolioError, match="already rejected"):
        portfolio.execute(**request)
    with pytest.raises(PortfolioError, match="different order intent"):
        portfolio.record_order_intent(
            "rejected-intent", {**request, "quantity": 0.002},
            {"risk_accepted": True},
        )


def test_order_intent_freshness_requires_recent_market_and_fx_timestamps(tmp_path):
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    portfolio = PaperPortfolio(str(tmp_path / "intent-freshness.db"), 100)
    request = {
        "symbol": "AAPL", "side": "buy", "quantity": 0.1, "price": 100,
        "fee_rate": 0.001, "slippage_rate": 0.001, "max_fraction": 1,
        "minimum": 1, "client_order_id": "fresh-usd-intent",
        "provider_id": "twelve_data", "instrument_id": "twelve_data:cash_equity:AAPL",
        "quote_currency": "USD", "fx_rate_to_eur": 1 / 1.1,
        "fx_provider_id": "twelve_data", "fx_timestamp": now_ms,
        "market_timestamp": now_ms,
    }
    portfolio.record_order_intent("fresh-usd-intent", request, {"risk_accepted": True})
    assert portfolio.order_intent_is_fresh("fresh-usd-intent", 60, 300, now_ms) is True
    assert portfolio.order_intent_is_fresh("fresh-usd-intent", 60, 300, now_ms + 61_000) is False
    assert portfolio.expire_order_intent("fresh-usd-intent", "market evidence expired") is True
    assert portfolio.get_order_intent("fresh-usd-intent")["status"] == "expired"
    assert [event["to_status"] for event in portfolio.list_order_intent_events("fresh-usd-intent")] == [
        "risk_approved", "expired",
    ]


def test_order_intent_reconciliation_flags_mismatched_trade_payload(portfolio):
    request = {
        "symbol": "BTC/EUR", "side": "buy", "quantity": 0.001, "price": 10000,
        "fee_rate": 0.001, "slippage_rate": 0.001, "max_fraction": 1,
        "minimum": 1, "client_order_id": "mismatched-fill",
        "provider_id": "ccxt:kraken", "instrument_id": "ccxt:kraken:spot:BTC/EUR",
        "stop_loss": 9800, "take_profit": 10400,
    }
    portfolio.record_order_intent("mismatched-fill", request, {"risk_accepted": True})
    trade = portfolio.execute(**request)
    with portfolio.connect() as db:
        db.execute(
            "UPDATE trades SET symbol='ETH/EUR' WHERE id=?",
            (trade["id"],),
        )
        db.execute(
            "UPDATE order_intents SET status='risk_approved' WHERE client_order_id=?",
            ("mismatched-fill",),
        )
    result = portfolio.reconcile_order_intents()
    assert result["reconciliation_required"] == 1
    intent = portfolio.get_order_intent("mismatched-fill")
    assert intent["status"] == "reconciliation_required"
    assert [event["to_status"] for event in portfolio.list_order_intent_events("mismatched-fill")][-1] == "reconciliation_required"
