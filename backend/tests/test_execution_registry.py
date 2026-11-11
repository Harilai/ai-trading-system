from datetime import datetime, timedelta, timezone

import pytest

from app.execution_registry import (
    CapabilityEvidence,
    CapabilityState,
    ExecutionAdapterRegistry,
    ExecutionCapabilities,
    ExecutionEnvironment,
    ExecutionRegistryError,
    ExecutionScope,
)


def evidence(state=CapabilityState.SUPPORTED, source="unit-test"):
    return CapabilityEvidence(state=state, source=source, verified_at="2026-11-11T10:00:00+00:00")


def capabilities(**overrides):
    values = {
        "asset_classes": ("crypto",),
        "order_types": ("limit",),
        "idempotent_client_order_id": evidence(),
        "partial_fills": evidence(),
        "cancellation": evidence(),
        "order_reconciliation": evidence(),
        "balances": evidence(),
        "positions": evidence(),
        "withdrawals": evidence(CapabilityState.UNSUPPORTED),
        "transfers": evidence(CapabilityState.UNSUPPORTED),
    }
    values.update(overrides)
    return ExecutionCapabilities(**values)


def scope(**overrides):
    values = {
        "provider_id": "ccxt:binance-demo",
        "venue_id": "binance",
        "account_id": "demo-account-a",
        "environment": ExecutionEnvironment.DEMO,
        "market_type": "spot",
    }
    values.update(overrides)
    return ExecutionScope(**values)


def test_registry_is_empty_by_default_and_does_not_discover_adapters():
    registry = ExecutionAdapterRegistry()
    assert len(registry) == 0
    assert registry.list_registrations() == []
    assert registry.get(scope()) is None


@pytest.mark.parametrize("field,value", [
    ("provider_id", ""),
    ("venue_id", ""),
    ("account_id", ""),
    ("market_type", ""),
    ("account_id", " demo-account-a "),
])
def test_scope_rejects_missing_or_noncanonical_identity(field, value):
    values = {
        "provider_id": "p",
        "venue_id": "v",
        "account_id": "a",
        "environment": ExecutionEnvironment.DEMO,
        "market_type": "spot",
    }
    values[field] = value
    with pytest.raises(ExecutionRegistryError):
        ExecutionScope(**values)


def test_scope_requires_a_known_environment():
    with pytest.raises(ExecutionRegistryError, match="environment"):
        ExecutionScope("p", "v", "a", "production-ish", "spot")


def test_capability_evidence_requires_provenance_and_timezone():
    with pytest.raises(ExecutionRegistryError, match="source"):
        CapabilityEvidence(CapabilityState.SUPPORTED, "")
    with pytest.raises(ExecutionRegistryError, match="timezone"):
        CapabilityEvidence(CapabilityState.SUPPORTED, "manual", "2026-11-11T10:00:00")


def test_registration_requires_explicit_scope_capabilities_and_adapter():
    registry = ExecutionAdapterRegistry()
    with pytest.raises(ExecutionRegistryError):
        registry.register(None, capabilities(), object())
    with pytest.raises(ExecutionRegistryError):
        registry.register(scope(), None, object())
    with pytest.raises(ExecutionRegistryError):
        registry.register(scope(), capabilities(), None)


def test_registration_is_unique_per_complete_scope():
    registry = ExecutionAdapterRegistry()
    adapter = object()
    registry.register(scope(), capabilities(), adapter)
    assert registry.get(scope()).adapter is adapter
    with pytest.raises(ExecutionRegistryError, match="already registered"):
        registry.register(scope(), capabilities(), object())


def test_same_provider_can_register_multiple_isolated_accounts_and_environments():
    registry = ExecutionAdapterRegistry()
    demo_adapter = object()
    paper_adapter = object()
    registry.register(scope(), capabilities(), demo_adapter)
    registry.register(
        scope(account_id="paper-account", environment=ExecutionEnvironment.PAPER),
        capabilities(),
        paper_adapter,
    )
    assert registry.get(scope()).adapter is demo_adapter
    assert registry.get(scope(account_id="paper-account", environment=ExecutionEnvironment.PAPER)).adapter is paper_adapter
    assert registry.get(scope(account_id="wrong-account")) is None
    assert registry.get(scope(environment=ExecutionEnvironment.LIVE)) is None


