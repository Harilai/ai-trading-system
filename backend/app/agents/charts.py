from collections.abc import Sequence

from ..indicators import atr, bollinger_bands, ema, macd, rsi, sma
from .contracts import MarketAnalysis


class ChartsAgent:
    """Deterministic technical-analysis component; it does not generate trade orders."""

    def analyze(
        self,
        symbol: str,
        timeframe: str,
        candles: Sequence[dict[str, float | int]],
    ) -> MarketAnalysis:
        if len(candles) < 50:
            raise ValueError("At least 50 candles are required for the initial analysis")
        closes = [float(candle["close"]) for candle in candles]
        bars = [
            {
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
            }
            for candle in candles
        ]
        timestamp = int(candles[-1]["timestamp"])
        return MarketAnalysis(
            symbol=symbol.upper(),
            timeframe=timeframe,
            as_of_ms=timestamp,
            close=closes[-1],
            indicators={
                "ema_12": ema(closes, 12),
                "ema_20": ema(closes, 20),
                "ema_26": ema(closes, 26),
                "ema_50": ema(closes, 50),
                "sma_20": sma(closes, 20),
                "rsi_14": rsi(closes, 14),
                "macd": macd(closes)["macd"],
                "macd_signal": macd(closes)["signal"],
                "macd_histogram": macd(closes)["histogram"],
                "bollinger_lower": bollinger_bands(closes, 20)["lower"],
                "bollinger_middle": bollinger_bands(closes, 20)["middle"],
                "bollinger_upper": bollinger_bands(closes, 20)["upper"],
                "atr_14": atr(bars, 14),
            },
        )
