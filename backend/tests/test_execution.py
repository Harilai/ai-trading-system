import pytest

from app.execution import ExecutionRouter, NormalizedOrder
from app.providers.base import (
    AssetClass,
    MarketInstrument,
    MarketType,
    ProviderCapabilities,
    VenueType,
)


class FakeExecutionProvider:
    provider_id = "fake-execution"

    def capabilities(self):
        return ProviderCapabilities(
            provider_id=self.provider_id,
            venue_type=VenueType.CEX,
            asset_classes=(AssetClass.CRYPTO,),
            market_types=(MarketType.SPOT,),
            public_market_data=True,
            authenticated_account=True,
            order_management=True,
            supports_market_orders=True,
            supports_client_order_id=True,
        )

    def validate_order(self, instrument, order):
        return {"accepted": True}

    def submit_order(self, order, client_order_id):
        return {"id": "order-1", "client_order_id": client_order_id}

    def get_order(self, instrument, order_id):
        return None

    def cancel_order(self, instrument, order_id):
        return {"id": order_id, "cancelled": True}

    def get_balances(self):
        return {}

    def get_positions(self):
        return []

    def reconcile(self):
        return {"ok": True}


@pytest.fixture
def instrument():
    return MarketInstrument(
        provider_id="fake-execution",
        symbol="BTC/EUR",
        asset_class=AssetClass.CRYPTO,
        market_type=MarketType.SPOT,
        base="BTC",
        quote="EUR",
        active=True,
    )


@pytest.fixture
def order():
    return NormalizedOrder(
        provider_id="fake-execution",
        instrument_id="fake-execution:spot:BTC/EUR",
        symbol="BTC/EUR",
        side="buy",
        order_type="market",
        quantity=0.001,
    )


def test_execution_router_is_disabled_by_default(instrument, order):
    router = ExecutionRouter({"fake-execution": FakeExecutionProvider()})
    with pytest.raises(ValueError, match="Live execution is disabled"):
        router.submit(
            "fake-execution",
            instrument,
            order,
            "stable-id",
            risk_decision=type("Risk", (), {"accepted": True})(),
        )


def test_execution_router_requires_risk_manager_approval(instrument, order):
    router = ExecutionRouter(
        {"fake-execution": FakeExecutionProvider()},
        live_enabled=True,
    )
    with pytest.raises(ValueError, match="Risk Manager approval"):
        router.submit(
            "fake-execution",
            instrument,
            order,
            "stable-id",
            risk_decision=type("Risk", (), {"accepted": False})(),
        )


def test_execution_router_checks_provider_identity(instrument, order):
    router = ExecutionRouter(
        {"fake-execution": FakeExecutionProvider()},
        live_enabled=True,
    )
    mismatched = NormalizedOrder(**{**order.__dict__, "provider_id": "other"})
    with pytest.raises(ValueError, match="Provider identity mismatch"):
        router.validate("fake-execution", instrument, mismatched)


def test_execution_router_submits_only_after_opt_in_and_risk_approval(instrument, order):
    router = ExecutionRouter(
        {"fake-execution": FakeExecutionProvider()},
        live_enabled=True,
    )
    result = router.submit(
        "fake-execution",
        instrument,
        order,
        "stable-id",
        risk_decision=type("Risk", (), {"accepted": True})(),
    )
    assert result["client_order_id"] == "stable-id"
