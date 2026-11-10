import ccxt
import math
from datetime import datetime, timezone


class MarketDataError(RuntimeError):
    pass


def _normalize_discovered_spot_ticker(
    symbol: str,
    ticker: dict,
    *,
    provider_id: str | None = None,
    now_ms: int,
    max_spread_fraction: float,
    min_quote_volume: float,
) -> dict | None:
    """Return a validated discovery row, skipping malformed provider payloads."""
    if not isinstance(ticker, dict):
        return None
    try:
        timestamp = int(ticker["timestamp"])
        last = float(ticker["last"])
        bid = float(ticker["bid"])
        ask = float(ticker["ask"])
        if ticker.get("quoteVolume") is not None:
            quote_volume = float(ticker["quoteVolume"])
        elif ticker.get("baseVolume") is not None:
            quote_volume = float(ticker["baseVolume"]) * last
        else:
            return None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None

    numeric = (last, bid, ask, quote_volume)
    if not all(math.isfinite(value) for value in numeric):
        return None
    if timestamp <= 0 or now_ms - timestamp > 120_000 or timestamp > now_ms + 30_000:
        return None
    if min(last, bid, ask) <= 0 or quote_volume < min_quote_volume or ask < bid:
        return None
    midpoint = (bid + ask) / 2
    if midpoint <= 0:
        return None
    spread = (ask - bid) / midpoint
    if not math.isfinite(spread) or spread > max_spread_fraction:
        return None

    result = {
        "symbol": symbol,
        "last": last,
        "bid": bid,
        "ask": ask,
        "spread_fraction": spread,
        "quote_volume_24h": quote_volume,
        "ticker_timestamp_ms": timestamp,
    }
    if provider_id:
        result.update({
            "provider_id": provider_id,
            "instrument_id": f"{provider_id}:spot:{symbol}",
        })
    return result


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
            row = _normalize_discovered_spot_ticker(
                symbol,
                tickers.get(symbol),
                now_ms=now_ms,
                max_spread_fraction=max_spread_fraction,
                min_quote_volume=min_quote_volume,
            )
            if row is not None:
                ranked.append(row)
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
        try:
            timestamp = float(ticker["timestamp"])
            max_age_seconds = float(max_age_seconds)
        except (KeyError, TypeError, ValueError, OverflowError):
            return True
        if not math.isfinite(timestamp) or timestamp <= 0 or not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
            return True
        now_ms = datetime.now(timezone.utc).timestamp() * 1000
        age_ms = now_ms - timestamp
        return age_ms < 0 or age_ms > max_age_seconds * 1000
