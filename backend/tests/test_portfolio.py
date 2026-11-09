import pytest
from app.portfolio import PaperPortfolio, PortfolioError

@pytest.fixture
def portfolio(tmp_path):
    return PaperPortfolio(str(tmp_path/"paper.db"),20)

def test_persistent_initial_balance(tmp_path):
    path=str(tmp_path/"paper.db")
    PaperPortfolio(path,20)
    assert PaperPortfolio(path,20).snapshot()["cash"]==20

def test_buy_sell_and_realised_pnl(portfolio):
    portfolio.execute("BTC/EUR","buy",0.001,10000,0.001,0,1,1)
    assert portfolio.snapshot()["positions"][0]["quantity"]==pytest.approx(0.001)
    portfolio.execute("BTC/EUR","sell",0.001,11000,0.001,0,1,1)
    snap=portfolio.snapshot()
    assert snap["positions"]==[]
    assert snap["realised_pnl"]>0

def test_rejects_wrong_quote(portfolio):
    with pytest.raises(PortfolioError): portfolio.execute("BTC/USDT","buy",0.001,10000,0,0,1,1)

def test_rejects_overallocation(portfolio):
    with pytest.raises(PortfolioError): portfolio.execute("BTC/EUR","buy",0.01,1000,0,0,0.25,1)

def test_rejects_oversell(portfolio):
    with pytest.raises(PortfolioError): portfolio.execute("BTC/EUR","sell",0.1,1000,0,0,1,1)
