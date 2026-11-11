from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from threading import RLock
from typing import Any


class ExecutionRegistryError(ValueError):
    """Invalid or ambiguous execution registration."""


class ExecutionEnvironment(str, Enum):
    MOCK = "mock"
    BACKTEST = "backtest"
    PAPER = "paper"
    DEMO = "demo"
    TESTNET = "testnet"
    LIVE = "live"


class CapabilityState(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"
    NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True)
class ExecutionScope:
    """Immutable identity of one executable account/venue/market scope."""

    provider_id: str
    venue_id: str
    account_id: str
    environment: ExecutionEnvironment
    market_type: str

    def __post_init__(self) -> None:
        for field_name in ("provider_id", "venue_id", "account_id", "market_type"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ExecutionRegistryError(f"{field_name} is required")
            if value.strip() != value:
                raise ExecutionRegistryError(f"{field_name} must be canonical without surrounding whitespace")
        if not isinstance(self.environment, ExecutionEnvironment):
            try:
                object.__setattr__(self, "environment", ExecutionEnvironment(self.environment))
            except (TypeError, ValueError) as exc:
                raise ExecutionRegistryError("Unsupported execution environment") from exc

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        return (
            self.provider_id,
            self.venue_id,
            self.account_id,
            self.environment.value,
            self.market_type,
        )


@dataclass(frozen=True)
class CapabilityEvidence:
    """Capability declaration with independent provenance and verification state."""

    state: CapabilityState
    source: str
    verified_at: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, CapabilityState):
            try:
                object.__setattr__(self, "state", CapabilityState(self.state))
            except (TypeError, ValueError) as exc:
                raise ExecutionRegistryError("Unsupported capability state") from exc
        if not isinstance(self.source, str) or not self.source.strip():
            raise ExecutionRegistryError("Capability evidence source is required")
        if self.verified_at is not None:
            try:
                parsed = datetime.fromisoformat(self.verified_at.replace("Z", "+00:00"))
            except (TypeError, ValueError) as exc:
                raise ExecutionRegistryError("Capability verification timestamp must be ISO-8601") from exc
            if parsed.tzinfo is None:
                raise ExecutionRegistryError("Capability verification timestamp must include a timezone")
            object.__setattr__(self, "verified_at", parsed.astimezone(timezone.utc).isoformat())
        if self.detail is not None and not isinstance(self.detail, str):
            raise ExecutionRegistryError("Capability evidence detail must be text")


@dataclass(frozen=True)
class ExecutionCapabilities:
    asset_classes: tuple[str, ...]
    order_types: tuple[str, ...]
    idempotent_client_order_id: CapabilityEvidence
    partial_fills: CapabilityEvidence
    cancellation: CapabilityEvidence
    order_reconciliation: CapabilityEvidence
    balances: CapabilityEvidence
    positions: CapabilityEvidence
    withdrawals: CapabilityEvidence
    transfers: CapabilityEvidence

    def __post_init__(self) -> None:
        for field_name in ("asset_classes", "order_types"):
            values = getattr(self, field_name)
            if not isinstance(values, tuple) or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                raise ExecutionRegistryError(f"{field_name} must be a tuple of non-empty strings")
            if len(values) != len(set(values)):
                raise ExecutionRegistryError(f"{field_name} cannot contain duplicates")
        for field_name in (
            "idempotent_client_order_id", "partial_fills", "cancellation",
            "order_reconciliation", "balances", "positions", "withdrawals", "transfers",
        ):
            if not isinstance(getattr(self, field_name), CapabilityEvidence):
                raise ExecutionRegistryError(f"{field_name} requires explicit capability evidence")

    def supports(
        self,
        capability_name: str,
        *,
        now: datetime | None = None,
        max_age: timedelta = timedelta(hours=24),
    ) -> bool:
        evidence = getattr(self, capability_name, None)
        if not isinstance(evidence, CapabilityEvidence) or evidence.state is not CapabilityState.SUPPORTED:
            return False
        if evidence.verified_at is None:
            return False
        if not isinstance(max_age, timedelta) or max_age.total_seconds() <= 0:
            raise ExecutionRegistryError("Capability evidence maximum age must be positive")
        verified_at = datetime.fromisoformat(evidence.verified_at)
        reference_time = now or datetime.now(timezone.utc)
        if reference_time.tzinfo is None:
            raise ExecutionRegistryError("Capability evaluation time must include a timezone")
        age = reference_time.astimezone(timezone.utc) - verified_at
        return timedelta(0) <= age <= max_age

    def describe(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "asset_classes": list(self.asset_classes),
            "order_types": list(self.order_types),
        }
        for field_name in (
            "idempotent_client_order_id", "partial_fills", "cancellation",
            "order_reconciliation", "balances", "positions", "withdrawals", "transfers",
        ):
            result[field_name] = asdict(getattr(self, field_name))
        return result


