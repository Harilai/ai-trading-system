"""Fail-closed paper exits for USD equities and ETFs."""
from __future__ import annotations

import hashlib
import math
import time
from typing import Any

from .config import settings
from .fx import invert_fx_observation, validate_fx_observation
from .market_data import MarketDataError
from .providers.base import AssetClass, MarketType


class EquityExitMonitor:
    """Close eligible Twelve Data paper positions using fresh market and FX evidence."""

    def __init__(self, provider: Any, portfolio: Any, *, instrument_cache_ttl_seconds: float = 3600):
        if instrument_cache_ttl_seconds <= 0:
            raise ValueError("instrument_cache_ttl_seconds must be positive")
        self.provider = provider
        self.portfolio = portfolio
        self.instrument_cache_ttl_seconds = instrument_cache_ttl_seconds
        self._instrument_cache = None
        self._instrument_cache_at = 0.0

    def run_once(self) -> dict:
        closed = []
        skipped = []
        positions = self.portfolio.snapshot().get("positions", [])
        candidates = [
            position for position in positions
            if position.get("provider_id") == "twelve_data"
            and str(position.get("quote_currency") or "").upper() == "USD"
            and (position.get("stop_loss") is not None or position.get("take_profit") is not None)
        ]
        fx = None
        if candidates:
            try:
                raw_fx = self.provider.exchange_rate("EUR/USD")
                fx = invert_fx_observation(validate_fx_observation(
                    raw_fx, "EUR/USD", max_age_seconds=settings.max_fx_data_age_seconds,
                ))
            except (MarketDataError, KeyError, TypeError, ValueError, OverflowError) as exc:
                return {"checked": 0, "closed": [], "skipped": [{"reason": f"fresh USD/EUR FX unavailable: {exc}"}]}

        try:
            discovered = self._discover_instruments() if candidates else []
        except MarketDataError as exc:
            return {"checked": 0, "closed": [], "skipped": [{"reason": f"instrument catalog unavailable: {exc}"}]}

        for position in candidates:
            symbol = str(position.get("symbol") or "").strip().upper()
            instrument_id = str(position.get("instrument_id") or "")
            try:
                instrument = self._resolve_instrument(discovered, symbol, instrument_id)
                quote = self.provider.ticker(symbol)
                price = self._validate_quote(quote, symbol, instrument)
                stop = self._finite_level(position.get("stop_loss"))
                target = self._finite_level(position.get("take_profit"))
                reason = "stop_loss" if stop is not None and price <= stop else (
                    "take_profit" if target is not None and price >= target else None
                )
                if reason is None:
                    continue
                quantity = float(position["quantity"])
                if not math.isfinite(quantity) or quantity <= 0:
                    raise MarketDataError("Position quantity is invalid")
                client_order_id = self._exit_id(position, reason)
                result = self.portfolio.execute(
                    symbol, "sell", quantity, price,
                    settings.equity_paper_fee_rate,
                    settings.equity_paper_slippage_rate,
                    1.0, 0.0,
                    client_order_id=client_order_id,
                    provider_id="twelve_data",
                    instrument_id=instrument_id,
                    strategy_id=position.get("strategy_id"),
                    quote_currency="USD",
                    fx_rate_to_eur=float(fx["rate"]),
                    fx_provider_id=fx["provider_id"],
                    fx_timestamp=int(fx["timestamp"]),
                    market_timestamp=int(quote["timestamp"]),
                    market_timestamp_source=str(quote.get("timestamp_source") or "provider"),
                )
                closed.append({"symbol": symbol, "reason": reason, "order": result})
            except Exception as exc:
                # One malformed/stale instrument must not prevent other positions
                # from being evaluated or turn missing evidence into an order.
                skipped.append({"symbol": symbol, "reason": str(exc)[:300]})
        return {"checked": len(candidates), "closed": closed, "skipped": skipped, "mode": "paper"}

    def _discover_instruments(self):
        now = time.monotonic()
        if (self._instrument_cache is not None
                and now - self._instrument_cache_at < self.instrument_cache_ttl_seconds):
            return self._instrument_cache
        discovered = self.provider.discover_markets((AssetClass.EQUITY, AssetClass.ETF))
        self._instrument_cache = discovered
        self._instrument_cache_at = now
        return discovered

    @staticmethod
    def _resolve_instrument(discovered: list, symbol: str, instrument_id: str):
        matches = [item for item in discovered
                   if item.symbol.upper() == symbol and item.instrument_id == instrument_id]
        if len(matches) != 1:
            raise MarketDataError("Equity instrument is missing, ambiguous, or has changed identity")
        instrument = matches[0]
        if (not instrument.active or instrument.provider_id != "twelve_data"
                or instrument.quote != "USD" or instrument.market_type != MarketType.CASH_EQUITY
                or instrument.asset_class not in {AssetClass.EQUITY, AssetClass.ETF}):
            raise MarketDataError("Instrument is not an active USD cash equity or ETF")
        return instrument

    def _validate_quote(self, quote: dict, symbol: str, instrument: Any) -> float:
        if not isinstance(quote, dict) or str(quote.get("symbol") or "").upper() != symbol:
            raise MarketDataError("Equity quote identity does not match the position")
        if str(quote.get("currency") or "").upper() != "USD":
            raise MarketDataError("Equity quote currency must explicitly be USD")
        if quote.get("provider_id") != "twelve_data":
            raise MarketDataError("Equity quote provider identity is missing or mismatched")
        venue_fields = ("exchange", "mic_code")
        expected_venue = {field: str(instrument.metadata.get(field) or "").strip().casefold()
                          for field in venue_fields}
        observed_venue = {field: str(quote.get(field) or "").strip().casefold()
                          for field in venue_fields}
        if any(expected_venue.values()):
            if not any(observed_venue.values()):
                raise MarketDataError("Equity quote is missing venue identity")
            if any(expected_venue[field] and observed_venue[field]
                   and expected_venue[field] != observed_venue[field] for field in venue_fields):
                raise MarketDataError("Equity quote venue does not match the instrument")
            if not any(expected_venue[field] == observed_venue[field]
                       for field in venue_fields if expected_venue[field]):
                raise MarketDataError("Equity quote venue identity could not be verified")
        try:
            price = float(quote["bid"] if quote.get("bid") is not None else quote["last"])
            timestamp = int(quote["timestamp"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise MarketDataError("Equity quote is missing a valid executable price or timestamp") from exc
        now_ms = int(time.time() * 1000)
        if (not math.isfinite(price) or price <= 0 or timestamp <= 0 or timestamp > now_ms
                or now_ms - timestamp > settings.max_data_age_seconds * 1000):
            raise MarketDataError("Equity quote is invalid or stale")
        bid, ask = quote.get("bid"), quote.get("ask")
        if bid is not None or ask is not None:
            try:
                bid, ask = float(bid), float(ask)
                spread = (ask - bid) / ((ask + bid) / 2)
            except (TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
                raise MarketDataError("Equity bid/ask is malformed") from exc
            if (not all(math.isfinite(value) and value > 0 for value in (bid, ask))
                    or ask < bid or spread > settings.max_equity_spread_fraction):
                raise MarketDataError("Equity spread is invalid or exceeds the configured limit")
        market_open = quote.get("is_market_open")
        if market_open is False:
            raise MarketDataError("Equity quote reports a closed exchange session")
        if market_open is not None and market_open is not True:
            raise MarketDataError("Equity quote contains an invalid market-session flag")
        if market_open is None:
            try:
                states = self.provider.market_state()
            except MarketDataError as exc:
                raise MarketDataError("Exchange session could not be verified") from exc
            keys = (instrument.metadata.get("exchange"), instrument.metadata.get("mic_code"))
            state = next((states.get(str(key).casefold()) for key in keys
                          if key and states.get(str(key).casefold())), None)
            if not state or state.get("is_market_open") is not True:
                raise MarketDataError("Equity exchange session is closed or unknown")
        return price

    @staticmethod
    def _finite_level(value):
        if value is None:
            return None
        level = float(value)
        if not math.isfinite(level) or level <= 0:
            raise MarketDataError("Stored stop/target level is invalid")
        return level

    @staticmethod
    def _exit_id(position: dict, reason: str) -> str:
        identity = "|".join(str(position.get(key) or "") for key in (
            "provider_id", "instrument_id", "quantity", "average_price", "stop_loss", "take_profit",
        )) + f"|{reason}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        return f"equity-exit-{digest}"
