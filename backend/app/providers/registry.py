from __future__ import annotations

from threading import RLock
from typing import Iterable

from .base import (
    AssetClass,
    MarketDataProvider,
    MarketInstrument,
    ProviderCapabilities,
)


class ProviderRegistryError(RuntimeError):
    pass


class ProviderRegistry:
    """Explicit registry of validated adapters; it never guesses provider access."""

    def __init__(self):
        self._providers: dict[str, MarketDataProvider] = {}
        self._lock = RLock()

    def register(self, provider: MarketDataProvider) -> None:
        capabilities = provider.capabilities()
        if not capabilities.provider_id or capabilities.provider_id != provider.provider_id:
            raise ProviderRegistryError("Provider identity does not match its capability declaration")
        with self._lock:
            if provider.provider_id in self._providers:
                raise ProviderRegistryError(f"Provider already registered: {provider.provider_id}")
            self._providers[provider.provider_id] = provider

    def get(self, provider_id: str) -> MarketDataProvider:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise ProviderRegistryError(f"Provider is not configured: {provider_id}") from exc

    def providers(self) -> list[str]:
        with self._lock:
            return sorted(self._providers)

    def capability_matrix(self) -> list[ProviderCapabilities]:
        with self._lock:
            providers = list(self._providers.values())
        return [provider.capabilities() for provider in providers]

    def discover_markets(
        self,
        asset_classes: tuple[AssetClass, ...] | None = None,
    ) -> tuple[list[MarketInstrument], list[dict[str, str]]]:
        discovered: list[MarketInstrument] = []
        failures: list[dict[str, str]] = []
        with self._lock:
            providers = list(self._providers.values())
        for provider in providers:
            caps = provider.capabilities()
            if asset_classes and not set(asset_classes).intersection(caps.asset_classes):
                continue
            try:
                discovered.extend(provider.discover_markets(asset_classes))
            except Exception as exc:
                failures.append({"provider_id": provider.provider_id, "reason": str(exc)})
        return discovered, failures

    def describe(self) -> dict:
        return {
            "providers": self.providers(),
            "capabilities": [
                {
                    "provider_id": item.provider_id,
                    "venue_type": item.venue_type.value,
                    "asset_classes": [x.value for x in item.asset_classes],
                    "market_types": [x.value for x in item.market_types],
                    "public_market_data": item.public_market_data,
                    "authenticated_account": item.authenticated_account,
                    "balances": item.balances,
                    "positions": item.positions,
                    "order_management": item.order_management,
                    "supports_market_orders": item.supports_market_orders,
                    "supports_limit_orders": item.supports_limit_orders,
                    "supports_stop_orders": item.supports_stop_orders,
                    "supports_client_order_id": item.supports_client_order_id,
                    "supports_partial_fills": item.supports_partial_fills,
                    "supports_cancel": item.supports_cancel,
                    "supports_reconciliation": item.supports_reconciliation,
                    "supports_withdrawals": item.supports_withdrawals,
                    "supports_wallet_signing": item.supports_wallet_signing,
                    "supported_symbols": list(item.supported_symbols),
                    "restrictions": list(item.restrictions),
                }
                for item in self.capability_matrix()
            ],
        }
