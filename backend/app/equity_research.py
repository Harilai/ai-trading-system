from __future__ import annotations

from typing import Any
import time

from .agents.charts import ChartsAgent
from .market_data import MarketDataError
from .strategy_research import StrategyResearchAgent
from .providers.base import AssetClass


class EquityEtfResearchAgent:
    """Research-only pipeline for US stocks and ETFs from a data provider.

    It never emits TradeProposal objects or submits orders. A missing bid/ask,
    stale quote, non-USD listing, or insufficient history prevents a candidate
    from being recommended for later execution.
    """

    def __init__(
        self,
        provider: Any,
        *,
        charts: ChartsAgent | None = None,
        strategy_research: StrategyResearchAgent | None = None,
        max_instruments: int = 5,
        timeframe: str = "1day",
        candle_limit: int = 250,
        max_quote_age_seconds: int = 24 * 60 * 60,
        require_two_sided_quote: bool = True,
    ):
        if max_instruments < 1:
            raise ValueError("max_instruments must be positive")
        if timeframe not in {"1day", "1h", "4h"}:
            raise ValueError("Equity research timeframe must be 1day, 1h or 4h")
        if not 120 <= candle_limit <= 5000:
            raise ValueError("Equity research requires between 120 and 5000 candles")
        if max_quote_age_seconds < 1:
            raise ValueError("max_quote_age_seconds must be positive")
        self.provider = provider
        self.charts = charts or ChartsAgent()
        self.strategy_research = strategy_research or StrategyResearchAgent()
        self.max_instruments = max_instruments
        self.timeframe = timeframe
        self.candle_limit = candle_limit
        self.max_quote_age_seconds = max_quote_age_seconds
        self.require_two_sided_quote = require_two_sided_quote

    def scan(self, asset_classes: tuple[AssetClass, ...] = (AssetClass.EQUITY, AssetClass.ETF)) -> dict:
        instruments = self.provider.discover_markets(asset_classes)
        analyses: list[dict] = []
        rejected: list[dict[str, str]] = []
        seen: set[str] = set()
        considered = 0

        for instrument in instruments:
            if considered >= self.max_instruments:
                break
            if instrument.asset_class not in asset_classes:
                continue
            if instrument.quote != "USD":
                rejected.append({"symbol": instrument.symbol, "reason": "Only USD listings are enabled"})
                continue
            if instrument.instrument_id in seen:
                continue
            seen.add(instrument.instrument_id)
            considered += 1
            try:
                quote = self.provider.ticker(instrument.symbol)
                if quote.get("last") is None or float(quote["last"]) <= 0:
                    raise MarketDataError("Quote has no valid last price")
                age_ms = max(0, time.time_ns() // 1_000_000 - int(quote["timestamp"]))
                if age_ms > self.max_quote_age_seconds * 1000:
                    raise MarketDataError("Quote is stale")
                bid = quote.get("bid")
                ask = quote.get("ask")
                if self.require_two_sided_quote and (bid is None or ask is None):
                    raise MarketDataError("Two-sided bid/ask quote required before execution eligibility")
                if bid is not None and ask is not None:
                    bid, ask = float(bid), float(ask)
                    if bid <= 0 or ask < bid:
                        raise MarketDataError("Invalid bid/ask spread")
                    spread_fraction = (ask - bid) / ((ask + bid) / 2)
                else:
                    spread_fraction = None

                rows = self.provider.ohlcv(
                    instrument.symbol,
                    timeframe=self.timeframe,
                    limit=self.candle_limit,
                )
                candles = [
                    {
                        "timestamp": row[0], "open": row[1], "high": row[2],
                        "low": row[3], "close": row[4], "volume": row[5],
                    }
                    for row in rows
                ]
                chart = self.charts.analyze(instrument.symbol, self.timeframe, candles)
                research = self.strategy_research.research(candles)
                analyses.append({
                    "instrument": {
                        "instrument_id": instrument.instrument_id,
                        "provider_id": instrument.provider_id,
                        "symbol": instrument.symbol,
                        "asset_class": instrument.asset_class.value,
                        "market_type": instrument.market_type.value,
                        "quote_currency": instrument.quote,
                        "metadata": instrument.metadata,
                    },
                    "quote": {
                        "last": float(quote["last"]),
                        "bid": bid,
                        "ask": ask,
                        "spread_fraction": spread_fraction,
                        "timestamp_ms": int(quote["timestamp"]),
                        "is_market_open": quote.get("is_market_open"),
                    },
                    "chart_analysis": chart.model_dump(),
                    "strategy_research": research,
                    "execution_eligible": False,
                    "execution_blockers": [
                        "No equity/ETF broker adapter is configured",
                        "No asset-specific equity/ETF Risk Manager approval path exists",
                        "This research result is not a trade instruction",
                    ],
                })
            except (MarketDataError, ValueError, TypeError, KeyError) as exc:
                rejected.append({"symbol": instrument.symbol, "reason": str(exc)})

        return {
            "universe": "twelve_data_us_equities_and_etfs",
            "provider_id": self.provider.provider_id,
            "timeframe": self.timeframe,
            "discovered": len(instruments),
            "considered": considered,
            "analysed": len(analyses),
            "analyses": analyses,
            "rejected": rejected,
            "execution": "disabled_research_only",
        }
