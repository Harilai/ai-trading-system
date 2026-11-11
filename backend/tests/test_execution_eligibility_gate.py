import pytest

from app.agents.contracts import RiskContext, TradeProposal
from app.agents.risk import RiskManager
from app.execution import ExecutionRouter
from app.instrument_eligibility import evaluate_instrument_eligibility
from app.providers.base import AssetClass, MarketInstrument, MarketType, ProviderCapabilities, VenueType


class FakeExecutionProvider:
    provider_id = "broker-x"

    def __init__(self, *, asset_classes=(AssetClass.EQUITY,), market_types=(MarketType.CASH_EQUITY,)):
        self._asset_classes = asset_classes
        self._market_types = market_types

    def capabilities(self):
        return ProviderCapabilities(
            provider_id=self.provider_id,
            venue_type=VenueType.BROKER,
            asset_classes=self._asset_classes,
            market_types=self._market_types,
            order_management=True,
            supports_limit_orders=True,
            supports_client_order_id=True,
            supports_partial_fills=True,
            supports_cancel=True,
            supports_reconciliation=True,
            balances=True,
            positions=True,
        )


def test_execution_registry_does_not_confuse_market_data_with_execution():
    router = ExecutionRouter()
    assert router.get_provider("broker-x") is None
    assert router.capabilities_for("broker-x") is None
    assert router.supports_instrument("broker-x", "equity", "cash_equity") is False


def test_execution_registry_requires_declared_asset_and_market_scope():
    router = ExecutionRouter({"broker-x": FakeExecutionProvider()})
    assert router.supports_instrument("broker-x", "equity", "cash_equity") is True
    assert router.supports_instrument("broker-x", "crypto", "spot") is False
    assert router.supports_instrument("broker-x", "equity", "margin") is False


def test_execution_registry_rejects_capability_identity_mismatch():
    provider = FakeExecutionProvider()
    provider.provider_id = "other"
    router = ExecutionRouter({"broker-x": provider})
    with pytest.raises(ValueError, match="identity mismatch"):
        router.capabilities_for("broker-x")


def risk_context():
    return RiskContext(
        equity_eur=10000,
        available_cash_eur=10000,
        quote_currency="USD",
        available_cash_by_currency={"USD": 10000},
        positions={},
    )


def proposal():
    return TradeProposal(
        symbol="ACME",
        provider_id="broker-x",
        instrument_id="broker-x:cash_equity:ACME",
        asset_class="equity",
        market_type="cash_equity",
        side="buy",
        quantity=1,
        reference_price=100,
        stop_loss=95,
        rationale="Test proposal for eligibility gate",
        source_agent="manual_owner",
    )


def supported_evidence(**overrides):
    evidence = {
        "status": "supported",
        "provider_id": "broker-x",
        "catalogue_id": "catalogue-acme",
        "instrument_id": "broker-x:cash_equity:ACME",
        "execution_adapter_configured": True,
    }
    evidence.update(overrides)
    return evidence


def test_risk_manager_fails_closed_for_unknown_or_unsupported_eligibility():
    manager = RiskManager()
    for status in ("unknown", "not_configured", "unsupported", None):
        decision = manager.evaluate(
            proposal(), risk_context(),
            instrument_eligibility=supported_evidence(status=status),
        )
        assert decision.accepted is False
        assert "not eligible" in decision.reason.lower()


def test_risk_manager_rejects_eligibility_from_another_provider_or_instrument():
    manager = RiskManager()
    provider_mismatch = manager.evaluate(
        proposal(), risk_context(),
        instrument_eligibility=supported_evidence(provider_id="other"),
    )
    instrument_mismatch = manager.evaluate(
        proposal(), risk_context(),
        instrument_eligibility=supported_evidence(instrument_id="broker-x:cash_equity:OTHER"),
    )
    assert provider_mismatch.accepted is False
    assert "provider identity" in provider_mismatch.reason.lower()
    assert instrument_mismatch.accepted is False
    assert "instrument eligibility identity" in instrument_mismatch.reason.lower()


def test_risk_manager_requires_explicit_execution_adapter_configuration():
    decision = RiskManager().evaluate(
        proposal(), risk_context(),
        instrument_eligibility=supported_evidence(execution_adapter_configured=False),
    )
    assert decision.accepted is False
    assert "adapter" in decision.reason.lower()
