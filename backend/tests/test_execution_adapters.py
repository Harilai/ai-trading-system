import pytest

from app.accounts.binance_demo import BinanceSpotDemoExecution
from app.execution_adapters import (
    BinanceSpotDemoScopedAdapter,
    PaperPortfolioScopedAdapter,
    ScopedAccountAdapterError,
)
from app.execution_registry import CapabilityState, ExecutionEnvironment
from app.portfolio import PaperPortfolio


def make_binance_demo_account():
    account = object.__new__(BinanceSpotDemoExecution)
    account.describe = lambda: {
        "provider_id": "ccxt:binance-demo",
        "account_alias": "binance-spot-demo",
        "environment": "demo",
        "demo_only": True,
        "production_endpoint_allowed": False,
    }
    account.fetch_balances = lambda: {
        "provider_id": "ccxt:binance-demo",
        "environment": "demo",
        "market_type": "spot",
        "demo_only": True,
        "withdrawals_enabled": False,
        "fetched_at": "2026-11-11T10:00:00+00:00",
        "balances": [
            {"asset": "EUR", "free": "100", "used": "0", "total": "100"},
            {"asset": "BTC", "free": "0.01", "used": "0", "total": "0.01"},
        ],
    }
    return account

def test_binance_demo_wrapper_requires_explicit_account_scope():
    with pytest.raises(ScopedAccountAdapterError, match="account scope"):
        BinanceSpotDemoScopedAdapter(make_binance_demo_account(), account_id="")


def test_binance_demo_wrapper_rejects_non_demo_or_production_capability():
    account = FakeBinanceDemo()
    account.describe = lambda: {
        **FakeBinanceDemo.describe(account),
        "environment": "live",
        "demo_only": False,
        "production_endpoint_allowed": True,
    }
    with pytest.raises(ScopedAccountAdapterError, match="isolated Demo"):
        BinanceSpotDemoScopedAdapter(account, account_id="binance-demo-account")


def test_binance_demo_wrapper_scopes_balance_snapshot_and_does_not_infer_positions():
    account = FakeBinanceDemo()
    adapter = BinanceSpotDemoScopedAdapter(account, account_id="binance-demo-account")
    snapshot = adapter.fetch_snapshot()
    assert snapshot.scope.environment is ExecutionEnvironment.DEMO
    assert snapshot.scope.account_id == "binance-demo-account"
    assert [item.asset for item in snapshot.balances] == ["EUR", "BTC"]
    assert snapshot.positions == ()
    assert adapter.capabilities().positions.state is CapabilityState.UNKNOWN


def test_binance_demo_wrapper_rejects_snapshot_scope_mismatch():
    account = FakeBinanceDemo()
    account.fetch_balances = lambda: {
        "environment": "live",
    }
    adapter = BinanceSpotDemoScopedAdapter(account, account_id="binance-demo-account")
    with pytest.raises(ScopedAccountAdapterError, match="does not match"):
        adapter.fetch_snapshot()


def test_paper_wrapper_exposes_internal_ledger_scope_and_snapshot(tmp_path):
    portfolio = PaperPortfolio(str(tmp_path / "paper.sqlite"), initial_cash=250)
    adapter = PaperPortfolioScopedAdapter(portfolio, account_id="paper-primary")
    snapshot = adapter.fetch_snapshot()
    assert snapshot.scope.provider_id == "internal:paper-portfolio"
    assert snapshot.scope.venue_id == "internal-ledger"
    assert snapshot.scope.environment is ExecutionEnvironment.PAPER
    assert snapshot.scope.account_id == "paper-primary"
    eur = next(item for item in snapshot.balances if item.asset == "EUR")
    assert eur.total == "250.0"
    capabilities = adapter.capabilities()
    assert capabilities.supports("balances")
    assert capabilities.supports("positions")
    assert capabilities.supports("order_reconciliation")
    assert capabilities.partial_fills.state is CapabilityState.UNSUPPORTED


def test_paper_wrapper_requires_explicit_account_scope(tmp_path):
    portfolio = PaperPortfolio(str(tmp_path / "paper.sqlite"), initial_cash=20)
    with pytest.raises(ScopedAccountAdapterError, match="account scope"):
        PaperPortfolioScopedAdapter(portfolio, account_id=" ")
