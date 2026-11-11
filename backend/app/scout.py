from __future__ import annotations

import math

from typing import Any
import re
import sqlite3

from .agents.charts import ChartsAgent
from .agents.contracts import MarketAnalysis
from .strategy_research import StrategyResearchAgent

from .market_data import MarketDataError

_TIMEFRAME_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def _timeframe_seconds(value: str) -> int | None:
    match = re.fullmatch(r"([1-9][0-9]*)([smhdw])", value or "")
    if not match:
        return None
    return int(match.group(1)) * _TIMEFRAME_SECONDS[match.group(2)]




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
        quote_store: Any | None = None,
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
        self.quote_store = quote_store
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
                raw_bid = ticker.get("bid")
                raw_ask = ticker.get("ask")
                try:
                    bid_value = float(raw_bid)
                    ask_value = float(raw_ask)
                    quote_observed = (
                        math.isfinite(bid_value) and math.isfinite(ask_value)
                        and bid_value > 0 and ask_value >= bid_value
                    )
                except (TypeError, ValueError, OverflowError):
                    bid_value = ask_value = float(last)
                    quote_observed = False
                midpoint = (bid_value + ask_value) / 2 if quote_observed else None
                candidates.append({
                    "symbol": normalized,
                    "provider_id": provider_id,
                    "instrument_id": instrument_id,
                    "last": float(last),
                    "bid": bid_value,
                    "ask": ask_value,
                    "spread_fraction": (
                        (ask_value - bid_value) / midpoint if midpoint else None
                    ),
                    "quote_observed": quote_observed,
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
            quote_capture_status = "not_configured"
            if self.quote_store is not None:
                quote_capture_status = "unavailable"
                if candidate.get("quote_observed") is True:
                    try:
                        captured = self.quote_store.record(
                            candidate.get("provider_id") or "ccxt:kraken",
                            candidate.get("instrument_id") or (
                                f"{candidate.get('provider_id') or 'ccxt:kraken'}:spot:{symbol.upper()}"
                            ),
                            symbol,
                            candidate["ticker_timestamp_ms"],
                            candidate["bid"],
                            candidate["ask"],
                            timestamp_source=candidate.get("ticker_timestamp_source", "exchange"),
                        )
                        quote_capture_status = captured["status"]
                    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
                        quote_capture_status = "capture_error"
                else:
                    quote_capture_status = "quote_not_observed"
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
                interval_seconds = _timeframe_seconds(self.timeframe)
                quote_snapshots = []
                quote_history_status = "not_configured"
                quote_coverage = None
                if self.quote_store is not None:
                    quote_history_status = "unavailable"
                    try:
                        if research_candles:
                            quote_until_ms = (
                                int(research_candles[-1]["timestamp"])
                                + (interval_seconds * 1000 if interval_seconds else 1)
                            )
                            quote_snapshots = self.quote_store.get(
                                candidate.get("provider_id") or "ccxt:kraken",
                                candidate.get("instrument_id") or (
                                    f"{candidate.get('provider_id') or 'ccxt:kraken'}:spot:{symbol.upper()}"
                                ),
                                since_ms=int(research_candles[0]["timestamp"]),
                                until_ms=quote_until_ms,
                            )
                            quote_coverage = self.quote_store.coverage(
                                candidate.get("provider_id") or "ccxt:kraken",
                                candidate.get("instrument_id") or (
                                    f"{candidate.get('provider_id') or 'ccxt:kraken'}:spot:{symbol.upper()}"
                                ),
                            )
                            quote_history_status = "available" if quote_snapshots else "empty"
                    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
                        quote_history_status = "unavailable"
                        quote_snapshots = []
                max_quote_age_seconds = min(max(interval_seconds or 60, 60), 300)
                try:
                    research = self.strategy_research.research(
                        research_candles,
                        expected_interval_seconds=interval_seconds,
                        calendar_model="crypto_24x7" if interval_seconds else "unspecified",
                        quote_snapshots=quote_snapshots,
                        max_quote_age_seconds=max_quote_age_seconds,
                    )
                except ValueError as exc:
                    insufficient_history = "At least 120 candles are required" in str(exc)
                    research = {
                        "status": "insufficient_or_invalid_history",
                        "reason": str(exc),
                        "diagnostic_code": (
                            "insufficient_history" if insufficient_history else "invalid_history"
                        ),
                        "failure_category": "sample" if insufficient_history else "data_quality",
                        "sample_adequacy": {
                            "status": "insufficient_history" if insufficient_history else "invalid_input",
                            "available_bars": len(research_candles),
                            "minimum_research_bars": 120,
                        },
                    }
                if isinstance(research, dict):
                    research = {
                        **research,
                        "history_source": history_source,
                        "history_bars": len(research_candles),
                        "persisted_finalized_bars_available": persisted_bars,
                        "quote_capture_status": quote_capture_status,
                        "quote_history_status": quote_history_status,
                        "quote_observations_available": len(quote_snapshots),
                        "quote_coverage": quote_coverage,
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

