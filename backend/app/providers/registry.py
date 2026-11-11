from __future__ import annotations

import math
from datetime import datetime, timezone
from threading import RLock
from typing import Iterable

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
        self.last_discovery_failures: list[dict[str, str]] = []

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
        requested_classes = set(asset_classes or ())
        for provider in providers:
            try:
                caps = provider.capabilities()
                if caps.provider_id != provider.provider_id:
                    failures.append({
                        "provider_id": provider.provider_id,
                        "reason": "Provider identity does not match its capability declaration",
                    })
                    continue
                if requested_classes and not requested_classes.intersection(caps.asset_classes):
                    continue
                provider_instruments = provider.discover_markets(asset_classes)
                if not isinstance(provider_instruments, (list, tuple)):
                    failures.append({
                        "provider_id": provider.provider_id,
                        "reason": "Discovery returned an invalid collection type",
                    })
                    continue
                for instrument in provider_instruments:
                    if not isinstance(instrument, MarketInstrument):
                        failures.append({
                            "provider_id": provider.provider_id,
                            "reason": "Discovery returned a record that is not a MarketInstrument",
                        })
                        continue
                    if instrument.provider_id != provider.provider_id:
                        failures.append({
                            "provider_id": provider.provider_id,
                            "reason": "Discovered instrument provider identity does not match its adapter",
                        })
                        continue
                    if instrument.asset_class not in caps.asset_classes:
                        failures.append({
                            "provider_id": provider.provider_id,
                            "reason": "Discovered asset class is not declared by provider capabilities",
                        })
                        continue
                    if instrument.market_type not in caps.market_types:
                        failures.append({
                            "provider_id": provider.provider_id,
                            "reason": "Discovered market type is not declared by provider capabilities",
                        })
                        continue
                    if requested_classes and instrument.asset_class not in requested_classes:
                        failures.append({
                            "provider_id": provider.provider_id,
                            "reason": "Provider returned an asset class outside the requested filter",
                        })
                        continue
                    discovered.append(instrument)
            except Exception as exc:
                # Provider exception text may contain URLs, credentials or request headers.
                failures.append({
                    "provider_id": provider.provider_id,
                    "reason": f"Discovery failed ({type(exc).__name__})",
                })
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
        failures: list[dict[str, str]] = []
        with self._lock:
            providers = list(self._providers.values())
        for provider in providers:
            discover = getattr(provider, "discover_spot_symbols", None)
            if discover is None:
                continue
            try:
                ranked.extend(discover(quote, limit, max_spread_fraction, min_quote_volume))
            except Exception as exc:
                failures.append({"provider_id": provider.provider_id, "reason": str(exc)})
                continue
        self.last_discovery_failures = failures
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
        try:
            timestamp = float(ticker["timestamp"])
            max_age_seconds = float(max_age_seconds)
        except (KeyError, TypeError, ValueError, OverflowError):
            return True
        if not math.isfinite(timestamp) or timestamp <= 0 or not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
            return True
        age_ms = datetime.now(timezone.utc).timestamp() * 1000 - timestamp
        return age_ms < 0 or age_ms > max_age_seconds * 1000

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
                    "requires_authentication_for_market_data": item.requires_authentication_for_market_data,
                    "requires_authentication_for_trading": item.requires_authentication_for_trading,
                    "connectivity_status": item.connectivity_status,
                    "rate_limit_ms": item.rate_limit_ms,
                    "geographic_restrictions": list(item.geographic_restrictions),
                    "account_eligibility": list(item.account_eligibility),
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
