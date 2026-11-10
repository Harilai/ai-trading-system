from __future__ import annotations

from datetime import datetime, timezone

import ccxt

from .base import AssetClass, MarketInstrument, MarketType, PrecisionMode, ProviderCapabilities, VenueType
from ..market_data import MarketDataError, _normalize_discovered_spot_ticker


class CcxtSpotProvider:
    """Public CEX spot-data adapter. Private trading is deliberately not enabled."""

    def __init__(self, exchange_id: str = "kraken"):
        cls = getattr(ccxt, exchange_id, None)
        if cls is None:
            raise MarketDataError(f"Unsupported CCXT exchange: {exchange_id}")
        self.provider_id = f"ccxt:{exchange_id}"
        self.exchange_id = exchange_id
        self.exchange = cls({"enableRateLimit": True, "timeout": 10000})
        self._markets = None

    def _load_markets(self):
        if self._markets is None:
            try:
                self._markets = self.exchange.load_markets()
            except Exception as exc:
                raise MarketDataError(f"Unable to load markets from {self.provider_id}: {exc}") from exc
        return self._markets

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.provider_id,
            venue_type=VenueType.CEX,
            asset_classes=(AssetClass.CRYPTO,),
            market_types=(MarketType.SPOT,),
            public_market_data=True,
            requires_authentication_for_market_data=False,
            requires_authentication_for_trading=True,
            connectivity_status="not_checked",
            rate_limit_ms=int(getattr(self.exchange, "rateLimit", 0) or 0),
            authenticated_account=False,
            balances=False,
            positions=False,
            order_management=False,
            restrictions=(
                "Public market data only; private account endpoints are not configured",
                "No order submission, cancellation, balances or withdrawals",
                "Initial implementation supports active crypto spot markets quoted in EUR",
            ),
        )

    @staticmethod
    def _optional_float(value):
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return parsed if parsed == parsed and abs(parsed) != float("inf") else None

    @staticmethod
    def _optional_precision(value):
        if isinstance(value, bool):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not parsed.is_integer() or parsed < 0:
            return None
        return int(parsed)

    def _precision_fields(self, market: dict) -> dict:
        raw = market.get("precision") or {}
        mode_value = getattr(self.exchange, "precisionMode", None)
        if mode_value == getattr(ccxt, "TICK_SIZE", object()):
            mode = PrecisionMode.TICK_SIZE
        elif mode_value == getattr(ccxt, "DECIMAL_PLACES", object()):
            mode = PrecisionMode.DECIMAL_PLACES
        elif mode_value == getattr(ccxt, "SIGNIFICANT_DIGITS", object()):
            mode = PrecisionMode.SIGNIFICANT_DIGITS
        else:
            mode = PrecisionMode.UNKNOWN

        fields = {"precision_mode": mode}
        if mode == PrecisionMode.TICK_SIZE:
            fields["price_tick"] = self._optional_float(raw.get("price"))
            fields["amount_step"] = self._optional_float(raw.get("amount"))
        elif mode in {PrecisionMode.DECIMAL_PLACES, PrecisionMode.SIGNIFICANT_DIGITS}:
            fields["price_precision"] = self._optional_precision(raw.get("price"))
            fields["amount_precision"] = self._optional_precision(raw.get("amount"))
        return fields

    @staticmethod
    def _expiry_iso(market: dict) -> str | None:
        value = market.get("expiryDatetime")
        if isinstance(value, str) and value.strip():
            return value.strip()
        value = market.get("expiry")
        if value is None:
            return None
        try:
            return datetime.fromtimestamp(float(value) / 1000, timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    def discover_markets(self, asset_classes=None) -> list[MarketInstrument]:
        if asset_classes and AssetClass.CRYPTO not in asset_classes:
            return []
        result = []
        for symbol, market in self._load_markets().items():
            if not market.get("spot") or market.get("active") is False:
                continue
            if market.get("quote") != "EUR":
                continue
            result.append(
                MarketInstrument(
                    provider_id=self.provider_id,
                    symbol=symbol,
                    asset_class=AssetClass.CRYPTO,
                    market_type=MarketType.SPOT,
                    base=str(market.get("base", "")),
                    quote=str(market.get("quote", "")),
                    active=market.get("active") is not False,
                    min_amount=self._optional_float(market.get("limits", {}).get("amount", {}).get("min")),
                    max_amount=self._optional_float(market.get("limits", {}).get("amount", {}).get("max")),
                    min_cost=self._optional_float(market.get("limits", {}).get("cost", {}).get("min")),
                    max_cost=self._optional_float(market.get("limits", {}).get("cost", {}).get("max")),
                    maker_fee=self._optional_float(market.get("maker")),
                    taker_fee=self._optional_float(market.get("taker")),
                    venue_id=self.exchange_id,
                    native_symbol=symbol,
                    **self._precision_fields(market),
                    contract_type=(
                        "option" if market.get("option") else
                        "perpetual" if market.get("swap") else
                        "future" if market.get("future") else
                        "spot"
                    ),
                    contract_size=self._optional_float(market.get("contractSize")),
                    settlement_currency=market.get("settle"),
                    expiry=self._expiry_iso(market),
                    strike=self._optional_float(market.get("strike")),
                    option_type=market.get("optionType"),
                    underlying=market.get("underlying"),
                    trading_status="active" if market.get("active") is not False else "inactive",
                    metadata={
                        "id": market.get("id"),
                        "precision": market.get("precision", {}),
                        "precision_mode": str(getattr(self.exchange, "precisionMode", "unknown")),
                        "contract": bool(market.get("contract", False)),
                    },
                )
            )
        return result

    def ticker(self, symbol: str) -> dict:
        try:
            data = self.exchange.fetch_ticker(symbol)
        except Exception as exc:
            raise MarketDataError(f"{self.provider_id} ticker failed for {symbol}: {exc}") from exc
        if data.get("timestamp") is None:
            raise MarketDataError("Ticker has no timestamp")
        return {
            "symbol": symbol,
            "last": data.get("last"),
            "bid": data.get("bid"),
            "ask": data.get("ask"),
            "timestamp": int(data["timestamp"]),
            "quoteVolume": data.get("quoteVolume"),
            "baseVolume": data.get("baseVolume"),
        }

    def discover_spot_symbols(
        self,
        quote: str = "EUR",
        limit: int = 5,
        max_spread_fraction: float = 0.02,
        min_quote_volume: float = 1000.0,
    ) -> list[dict]:
        if quote != "EUR":
            raise ValueError("Initial CCXT adapter is restricted to EUR-quoted spot markets")
        if limit < 1 or not 0 <= max_spread_fraction < 1 or min_quote_volume < 0:
            raise ValueError("Invalid market discovery limits")
        markets = self._load_markets()
        symbols = [
            symbol for symbol, market in markets.items()
            if market.get("spot") and market.get("active") is not False and market.get("quote") == quote
        ]
        try:
            if self.exchange.has.get("fetchTickers"):
                tickers = self.exchange.fetch_tickers(symbols)
            else:
                tickers = {}
                for symbol in symbols:
                    try:
                        tickers[symbol] = self.exchange.fetch_ticker(symbol)
                    except Exception:
                        continue
        except Exception as exc:
            raise MarketDataError(f"{self.provider_id} market discovery failed: {exc}") from exc

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        ranked = []
        for symbol in symbols:
            row = _normalize_discovered_spot_ticker(
                symbol,
                tickers.get(symbol),
                provider_id=self.provider_id,
                now_ms=now_ms,
                max_spread_fraction=max_spread_fraction,
                min_quote_volume=min_quote_volume,
            )
            if row is not None:
                row.update({
                    "asset_class": AssetClass.CRYPTO.value,
                    "market_type": MarketType.SPOT.value,
                })
                ranked.append(row)
        return sorted(ranked, key=lambda item: item["quote_volume_24h"], reverse=True)[:limit]

    def ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> list:
        try:
            return self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        except Exception as exc:
            raise MarketDataError(f"{self.provider_id} OHLCV failed for {symbol}: {exc}") from exc

    @staticmethod
    def is_stale(ticker: dict, max_age_seconds: int) -> bool:
        age = datetime.now(timezone.utc).timestamp() * 1000 - ticker["timestamp"]
        return age > max_age_seconds * 1000
