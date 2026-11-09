from __future__ import annotations

from datetime import datetime, timezone

import ccxt

from .base import AssetClass, MarketInstrument, MarketType, ProviderCapabilities, VenueType
from ..market_data import MarketDataError


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
                    min_amount=(market.get("limits", {}).get("amount", {}).get("min")),
                    min_cost=(market.get("limits", {}).get("cost", {}).get("min")),
                    maker_fee=market.get("maker"),
                    taker_fee=market.get("taker"),
                    metadata={
                        "id": market.get("id"),
                        "precision": market.get("precision", {}),
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
            ticker = tickers.get(symbol) or {}
            timestamp = ticker.get("timestamp")
            last, bid, ask = ticker.get("last"), ticker.get("bid"), ticker.get("ask")
            if timestamp is None or last is None or bid is None or ask is None:
                continue
            if now_ms - int(timestamp) > 120_000 or int(timestamp) > now_ms + 30_000:
                continue
            if min(float(last), float(bid), float(ask)) <= 0 or ask < bid:
                continue
            midpoint = (float(bid) + float(ask)) / 2
            spread = (float(ask) - float(bid)) / midpoint
            volume = ticker.get("quoteVolume")
            if volume is None and ticker.get("baseVolume") is not None:
                volume = float(ticker["baseVolume"]) * float(last)
            if volume is None or float(volume) < min_quote_volume or spread > max_spread_fraction:
                continue
            ranked.append({
                "symbol": symbol, "last": float(last), "bid": float(bid), "ask": float(ask),
                "spread_fraction": spread, "quote_volume_24h": float(volume),
                "ticker_timestamp_ms": int(timestamp), "provider_id": self.provider_id,
                "asset_class": AssetClass.CRYPTO.value, "market_type": MarketType.SPOT.value,
            })
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
