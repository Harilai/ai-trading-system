from __future__ import annotations

from typing import Any

from .agents.charts import ChartsAgent
from .agents.contracts import MarketAnalysis


class ScoutAgent:
    """Discovers liquid EUR-quoted spot markets and delegates deterministic chart analysis."""

    def __init__(
        self,
        market_data: Any,
        charts: ChartsAgent | None = None,
        *,
        max_symbols: int = 5,
        timeframe: str = "1h",
        candle_limit: int = 100,
    ):
        if max_symbols < 1:
            raise ValueError("max_symbols must be positive")
        self.market_data = market_data
        self.charts = charts or ChartsAgent()
        self.max_symbols = max_symbols
        self.timeframe = timeframe
        self.candle_limit = candle_limit

    def scan(self) -> dict[str, Any]:
        candidates = self.market_data.discover_spot_symbols(
            quote="EUR",
            limit=self.max_symbols,
        )
        analyses: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []

        for candidate in candidates:
            symbol = candidate["symbol"]
            try:
                rows = self.market_data.ohlcv(
                    symbol,
                    timeframe=self.timeframe,
                    limit=self.candle_limit,
                )
                candles = [
                    {
                        "timestamp": row[0],
                        "open": row[1],
                        "high": row[2],
                        "low": row[3],
                        "close": row[4],
                        "volume": row[5],
                    }
                    for row in rows
                ]
                analysis: MarketAnalysis = self.charts.analyze(
                    symbol,
                    self.timeframe,
                    candles,
                )
                analyses.append(
                    {
                        "market": candidate,
                        "analysis": analysis.model_dump(),
                    }
                )
            except (ValueError, KeyError, TypeError) as exc:
                rejected.append({"symbol": symbol, "reason": str(exc)})

        return {
            "universe": "active_spot_EUR",
            "timeframe": self.timeframe,
            "discovered": len(candidates),
            "analysed": len(analyses),
            "analyses": analyses,
            "rejected": rejected,
            "execution": "analysis_only",
        }
