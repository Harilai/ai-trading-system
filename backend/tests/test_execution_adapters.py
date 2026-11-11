import pytest

from app.accounts.binance_demo import BinanceSpotDemoExecution
from app.execution_adapters import (
    BinanceSpotDemoScopedAdapter,
    PaperPortfolioScopedAdapter,
    ScopedAccountAdapterError,
)
from app.execution_registry import CapabilityState, ExecutionAdapterRegistry, ExecutionEnvironment
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
    account = make_binance_demo_account()
    original_description = account.describe()
    account.describe = lambda: {
        **original_description,
        "environment": "live",
        "demo_only": False,
        "production_endpoint_allowed": True,
    }
    with pytest.raises(ScopedAccountAdapterError, match="isolated Demo"):
        BinanceSpotDemoScopedAdapter(account, account_id="binance-demo-account")


def test_binance_demo_wrapper_scopes_balance_snapshot_and_does_not_infer_positions():
    account = make_binance_demo_account()
    adapter = BinanceSpotDemoScopedAdapter(account, account_id="binance-demo-account")
    snapshot = adapter.fetch_snapshot()
    assert snapshot.scope.environment is ExecutionEnvironment.DEMO
    assert snapshot.scope.account_id == "binance-demo-account"
    assert [item.asset for item in snapshot.balances] == ["EUR", "BTC"]
    assert snapshot.positions == ()
    assert adapter.account_identity_verified is False
    assert adapter.capabilities().positions.state is CapabilityState.UNKNOWN


def test_binance_demo_wrapper_rejects_snapshot_scope_mismatch():
    account = make_binance_demo_account()
    account.fetch_balances = lambda: {
        "provider_id": "ccxt:binance-demo",
        "environment": "live",
        "market_type": "spot",
        "demo_only": True,
        "withdrawals_enabled": False,
        "fetched_at": "2026-11-11T10:00:00+00:00",
        "balances": [],
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
    assert adapter.account_identity_verified is False
    capabilities = adapter.capabilities()
    assert capabilities.supports("balances")
    assert capabilities.supports("positions")
    assert capabilities.supports("order_reconciliation")
    assert capabilities.asset_classes == ("crypto",)
    assert capabilities.order_types == ("market",)
    assert capabilities.partial_fills.state is CapabilityState.UNSUPPORTED


def test_paper_wrapper_requires_explicit_account_scope(tmp_path):
    portfolio = PaperPortfolio(str(tmp_path / "paper.sqlite"), initial_cash=20)
    with pytest.raises(ScopedAccountAdapterError, match="account scope"):
        PaperPortfolioScopedAdapter(portfolio, account_id=" ")



def test_binance_demo_readiness_fails_closed_until_positions_are_modeled():
    adapter = BinanceSpotDemoScopedAdapter(
        make_binance_demo_account(),
        account_id="binance-demo-account",
    )
    registry = ExecutionAdapterRegistry()
    registry.register(adapter.scope, adapter.capabilities(), adapter)
    assert not registry.supports_instrument(
        adapter.scope,
        asset_class="crypto",
        order_type="limit",
    )


def test_paper_readiness_fails_closed_until_partial_fill_and_cancel_semantics_exist(tmp_path):
    portfolio = PaperPortfolio(str(tmp_path / "paper.sqlite"), initial_cash=250)
    adapter = PaperPortfolioScopedAdapter(portfolio, account_id="paper-primary")
    registry = ExecutionAdapterRegistry()
    registry.register(adapter.scope, adapter.capabilities(), adapter)
    assert not registry.supports_instrument(
        adapter.scope,
        asset_class="crypto",
        order_type="limit",
    )



def test_binance_demo_wrapper_rejects_naive_snapshot_timestamp():
    account = make_binance_demo_account()
    account.fetch_balances = lambda: {
        "provider_id": "ccxt:binance-demo",
        "environment": "demo",
        "market_type": "spot",
        "demo_only": True,
        "withdrawals_enabled": False,
        "fetched_at": "2026-11-11T10:00:00",
        "balances": [],
    }
    adapter = BinanceSpotDemoScopedAdapter(account, account_id="binance-demo-account")
    with pytest.raises(ScopedAccountAdapterError, match="timezone"):
        adapter.fetch_snapshot()


def test_binance_demo_scope_alias_is_not_misrepresented_as_verified_exchange_identity():
    adapter = BinanceSpotDemoScopedAdapter(
        make_binance_demo_account(),
        account_id="operator-configured-alias",
    )
    assert adapter.scope.account_id == "operator-configured-alias"
    assert adapter.account_identity_verified is False



def test_paper_wrapper_preserves_tiny_positive_spot_quantity_exactly(tmp_path):
    portfolio = PaperPortfolio(str(tmp_path / "paper.sqlite"), initial_cash=20)
    portfolio.snapshot = lambda: {
        "cash_balances": [],
        "positions": [{
            "provider_id": "ccxt:kraken",
            "instrument_id": "BTC/EUR",
            "symbol": "BTC/EUR",
            "quantity": "1e-400",
            "average_price": "60000.00",
            "quote_currency": "EUR",
        }],
    }
    snapshot = PaperPortfolioScopedAdapter(portfolio, account_id="paper-primary").fetch_snapshot()
    assert len(snapshot.positions) == 1
    assert snapshot.positions[0].quantity == "1E-400"


def test_paper_wrapper_rejects_non_finite_position_quantity(tmp_path):
    portfolio = PaperPortfolio(str(tmp_path / "paper.sqlite"), initial_cash=20)
    portfolio.snapshot = lambda: {
        "cash_balances": [],
        "positions": [{
            "provider_id": "ccxt:kraken",
            "instrument_id": "BTC/EUR",
            "symbol": "BTC/EUR",
            "quantity": "NaN",
            "average_price": "60000",
            "quote_currency": "EUR",
        }],
    }
    adapter = PaperPortfolioScopedAdapter(portfolio, account_id="paper-primary")
    with pytest.raises(ScopedAccountAdapterError, match="finite"):
        adapter.fetch_snapshot()
