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
    """Provider-neutral validation and routing; live submission is opt-in per adapter."""

    def __init__(self, providers: dict[str, ExecutionProvider] | None = None):
        self.providers = providers or {}

    def register(self, provider: ExecutionProvider) -> None:
        if provider.provider_id in self.providers:
            raise ValueError(f"Execution provider already registered: {provider.provider_id}")
        self.providers[provider.provider_id] = provider

    def validate(self, provider_id: str, instrument: MarketInstrument, order: NormalizedOrder) -> dict[str, Any]:
        provider = self.providers.get(provider_id)
        if provider is None:
            raise ValueError(f"No execution adapter configured for {provider_id}")
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

    def submit(self, provider_id: str, instrument: MarketInstrument, order: NormalizedOrder, client_order_id: str):
        provider = self.providers[provider_id]
        validation = self.validate(provider_id, instrument, order)
        if not validation.get("accepted"):
            raise ValueError(validation.get("reason", "Provider rejected order validation"))
        if not client_order_id:
            raise ValueError("A stable client_order_id is mandatory")
        return provider.submit_order(order.__dict__, client_order_id)
