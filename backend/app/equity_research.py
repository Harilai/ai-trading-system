from __future__ import annotations

import math
import time
from typing import Any

from .agents.charts import ChartsAgent
from .market_data import MarketDataError
from .providers.base import AssetClass
from .strategy_research import StrategyResearchAgent


class EquityEtfResearchAgent:
    """Bounded, liquidity-aware research pipeline for US stocks and ETFs.

    Candidate quotes are screened and ranked before expensive historical candle
    requests. Research eligibility remains separate from execution eligibility.
    """

    def __init__(
        self,
        provider: Any,
        *,
        charts: ChartsAgent | None = None,
        strategy_research: StrategyResearchAgent | None = None,
        max_instruments: int = 5,
        max_quote_candidates: int = 10,
        timeframe: str = "1day",
        candle_limit: int = 250,
        max_quote_age_seconds: int = 24 * 60 * 60,
        require_two_sided_quote: bool = False,
        max_execution_spread_fraction: float = 0.02,
    ):
        if max_instruments < 1:
            raise ValueError("max_instruments must be positive")
        if max_quote_candidates < max_instruments or max_quote_candidates > 100:
            raise ValueError("max_quote_candidates must be between max_instruments and 100")
        if timeframe not in {"1day", "1h", "4h"}:
            raise ValueError("Equity research timeframe must be 1day, 1h or 4h")
        if not 120 <= candle_limit <= 5000:
            raise ValueError("Equity research requires between 120 and 5000 candles")
        if max_quote_age_seconds < 1:
            raise ValueError("max_quote_age_seconds must be positive")
        if not 0 < max_execution_spread_fraction <= 1:
            raise ValueError("max_execution_spread_fraction must be between 0 and 1")
        self.provider = provider
        self.charts = charts or ChartsAgent()
        self.strategy_research = strategy_research or StrategyResearchAgent()
        self.max_instruments = max_instruments
        self.max_quote_candidates = max_quote_candidates
        self.timeframe = timeframe
        self.candle_limit = candle_limit
        self.max_quote_age_seconds = max_quote_age_seconds
        self.require_two_sided_quote = require_two_sided_quote
        self.max_execution_spread_fraction = max_execution_spread_fraction

    @staticmethod
    def _quote_quality(quote: dict, now_ms: int, max_age_ms: int) -> tuple[float, dict]:
        last = float(quote.get("last") or 0)
        if last <= 0:
            raise MarketDataError("Quote has no valid last price")
        timestamp_ms = int(quote["timestamp"])
        age_ms = max(0, now_ms - timestamp_ms)
        if age_ms > max_age_ms:
            raise MarketDataError("Quote is stale")

        bid, ask = quote.get("bid"), quote.get("ask")
        spread_fraction = None
        if bid is not None and ask is not None:
            bid, ask = float(bid), float(ask)
            if bid <= 0 or ask <= 0 or ask < bid:
                raise MarketDataError("Invalid bid/ask spread")
            midpoint = (ask + bid) / 2
            spread_fraction = (ask - bid) / midpoint if midpoint > 0 else None

        average_volume = quote.get("averageVolume")
        current_volume = quote.get("baseVolume")
        volume = average_volume if average_volume is not None else current_volume
        volume = float(volume) if volume is not None else None
        if volume is not None and volume < 0:
            raise MarketDataError("Trading volume cannot be negative")

        # Missing volume receives the lowest score but does not discard useful history.
        # Log scaling avoids allowing a few extreme-volume mega caps to dominate.
        volume_score = math.log1p(volume) if volume is not None else 0.0
        freshness_score = max(0.0, 1.0 - age_ms / max_age_ms)
        spread_score = (
            max(0.0, 1.0 - spread_fraction / 0.02)
            if spread_fraction is not None else 0.25
        )
        score = volume_score * 0.60 + freshness_score * 0.25 + spread_score * 0.15
        return score, {
            "timestamp_ms": timestamp_ms,
            "age_seconds": round(age_ms / 1000, 2),
            "average_volume": float(average_volume) if average_volume is not None else None,
            "ranking_volume": volume if volume is not None and volume > 0 else None,
            "spread_fraction": spread_fraction,
            "liquidity_score": round(score, 6),
            "ranking_components": {
                "volume_weight": 0.60,
                "freshness_weight": 0.25,
                "spread_weight": 0.15,
            },
        }

    def scan(self, asset_classes: tuple[AssetClass, ...] = (AssetClass.EQUITY, AssetClass.ETF)) -> dict:
        instruments = self.provider.discover_markets(asset_classes)
        rejected: list[dict[str, str]] = []
        candidates: list[dict] = []
        seen: set[str] = set()
        considered = 0
        now_ms = time.time_ns() // 1_000_000
        exchange_states: dict[str, dict] = {}
        market_state_error = None
        market_state_reader = getattr(self.provider, "market_state", None)
        if callable(market_state_reader):
            try:
                exchange_states = market_state_reader()
            except MarketDataError as exc:
                market_state_error = str(exc)

        # Stage 1: bounded quote screening. Only this small pool incurs quote calls.
        for instrument in instruments:
            if considered >= self.max_quote_candidates:
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
                score, quality = self._quote_quality(
                    quote, now_ms, self.max_quote_age_seconds * 1000
                )
                candidates.append({
                    "instrument": instrument,
                    "quote": quote,
                    "quality": quality,
                    "score": score,
                })
            except (MarketDataError, ValueError, TypeError, KeyError) as exc:
                rejected.append({"symbol": instrument.symbol, "reason": str(exc)})

        # Stable deterministic tie-break prevents catalog order deciding the winners.
        candidates.sort(key=lambda item: (-item["score"], item["instrument"].symbol))
        ranked_candidates = candidates[: self.max_instruments]
        analyses: list[dict] = []

        # Stage 2: retrieve history only for the highest-ranked candidates.
        for rank, candidate in enumerate(ranked_candidates, start=1):
            instrument = candidate["instrument"]
            quote = candidate["quote"]
            quality = candidate["quality"]
            bid, ask = quote.get("bid"), quote.get("ask")
            spread_fraction = quality["spread_fraction"]
            blockers: list[str] = [
                "No equity/ETF broker adapter is configured",
                "No asset-specific equity/ETF Risk Manager approval path exists",
                "This research result is not a trade instruction",
            ]
            if bid is None or ask is None:
                if self.require_two_sided_quote:
                    rejected.append({"symbol": instrument.symbol, "reason": "Two-sided bid/ask quote required"})
                    continue
                blockers.append("Two-sided bid/ask quote is unavailable")
            elif spread_fraction is None or spread_fraction > self.max_execution_spread_fraction:
                blockers.append("Bid/ask spread exceeds the execution-quality threshold")

            average_volume = quote.get("averageVolume")
            if average_volume is None:
                blockers.append("Average trading volume is unavailable")
            elif float(average_volume) <= 0:
                blockers.append("Average trading volume is not positive")

            exchange = instrument.metadata.get("exchange") if instrument.metadata else None
            market_state = exchange_states.get(str(exchange).casefold()) if exchange else None
            if market_state is None and instrument.metadata:
                mic = instrument.metadata.get("mic_code")
                market_state = exchange_states.get(str(mic).casefold()) if mic else None
            market_open = (
                market_state.get("is_market_open") if market_state is not None
                else quote.get("is_market_open")
            )
            if market_open is False:
                blockers.append("US exchange is currently reported closed")
            elif market_open is not True:
                blockers.append("Exchange session state is unknown")
            if market_state_error:
                blockers.append("Exchange session lookup failed; execution remains blocked")

            try:
                rows = self.provider.ohlcv(
                    instrument.symbol, timeframe=self.timeframe, limit=self.candle_limit
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
                    "rank": rank,
                    "selection_score": quality["liquidity_score"],
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
                        **quality,
                        "is_market_open": market_open,
                        "exchange_session": market_state,
                        "market_state_error": market_state_error,
                    },
                    "chart_analysis": chart.model_dump(),
                    "strategy_research": research,
                    "research_eligible": True,
                    "execution_eligible": False,
                    "execution_blockers": blockers,
                })
            except (MarketDataError, ValueError, TypeError, KeyError) as exc:
                rejected.append({"symbol": instrument.symbol, "reason": str(exc)})

        return {
            "universe": "twelve_data_us_equities_and_etfs",
            "provider_id": self.provider.provider_id,
            "timeframe": self.timeframe,
            "discovered": len(instruments),
            "considered": considered,
            "quote_candidates": len(candidates),
            "ranked": len(ranked_candidates),
            "analysed": len(analyses),
            "selection_method": "bounded_quote_screen_volume_60_freshness_25_spread_15",
            "analyses": analyses,
            "rejected": rejected,
            "execution": "disabled_research_only",
        }
