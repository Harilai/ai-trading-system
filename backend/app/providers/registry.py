from __future__ import annotations

from threading import RLock
from typing import Iterable

from datetime import datetime, timezone

from ..market_data import MarketDataError

from .base import (
    AssetClass,
    MarketDataProvider,
    MarketInstrument,
    ProviderCapabilities,
)


class ProviderRegistryError(MarketDataError):
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

    def discover_spot_symbols(
        self,
        quote: str = "EUR",
        limit: int = 5,
        max_spread_fraction: float = 0.02,
        min_quote_volume: float = 1000.0,
    ) -> list[dict]:
        if limit < 1:
            raise ValueError("limit must be positive")
        ranked = []
        with self._lock:
            providers = list(self._providers.values())
        for provider in providers:
            discover = getattr(provider, "discover_spot_symbols", None)
            if discover is None:
                continue
            try:
                ranked.extend(discover(quote, limit, max_spread_fraction, min_quote_volume))
            except Exception:
                # Provider-specific failures are reported by discover_markets and provider health.
                continue
        ranked.sort(key=lambda item: item.get("quote_volume_24h", 0), reverse=True)
        return ranked[:limit]

    def ticker(self, symbol: str, provider_id: str | None = None) -> dict:
        if provider_id:
            return self.get(provider_id).ticker(symbol)
        with self._lock:
            providers = list(self._providers.values())
        failures = []
        for provider in providers:
            try:
                return provider.ticker(symbol)
            except Exception as exc:
                failures.append(f"{provider.provider_id}: {exc}")
        raise ProviderRegistryError(f"No configured provider supplied {symbol}: {'; '.join(failures)}")

    def ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100, provider_id: str | None = None) -> list:
        if provider_id:
            return self.get(provider_id).ohlcv(symbol, timeframe, limit)
        with self._lock:
            providers = list(self._providers.values())
        failures = []
        for provider in providers:
            try:
                return provider.ohlcv(symbol, timeframe, limit)
            except Exception as exc:
                failures.append(f"{provider.provider_id}: {exc}")
        raise ProviderRegistryError(f"No configured provider supplied OHLCV for {symbol}: {'; '.join(failures)}")

    @staticmethod
    def is_stale(ticker: dict, max_age_seconds: int) -> bool:
        age = datetime.now(timezone.utc).timestamp() * 1000 - ticker["timestamp"]
        return age > max_age_seconds * 1000

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
