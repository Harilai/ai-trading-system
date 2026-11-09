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
    assert {"client_order_id", "provider_id", "instrument_id"} <= trade_columns
    with sqlite3.connect(path) as db:
        withdrawal_columns = {row[1] for row in db.execute("PRAGMA table_info(withdrawal_requests)")}
    assert {"profit_component", "non_reserved_component"} <= withdrawal_columns


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
