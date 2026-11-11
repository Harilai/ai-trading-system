from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .providers.base import ExecutionProvider, MarketInstrument


@dataclass(frozen=True)
class NormalizedOrder:
    provider_id: str
    instrument_id: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: str = "GTC"


class ExecutionRouter:
    """Provider-neutral validation and routing; live execution is disabled by default."""

    def __init__(
        self,
        providers: dict[str, ExecutionProvider] | None = None,
        *,
        live_enabled: bool = False,
    ):
        self.providers = providers or {}
        self.live_enabled = live_enabled

    def get_provider(self, provider_id: str) -> ExecutionProvider | None:
        """Return an explicitly registered execution adapter, never a market-data provider."""
        return self.providers.get(provider_id)

    def capabilities_for(self, provider_id: str):
        provider = self.get_provider(provider_id)
        if provider is None:
            return None
        capabilities = provider.capabilities()
        if capabilities.provider_id != provider_id:
            raise ValueError("Execution provider capability identity mismatch")
        return capabilities

    def register(self, provider: ExecutionProvider) -> None:
        if provider.provider_id in self.providers:
            raise ValueError(f"Execution provider already registered: {provider.provider_id}")
        self.providers[provider.provider_id] = provider

    def validate(self, provider_id: str, instrument: MarketInstrument, order: NormalizedOrder) -> dict[str, Any]:
        provider = self.providers.get(provider_id)
        if provider is None:
            raise ValueError(f"No execution adapter configured for {provider_id}")
        if instrument.provider_id != provider_id or order.provider_id != provider_id:
            raise ValueError("Provider identity mismatch")
        caps = provider.capabilities()
        if not caps.order_management:
            raise ValueError(f"{provider_id} does not expose validated order management")
        if order.order_type == "market" and not caps.supports_market_orders:
            raise ValueError("Market orders are not supported by this provider")
        if order.order_type == "limit" and not caps.supports_limit_orders:
            raise ValueError("Limit orders are not supported by this provider")
        if order.order_type == "stop" and not caps.supports_stop_orders:
            raise ValueError("Stop orders are not supported by this provider")
        return provider.validate_order(instrument, order.__dict__)

    def submit(
        self,
        provider_id: str,
        instrument: MarketInstrument,
        order: NormalizedOrder,
        client_order_id: str,
        *,
        risk_decision: Any,
    ):
        if not self.live_enabled:
            raise ValueError("Live execution is disabled")
        if not getattr(risk_decision, "accepted", False):
            raise ValueError("A positive Risk Manager approval is required before execution")
        if not client_order_id:
            raise ValueError("A stable client_order_id is mandatory")
        validation = self.validate(provider_id, instrument, order)
        if not validation.get("accepted"):
            raise ValueError(validation.get("reason", "Provider rejected order validation"))
        return self.providers[provider_id].submit_order(order.__dict__, client_order_id)
