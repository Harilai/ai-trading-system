import ccxt
from datetime import datetime, timezone

class MarketDataError(RuntimeError): pass

class CcxtMarketData:
    """Public data adapter only; it never sends private exchange orders."""
    def __init__(self, exchange_id: str = "kraken"):
        cls = getattr(ccxt, exchange_id, None)
        if cls is None: raise MarketDataError(f"Unsupported exchange: {exchange_id}")
        self.exchange = cls({"enableRateLimit": True, "timeout": 10000})

    def ticker(self, symbol: str) -> dict:
        try: data = self.exchange.fetch_ticker(symbol)
        except Exception as exc: raise MarketDataError(str(exc)) from exc
        if data.get("timestamp") is None: raise MarketDataError("Ticker has no timestamp")
        return {"symbol": symbol, "last": data.get("last"), "bid": data.get("bid"),
                "ask": data.get("ask"), "timestamp": int(data["timestamp"])}

    def ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> list:
        try: return self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        except Exception as exc: raise MarketDataError(str(exc)) from exc

    @staticmethod
    def is_stale(ticker: dict, max_age_seconds: int) -> bool:
        age = datetime.now(timezone.utc).timestamp()*1000 - ticker["timestamp"]
        return age > max_age_seconds*1000
