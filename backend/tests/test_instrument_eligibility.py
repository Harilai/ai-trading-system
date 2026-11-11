from app.instrument_eligibility import evaluate_instrument_eligibility
from app.providers.base import (
    AssetClass,
    MarketType,
    ProviderCapabilities,
    VenueType,
)


def capabilities(**overrides):
    values = {
        "provider_id": "broker-x",
        "venue_type": VenueType.BROKER,
        "asset_classes": (AssetClass.EQUITY,),
        "market_types": (MarketType.CASH_EQUITY,),
        "public_market_data": True,
        "order_management": True,
        "supports_limit_orders": True,
        "supports_client_order_id": True,
        "supports_partial_fills": True,
        "supports_cancel": True,
        "supports_reconciliation": True,
        "balances": True,
        "positions": True,
    }
    values.update(overrides)
    return ProviderCapabilities(**values)


def instrument(**overrides):
    values = {
        "provider_id": "broker-x",
        "symbol": "ACME",
        "asset_class": "equity",
        "market_type": "cash_equity",
        "base": "ACME",
        "quote": "USD",
        "native_symbol": "ACME",
        "active": True,
        "trading_status": "active",
        "minimum_amount": 1,
        "minimum_cost": 1,
        "maker_fee": 0,
        "taker_fee": 0.001,
        "precision_mode": "tick_size",
        "price_tick": 0.01,
        "amount_step": 1,
        "session_timezone": "America/New_York",
    }
    values.update(overrides)
    return values


def test_complete_instrument_is_supported_only_with_registered_execution_adapter():
    result = evaluate_instrument_eligibility(
        instrument(),
        capabilities(),
        catalogue_id="inst_acme",
        execution_adapter_configured=True,
    )

    assert result.status == "supported"
    assert result.missing_requirements == ()
    assert result.blockers == ()


def test_market_data_provider_without_order_management_is_unsupported():
    result = evaluate_instrument_eligibility(
        instrument(),
        capabilities(order_management=False),
        catalogue_id="inst_acme",
        execution_adapter_configured=True,
    )

    assert result.status == "unsupported"
    assert result.execution_capability == "unsupported"
    assert any("data-only" in blocker for blocker in result.blockers)


def test_missing_precision_and_minimums_fail_closed_as_unknown():
    result = evaluate_instrument_eligibility(
        instrument(
            precision_mode="unknown",
            price_tick=None,
            amount_step=None,
            minimum_amount=None,
            minimum_cost=None,
        ),
        capabilities(),
        catalogue_id="inst_acme",
        execution_adapter_configured=True,
    )

    assert result.status == "unknown"
    assert {
        "precision_mode", "minimum_amount", "minimum_cost",
    }.issubset(set(result.missing_requirements))


def test_inactive_instrument_is_unsupported():
    result = evaluate_instrument_eligibility(
        instrument(active=False, trading_status="suspended"),
        capabilities(),
        catalogue_id="inst_acme",
        execution_adapter_configured=True,
    )

    assert result.status == "unsupported"


def test_unregistered_provider_is_not_configured():
    result = evaluate_instrument_eligibility(
        instrument(),
        None,
        catalogue_id="inst_acme",
    )

    assert result.status == "not_configured"
    assert result.missing_requirements == ("provider_configuration",)


def test_capability_identity_or_asset_mismatch_blocks_instrument():
    result = evaluate_instrument_eligibility(
        instrument(),
        capabilities(provider_id="different-provider", asset_classes=(AssetClass.CRYPTO,)),
        catalogue_id="inst_acme",
        execution_adapter_configured=True,
    )

    assert result.status == "unsupported"
    assert any("identity" in blocker.lower() for blocker in result.blockers)
    assert any("asset class" in blocker.lower() for blocker in result.blockers)


def test_valid_market_capability_without_execution_adapter_is_not_configured():
    result = evaluate_instrument_eligibility(
        instrument(),
        capabilities(),
        catalogue_id="inst_acme",
        execution_adapter_configured=False,
    )

    assert result.status == "not_configured"
    assert result.execution_capability == "not_configured"
