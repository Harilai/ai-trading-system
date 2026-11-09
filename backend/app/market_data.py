import ccxt
from datetime import datetime, timezone


class MarketDataError(RuntimeError):
    pass


class CcxtMarketData:
    """Public data adapter only; it never sends private exchange orders."""

    def __init__(self, exchange_id: str = "kraken"):
        cls = getattr(ccxt, exchange_id, None)
        if cls is None:
            raise MarketDataError(f"Unsupported exchange: {exchange_id}")
        self.exchange = cls({"enableRateLimit": True, "timeout": 10000})

    def ticker(self, symbol: str) -> dict:
        try:
            data = self.exchange.fetch_ticker(symbol)
        except Exception as exc:
            raise MarketDataError(str(exc)) from exc
        if data.get("timestamp") is None:
            raise MarketDataError("Ticker has no timestamp")
        return {
            "symbol": symbol,
            "last": data.get("last"),
            "bid": data.get("bid"),
            "ask": data.get("ask"),
            "timestamp": int(data["timestamp"]),
        }

    def discover_spot_symbols(
        self,
        quote: str = "EUR",
        limit: int = 5,
        max_spread_fraction: float = 0.02,
        min_quote_volume: float = 1000.0,
    ) -> list[dict]:
        """Discover active spot markets and rank them by 24h quote volume."""
        if limit < 1 or not 0 <= max_spread_fraction < 1 or min_quote_volume < 0:
            raise ValueError("Invalid market discovery limits")
        try:
            markets = self.exchange.load_markets()
            symbols = [
                symbol
                for symbol, market in markets.items()
                if market.get("spot")
                and market.get("active") is not False
                and market.get("quote") == quote
            ]
            if not symbols:
                return []
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
            raise MarketDataError(f"Market discovery failed: {exc}") from exc

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
            quote_volume = ticker.get("quoteVolume")
            if quote_volume is None and ticker.get("baseVolume") is not None:
                quote_volume = float(ticker["baseVolume"]) * float(last)
            if quote_volume is None or float(quote_volume) < min_quote_volume:
                continue
            if spread > max_spread_fraction:
                continue
            ranked.append(
                {
                    "symbol": symbol,
                    "last": float(last),
                    "bid": float(bid),
                    "ask": float(ask),
                    "spread_fraction": spread,
                    "quote_volume_24h": float(quote_volume),
                    "ticker_timestamp_ms": int(timestamp),
                }
            )
        return sorted(
            ranked,
            key=lambda item: item["quote_volume_24h"],
            reverse=True,
        )[:limit]

    def ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> list:
        try:
            return self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        except Exception as exc:
            raise MarketDataError(str(exc)) from exc

    @staticmethod
    def is_stale(ticker: dict, max_age_seconds: int) -> bool:
        age = datetime.now(timezone.utc).timestamp() * 1000 - ticker["timestamp"]
        return age > max_age_seconds * 1000
