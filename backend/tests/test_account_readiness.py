from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.account_readiness import PaperAccountReadiness
from app.execution_adapters import PaperPortfolioScopedAdapter
from app.execution_registry import ExecutionEnvironment, ExecutionScope
from app.portfolio import PaperPortfolio


def make_service(tmp_path):
    portfolio = PaperPortfolio(str(tmp_path / "paper.sqlite"), initial_cash=250)
    adapter = PaperPortfolioScopedAdapter(portfolio, account_id="paper-primary")
    return portfolio, adapter, PaperAccountReadiness(portfolio, adapter)


def test_internal_paper_account_is_ready_with_fresh_consistent_ledger(tmp_path):
    _, _, service = make_service(tmp_path)

    result = service.evaluate(side="buy")

    assert result["status"] == "ready"
    assert result["scope"] == {
        "provider_id": "internal:paper-portfolio",
        "venue_id": "internal-ledger",
        "account_id": "paper-primary",
        "environment": "paper",
        "market_type": "spot",
    }
    assert result["snapshot"]["identity_basis"] == "local_internal_paper_ledger"
    assert result["snapshot"]["remote_identity_verified"] is False
    assert result["reconciliation"]["status"] == "clear"
    assert result["reconciliation"]["unresolved_intents"] == 0
    assert result["blockers"] == []


def test_preflight_rejects_stale_snapshot(tmp_path, monkeypatch):
    _, adapter, service = make_service(tmp_path)
    original = adapter.fetch_snapshot
    stale = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    monkeypatch.setattr(adapter, "fetch_snapshot", lambda: replace(original(), fetched_at=stale))

    result = service.evaluate(side="buy")

    assert result["status"] == "blocked"
    assert "snapshot_stale" in result["blockers"]


def test_preflight_rejects_snapshot_from_another_account_scope(tmp_path, monkeypatch):
    _, adapter, service = make_service(tmp_path)
    original = adapter.fetch_snapshot
    wrong_scope = ExecutionScope(
        provider_id="internal:paper-portfolio",
        venue_id="internal-ledger",
        account_id="paper-other",
        environment=ExecutionEnvironment.PAPER,
        market_type="spot",
    )
    monkeypatch.setattr(adapter, "fetch_snapshot", lambda: replace(original(), scope=wrong_scope))

    result = service.evaluate(side="buy")

    assert result["status"] == "blocked"
    assert "snapshot_scope_mismatch" in result["blockers"]


def test_preflight_blocks_unresolved_order_intent_but_can_exclude_its_own_resume(tmp_path):
    portfolio, _, service = make_service(tmp_path)
    request = {
        "symbol": "BTC/EUR",
        "side": "buy",
        "quantity": 0.001,
        "price": 50000,
        "fee_rate": 0.001,
        "slippage_rate": 0.001,
        "max_fraction": 0.25,
        "minimum": 1,
        "client_order_id": "pending-paper-entry",
        "provider_id": "ccxt:kraken",
        "instrument_id": "ccxt:kraken:spot:BTC/EUR",
        "quote_currency": "EUR",
        "market_timestamp_source": "unknown",
    }
    portfolio.record_order_intent("pending-paper-entry", request, {"accepted": True})

    blocked = service.evaluate(side="buy")
    resumed = service.evaluate(side="buy", exclude_client_order_id="pending-paper-entry")

    assert blocked["status"] == "blocked"
    assert "unresolved_paper_order_intents" in blocked["blockers"]
    assert resumed["status"] == "ready"


def test_risk_reducing_sell_does_not_require_entry_preflight(tmp_path):
    _, _, service = make_service(tmp_path)

    result = service.evaluate(side="sell")

    assert result["status"] == "not_required"
    assert result["blockers"] == []



def test_preflight_rejects_negative_or_inconsistent_cash_balance(tmp_path, monkeypatch):
    from dataclasses import replace

    _, adapter, service = make_service(tmp_path)
    original = adapter.fetch_snapshot

    def invalid_snapshot():
        snapshot = original()
        first = snapshot.balances[0]
        invalid = replace(first, free="-1", used="0", total="-1")
        return replace(snapshot, balances=(invalid, *snapshot.balances[1:]))

    monkeypatch.setattr(adapter, "fetch_snapshot", invalid_snapshot)

    result = service.evaluate(side="buy")

    assert result["status"] == "blocked"
    assert "balance_values_inconsistent" in result["blockers"]



def test_preflight_keeps_same_instrument_id_isolated_across_providers(tmp_path, monkeypatch):
    from dataclasses import replace
    from app.execution_adapters import ScopedPosition

    _, adapter, service = make_service(tmp_path)
    original = adapter.fetch_snapshot

    def multi_provider_snapshot():
        snapshot = original()
        positions = (
            ScopedPosition("ccxt:kraken", "shared-id", "BTC/EUR", "0.1", "50000", "EUR"),
            ScopedPosition("ccxt:coinbase", "shared-id", "BTC/EUR", "0.2", "51000", "EUR"),
        )
        return replace(snapshot, positions=positions)

    monkeypatch.setattr(adapter, "fetch_snapshot", multi_provider_snapshot)

    result = service.evaluate(side="buy")

    assert result["status"] == "ready"
