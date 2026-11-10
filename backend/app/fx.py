"""Validated FX observations used for paper portfolio valuation and conversion."""
from __future__ import annotations

import math
import time

from .market_data import MarketDataError


def validate_fx_observation(
    observation: dict,
    symbol: str,
    *,
    max_age_seconds: int,
    now_ms: int | None = None,
) -> dict:
    """Validate pair, positive finite rate and timestamp freshness; never invent a rate."""
    normalized = str(symbol).strip().upper()
    parts = normalized.split("/")
    if len(parts) != 2 or not all(len(part) == 3 and part.isalpha() for part in parts):
        raise MarketDataError("FX symbol must be a three-letter currency pair")
    if not isinstance(observation, dict):
        raise MarketDataError("FX observation is missing or malformed")
    if str(observation.get("symbol") or "").upper() != normalized:
        raise MarketDataError("FX observation pair does not match the requested pair")
    try:
        rate = float(observation["rate"])
        timestamp = int(observation["timestamp"])
        age_limit_ms = int(max_age_seconds) * 1000
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise MarketDataError("FX observation is missing a valid rate or timestamp") from exc
    if not math.isfinite(rate) or rate <= 0 or timestamp <= 0 or age_limit_ms <= 0:
        raise MarketDataError("FX observation rate, timestamp and age limit must be positive and finite")
    current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    age_ms = current_ms - timestamp
    if age_ms < -30_000:
        raise MarketDataError("FX observation timestamp is in the future")
    if age_ms > age_limit_ms:
        raise MarketDataError("FX observation is stale")
    return {
        **observation,
        "symbol": normalized,
        "base_currency": parts[0],
        "quote_currency": parts[1],
        "rate": rate,
        "timestamp": timestamp,
        "age_seconds": max(0.0, age_ms / 1000),
    }


def invert_fx_observation(observation: dict) -> dict:
    """Invert a previously validated rate without changing its source timestamp."""
    rate = float(observation["rate"])
    if not math.isfinite(rate) or rate <= 0:
        raise MarketDataError("Cannot invert an invalid FX rate")
    base = str(observation["base_currency"])
    quote = str(observation["quote_currency"])
    return {
        **observation,
        "symbol": f"{quote}/{base}",
        "base_currency": quote,
        "quote_currency": base,
        "rate": 1.0 / rate,
        "inverted": True,
    }
