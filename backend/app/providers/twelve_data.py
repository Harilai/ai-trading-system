from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..market_data import MarketDataError
from .base import AssetClass, MarketInstrument, MarketType, ProviderCapabilities, VenueType


class TwelveDataProvider:
    """Optional Twelve Data public market-data adapter for US equities and ETFs.

    Basic-plan scope is deliberately restricted to US listings. It is a data-only
    adapter: no account, order, position, withdrawal or signing capabilities.
    """

    BASE_URL = "https://api.twelvedata.com"

    def __init__(self, api_key: str, *, opener: Callable = urlopen, timeout: int = 15):
        if not api_key or not api_key.strip():
            raise ValueError("Twelve Data API key is required")
        self.provider_id = "twelve_data"
        self._api_key = api_key.strip()
        self._opener = opener
        self.timeout = timeout

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.provider_id,
            venue_type=VenueType.DATA,
            asset_classes=(AssetClass.EQUITY, AssetClass.ETF),
            market_types=(MarketType.CASH_EQUITY,),
            public_market_data=True,
            requires_authentication_for_market_data=True,
            requires_authentication_for_trading=True,
            connectivity_status="not_checked",
            rate_limit_ms=7500,
            geographic_restrictions=("Initial adapter restricted to United States listings",),
            account_eligibility=("Twelve Data API key required; free Basic plan has usage quotas",),
            authenticated_account=False,
            balances=False,
            positions=False,
            order_management=False,
            supports_market_orders=False,
            supports_limit_orders=False,
            supports_stop_orders=False,
            supports_client_order_id=False,
            supports_partial_fills=False,
            supports_cancel=False,
            supports_reconciliation=False,
            supports_withdrawals=False,
            supports_wallet_signing=False,
            restrictions=(
                "Market data only; no brokerage account or order execution",
                "Initial adapter restricted to United States equities and ETFs",
                "Free Basic plan quota is 8 API credits per minute and 800 per day; endpoint weights apply",
                "International symbols are not included in this initial adapter",
                "Quotes may not contain bid/ask; absence is preserved, never replaced with fabricated values",
            ),
        )

    def _request(self, endpoint: str, params: dict | None = None) -> dict:
        query = dict(params or {})
        query["apikey"] = self._api_key
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}?{urlencode(query)}"
        try:
            response = self._opener(url, timeout=self.timeout)
            payload = response.read()
            data = json.loads(payload.decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            raise MarketDataError(f"Twelve Data request failed for {endpoint}: {type(exc).__name__}") from exc
        if not isinstance(data, dict):
            raise MarketDataError(f"Twelve Data returned an invalid response for {endpoint}")
        if data.get("status") == "error" or data.get("code") not in (None, 200):
            message = str(data.get("message") or data.get("status") or "provider error")
            raise MarketDataError(f"Twelve Data {endpoint} error: {message[:240]}")
        return data

    @staticmethod
    def _timestamp_ms(value, exchange_timezone: str | None = None) -> int:
        if isinstance(value, (int, float)):
            numeric = float(value)
            return int(numeric * 1000 if numeric < 100_000_000_000 else numeric)
        if not isinstance(value, str) or not value.strip():
            raise MarketDataError("Twelve Data response is missing a usable timestamp")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                if not exchange_timezone:
                    raise MarketDataError("Twelve Data timestamp has no timezone metadata")
                parsed = parsed.replace(tzinfo=ZoneInfo(exchange_timezone))
            return int(parsed.timestamp() * 1000)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise MarketDataError("Twelve Data returned an invalid timestamp or exchange timezone") from exc

    def discover_markets(self, asset_classes: tuple[AssetClass, ...] | None = None) -> list[MarketInstrument]:
        requested = set(asset_classes or (AssetClass.EQUITY, AssetClass.ETF))
        discovered: list[MarketInstrument] = []
        if AssetClass.EQUITY in requested:
            stocks = self._request("stocks", {"country": "United States", "type": "Common Stock"})
            for item in stocks.get("data", []):
                symbol = str(item.get("symbol") or "").strip().upper()
                if not symbol:
                    continue
                discovered.append(MarketInstrument(
                    provider_id=self.provider_id,
                    symbol=symbol,
                    asset_class=AssetClass.EQUITY,
                    market_type=MarketType.CASH_EQUITY,
                    base=symbol,
                    quote=str(item.get("currency") or "USD").upper(),
                    active=True,
                    metadata={
                        "name": item.get("name"),
                        "exchange": item.get("exchange"),
                        "country": item.get("country"),
                        "instrument_type": item.get("type"),
                        "data_scope": "US",
                    },
                ))
        if AssetClass.ETF in requested:
            funds = self._request("etfs/list", {"country": "United States", "outputsize": 50, "page": 1})
            result = funds.get("result") or {}
            for item in result.get("list", []):
                symbol = str(item.get("symbol") or "").strip().upper()
                if not symbol:
                    continue
                discovered.append(MarketInstrument(
                    provider_id=self.provider_id,
                    symbol=symbol,
                    asset_class=AssetClass.ETF,
                    market_type=MarketType.CASH_EQUITY,
                    base=symbol,
                    quote=str(item.get("currency") or "USD").upper(),
                    active=True,
                    metadata={
                        "name": item.get("name"),
                        "exchange": item.get("exchange") or item.get("mic_code"),
                        "country": item.get("country") or "United States",
                        "fund_family": item.get("fund_family"),
                        "fund_type": item.get("fund_type"),
                        "data_scope": "US",
                    },
                ))
        return discovered

    def ticker(self, symbol: str) -> dict:
        data = self._request("quote", {"symbol": symbol})
        last_value = data.get("close") or data.get("price")
        if last_value is None:
            raise MarketDataError(f"Twelve Data quote for {symbol} has no last/close price")
        timestamp = self._timestamp_ms(
            data.get("timestamp") or data.get("datetime"),
            data.get("exchange_timezone"),
        )
        volume = data.get("volume")
        average_volume = data.get("average_volume")
        return {
            "symbol": str(data.get("symbol") or symbol).upper(),
            "last": float(last_value),
            "bid": float(data["bid"]) if data.get("bid") is not None else None,
            "ask": float(data["ask"]) if data.get("ask") is not None else None,
            "timestamp": timestamp,
            "quoteVolume": None,
            "baseVolume": float(volume) if volume is not None else None,
            "averageVolume": float(average_volume) if average_volume is not None else None,
            "provider_id": self.provider_id,
            "exchange": data.get("exchange"),
            "currency": data.get("currency"),
            "is_market_open": data.get("is_market_open"),
        }

    def ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> list:
        intervals = {
            "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
            "45m": "45min", "1h": "1h", "2h": "2h", "4h": "4h",
            "8h": "8h", "1d": "1day", "1w": "1week", "1M": "1month",
        }
        if timeframe not in intervals:
            raise MarketDataError(f"Unsupported Twelve Data interval: {timeframe}")
        if not 1 <= limit <= 5000:
            raise MarketDataError("Twelve Data candle limit must be between 1 and 5000")
        data = self._request("time_series", {
            "symbol": symbol,
            "interval": intervals[timeframe],
            "outputsize": limit,
            "order": "ASC",
        })
        meta = data.get("meta") or {}
        exchange_timezone = meta.get("exchange_timezone")
        values = data.get("values") or []
        rows = []
        for item in values:
            timestamp = self._timestamp_ms(item.get("datetime"), exchange_timezone)
            try:
                rows.append([
                    timestamp,
                    float(item["open"]),
                    float(item["high"]),
                    float(item["low"]),
                    float(item["close"]),
                    float(item.get("volume") or 0),
                ])
            except (KeyError, TypeError, ValueError) as exc:
                raise MarketDataError("Twelve Data returned malformed OHLCV values") from exc
        rows.sort(key=lambda row: row[0])
        return rows
