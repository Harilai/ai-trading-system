from __future__ import annotations

from typing import Any
import sqlite3

from .agents.charts import ChartsAgent
from .agents.contracts import MarketAnalysis
from .strategy_research import StrategyResearchAgent
from .market_data import MarketDataError


class ScoutAgent:
    """Discovers liquid EUR-quoted spot markets and delegates deterministic chart analysis."""

    def __init__(
        self,
        market_data: Any,
        charts: ChartsAgent | None = None,
        *,
        max_symbols: int = 5,
        timeframe: str = "1h",
        candle_limit: int = 250,
        strategy_research: StrategyResearchAgent | None = None,
        max_ticker_age_seconds: int = 60,
        historical_store: Any | None = None,
        research_history_limit: int = 5000,
    ):
        if max_symbols < 1:
            raise ValueError("max_symbols must be positive")
        if not 1 <= research_history_limit <= 100_000:
            raise ValueError("research_history_limit must be between 1 and 100000")
        self.market_data = market_data
        self.charts = charts or ChartsAgent()
        self.max_symbols = max_symbols
        self.timeframe = timeframe
        self.candle_limit = candle_limit
        self.strategy_research = strategy_research or StrategyResearchAgent()
        self.max_ticker_age_seconds = max_ticker_age_seconds
        self.historical_store = historical_store
        self.research_history_limit = research_history_limit

    def scan(self, include_symbols: list[str] | None = None) -> dict[str, Any]:
        rejected: list[dict[str, str]] = []
        candidates = self.market_data.discover_spot_symbols(
            quote="EUR",
            limit=self.max_symbols,
        )
        for failure in getattr(self.market_data, "last_discovery_failures", []):
            rejected.append({
                "symbol": failure["provider_id"],
                "reason": "Provider discovery failed: " + failure["reason"],
            })
        known_instruments = {
            item.get("instrument_id") or f"{item.get('provider_id', 'ccxt:kraken')}:spot:{item['symbol'].upper()}"
            for item in candidates
        }
        for held in include_symbols or []:
            if isinstance(held, dict):
                normalized = str(held.get("symbol", "")).strip().upper()
                provider_id = str(held.get("provider_id") or "ccxt:kraken")
                instrument_id = str(held.get("instrument_id") or f"{provider_id}:spot:{normalized}")
            else:
                normalized = str(held).strip().upper()
                provider_id = "ccxt:kraken"
                instrument_id = f"{provider_id}:spot:{normalized}"
            if not normalized.endswith("/EUR") or instrument_id in known_instruments:
                continue
            try:
                try:
                    ticker = self.market_data.ticker(normalized, provider_id=provider_id)
                except TypeError:
                    ticker = self.market_data.ticker(normalized)
                if self.market_data.is_stale(ticker, self.max_ticker_age_seconds):
                    continue
                last = ticker.get("last")
                if last is None or float(last) <= 0:
                    continue
                candidates.append({
                    "symbol": normalized,
                    "provider_id": provider_id,
                    "instrument_id": instrument_id,
                    "last": float(last),
                    "bid": float(ticker.get("bid") or last),
                    "ask": float(ticker.get("ask") or last),
                    "spread_fraction": 0.0,
                    "quote_volume_24h": 0.0,
                    "ticker_timestamp_ms": int(ticker["timestamp"]),
                    "ticker_timestamp_source": ticker.get("timestamp_source", "exchange"),
                    "included_open_position": True,
                })
                known_instruments.add(instrument_id)
            except (MarketDataError, ValueError, TypeError, KeyError):
                rejected.append({"symbol": normalized, "reason": "Unable to refresh held position ticker"})
        analyses: list[dict[str, Any]] = []

        for candidate in candidates:
            symbol = candidate["symbol"]
            try:
                provider_id = candidate.get("provider_id")
                try:
                    rows = self.market_data.ohlcv(
                        symbol,
                        timeframe=self.timeframe,
                        limit=self.candle_limit,
                        provider_id=provider_id,
                    )
                except TypeError:
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
                research_candles = candles
                history_source = "provider_recent"
                persisted_bars = 0
                if self.historical_store is not None:
                    history_provider_id = candidate.get("provider_id") or "ccxt:kraken"
                    history_instrument_id = candidate.get("instrument_id") or (
                        f"{history_provider_id}:spot:{symbol.upper()}"
                    )
                    try:
                        stored_rows = self.historical_store.get(
                            history_provider_id, history_instrument_id, self.timeframe,
                            limit=self.research_history_limit,
                        )
                        stored_candles = [
                            {
                                "timestamp": row["timestamp_ms"],
                                "open": row["open"],
                                "high": row["high"],
                                "low": row["low"],
                                "close": row["close"],
                                "volume": row["volume"],
                            }
                            for row in stored_rows if row.get("is_final") is True
                        ]
                        persisted_bars = len(stored_candles)
                        if persisted_bars > len(candles):
                            research_candles = stored_candles
                            history_source = "persistent_finalized_ohlcv"
                    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
                        # Research falls back to the live provider sample; chart
                        # analysis and execution are not affected by store outages.
                        history_source = "provider_recent_store_unavailable"
                try:
                    research = self.strategy_research.research(research_candles)
                except ValueError as exc:
                    research = {"status": "insufficient_or_invalid_history", "reason": str(exc)}
                if isinstance(research, dict):
                    research = {
                        **research,
                        "history_source": history_source,
                        "history_bars": len(research_candles),
                        "persisted_finalized_bars_available": persisted_bars,
                    }
                analyses.append(
                    {
                        "market": candidate,
                        "analysis": analysis.model_dump(),
                        "strategy_research": research,
                    }
                )
            except (MarketDataError, ValueError, KeyError, TypeError) as exc:
                rejected.append({"symbol": symbol, "reason": str(exc)})

        return {
            "universe": "configured_provider_crypto_spot_EUR",
            "timeframe": self.timeframe,
            "discovered": len(candidates),
            "analysed": len(analyses),
            "analyses": analyses,
            "rejected": rejected,
            "execution": "analysis_only",
        }

