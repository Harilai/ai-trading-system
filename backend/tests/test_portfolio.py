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


def test_partial_sell_preserves_remaining_cost_basis(portfolio):
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