@dataclass(frozen=True)
class ExecutionRegistration:
    scope: ExecutionScope
    capabilities: ExecutionCapabilities
    adapter: Any
    registered_at: str

    def __post_init__(self) -> None:
        if self.adapter is None:
            raise ExecutionRegistryError("Execution adapter is required")
        try:
            timestamp = datetime.fromisoformat(self.registered_at.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ExecutionRegistryError("Registration timestamp must be ISO-8601") from exc
        if timestamp.tzinfo is None:
            raise ExecutionRegistryError("Registration timestamp must include a timezone")


class ExecutionAdapterRegistry:
    """Explicit scoped registry. Empty by default; never discovers or activates providers."""

    def __init__(self) -> None:
        self._registrations: dict[tuple[str, str, str, str, str], ExecutionRegistration] = {}
        self._lock = RLock()

    def register(
        self,
        scope: ExecutionScope,
        capabilities: ExecutionCapabilities,
        adapter: Any,
        *,
        now: datetime | None = None,
    ) -> ExecutionRegistration:
        if not isinstance(scope, ExecutionScope):
            raise ExecutionRegistryError("A complete execution scope is mandatory")
        if not isinstance(capabilities, ExecutionCapabilities):
            raise ExecutionRegistryError("Explicit execution capabilities are mandatory")
        registered_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        registration = ExecutionRegistration(scope, capabilities, adapter, registered_at)
        with self._lock:
            if scope.key in self._registrations:
                raise ExecutionRegistryError("Execution scope is already registered")
            self._registrations[scope.key] = registration
        return registration

    def get(self, scope: ExecutionScope) -> ExecutionRegistration | None:
        if not isinstance(scope, ExecutionScope):
            raise ExecutionRegistryError("A complete execution scope is mandatory")
        with self._lock:
            return self._registrations.get(scope.key)

    def list_registrations(self) -> list[dict[str, Any]]:
        with self._lock:
            registrations = tuple(self._registrations.values())
        return [
            {
                "scope": {
                    "provider_id": item.scope.provider_id,
                    "venue_id": item.scope.venue_id,
                    "account_id": item.scope.account_id,
                    "environment": item.scope.environment.value,
                    "market_type": item.scope.market_type,
                },
                "capabilities": item.capabilities.describe(),
                "registered_at": item.registered_at,
            }
            for item in sorted(registrations, key=lambda value: value.scope.key)
        ]

    def supports_instrument(
        self,
        scope: ExecutionScope,
        *,
        asset_class: str,
        order_type: str,
        required_capabilities: tuple[str, ...] = (
            "idempotent_client_order_id",
            "partial_fills",
            "cancellation",
            "order_reconciliation",
            "balances",
            "positions",
        ),
        now: datetime | None = None,
        evidence_max_age: timedelta = timedelta(hours=24),
    ) -> bool:
        registration = self.get(scope)
        if registration is None:
            return False
        capabilities = registration.capabilities
        if asset_class not in capabilities.asset_classes or order_type not in capabilities.order_types:
            return False
        return all(
            capabilities.supports(name, now=now, max_age=evidence_max_age)
            for name in required_capabilities
        )

    def __len__(self) -> int:
        with self._lock:
            return len(self._registrations)