def test_partial_capabilities_are_reported_but_never_promoted_to_ready():
    registry = ExecutionAdapterRegistry()
    partial = capabilities(positions=evidence(CapabilityState.UNKNOWN))
    registry.register(scope(), partial, object())
    assert registry.supports_instrument(
        scope(), asset_class="crypto", order_type="limit",
    ) is False
    # Read-only account features can be supported without withdrawals/transfers.
    assert partial.supports("balances") is True
    assert partial.supports("positions") is False
    assert partial.supports("withdrawals") is False
    assert partial.supports("transfers") is False


def test_unsupported_market_order_and_asset_are_rejected():
    registry = ExecutionAdapterRegistry()
    registry.register(scope(), capabilities(), object())
    assert not registry.supports_instrument(scope(), asset_class="equity", order_type="limit")
    assert not registry.supports_instrument(scope(), asset_class="crypto", order_type="market")


def test_unknown_capability_fails_closed_even_when_other_capabilities_are_supported():
    registry = ExecutionAdapterRegistry()
    partial = capabilities(order_reconciliation=evidence(CapabilityState.UNKNOWN))
    registry.register(scope(), partial, object())
    assert not registry.supports_instrument(scope(), asset_class="crypto", order_type="limit")


def test_live_scope_is_distinct_and_not_created_by_demo_registration():
    registry = ExecutionAdapterRegistry()
    registry.register(scope(), capabilities(), object())
    live_scope = scope(environment=ExecutionEnvironment.LIVE, account_id="production-account")
    assert registry.get(live_scope) is None
    assert not registry.supports_instrument(live_scope, asset_class="crypto", order_type="limit")


def test_registration_timestamp_is_timezone_aware():
    registry = ExecutionAdapterRegistry()
    with pytest.raises(ExecutionRegistryError, match="timezone"):
        from app.execution_registry import ExecutionRegistration
        ExecutionRegistration(
            scope(),
            capabilities(),
            object(),
            "2026-11-11T10:00:00",
        )



def test_capability_evidence_without_verification_time_is_not_usable():
    unchecked = evidence()
    unchecked = CapabilityEvidence(CapabilityState.SUPPORTED, "manual")
    assert capabilities(idempotent_client_order_id=unchecked).supports("idempotent_client_order_id") is False


def test_stale_or_future_capability_evidence_fails_closed():
    stale = CapabilityEvidence(
        CapabilityState.SUPPORTED,
        "manual",
        "2026-11-09T10:00:00+00:00",
    )
    future = CapabilityEvidence(
        CapabilityState.SUPPORTED,
        "manual",
        "2026-11-12T10:00:00+00:00",
    )
    registry = ExecutionAdapterRegistry()
    registry.register(
        scope(),
        capabilities(
            idempotent_client_order_id=stale,
            partial_fills=future,
        ),
        object(),
    )
    now = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)
    assert not registry.supports_instrument(
        scope(), asset_class="crypto", order_type="limit", now=now,
    )


def test_capability_evidence_freshness_can_be_evaluated_deterministically():
    registry = ExecutionAdapterRegistry()
    registry.register(scope(), capabilities(), object())
    now = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)
    assert registry.supports_instrument(
        scope(), asset_class="crypto", order_type="limit", now=now,
    )
    assert not registry.supports_instrument(
        scope(),
        asset_class="crypto",
        order_type="limit",
        now=now,
        evidence_max_age=timedelta(minutes=30),
    )


def test_registry_inventory_never_serializes_adapter_or_secrets():
    registry = ExecutionAdapterRegistry()
    adapter = type("Adapter", (), {"api_secret": "must-not-leak"})()
    registry.register(scope(), capabilities(), adapter)
    inventory = registry.list_registrations()
    rendered = repr(inventory)
    assert "must-not-leak" not in rendered
    assert "adapter" not in inventory[0]
