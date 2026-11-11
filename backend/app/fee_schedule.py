from __future__ import annotations

from decimal import Decimal, InvalidOperation


class FeeScheduleError(ValueError):
    """A provider fee schedule cannot safely price the requested research period."""


def resolve_taker_fee(
    schedule: dict,
    *,
    provider_id: str,
    instrument_id: str,
    market_type: str,
    quote_currency: str,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
) -> dict:
    """Resolve an explicit, quote-denominated taker fee schedule without look-ahead.

    Research fills are simulated as marketable orders, so taker fees are used.
    Maker rates are retained as provider metadata but are never substituted.
    """
    if not isinstance(schedule, dict):
        raise FeeScheduleError("Fee schedule must be an object")
    expected = {
        "provider_id": provider_id,
        "instrument_id": instrument_id,
        "market_type": market_type,
    }
    for key, value in expected.items():
        actual = schedule.get(key)
        if not isinstance(value, str) or not value.strip() or actual != value:
            raise FeeScheduleError(f"Fee schedule {key} does not match research scope")

    source = schedule.get("source")
    fee_currency = schedule.get("fee_currency")
    quote = quote_currency
    if not isinstance(source, str) or not source.strip():
        raise FeeScheduleError("Fee schedule source is required")
    if (
        not isinstance(fee_currency, str) or not fee_currency.strip()
        or not isinstance(quote, str) or not quote.strip()
    ):
        raise FeeScheduleError("Fee and quote currencies are required")
    fee_currency = fee_currency.strip()
    quote = quote.strip()
    if fee_currency.upper() != quote.upper():
        raise FeeScheduleError("Only quote-denominated fees are supported by this cost model")

    def rate(name: str) -> Decimal:
        value = schedule.get(name)
        if isinstance(value, bool) or value is None:
            raise FeeScheduleError(f"Fee schedule {name} is required")
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise FeeScheduleError(f"Fee schedule {name} is invalid") from None
        if not parsed.is_finite() or parsed < 0 or parsed >= 1:
            raise FeeScheduleError(f"Fee schedule {name} must be finite and in [0, 1)")
        return parsed

    maker_rate = rate("maker_rate")
    taker_rate = rate("taker_rate")
    observed_at = schedule.get("observed_at_ms")
    effective_from = schedule.get("effective_from_ms")
    effective_until = schedule.get("effective_until_ms")
    for name, value in (
        ("observed_at_ms", observed_at),
        ("effective_from_ms", effective_from),
        ("effective_until_ms", effective_until),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise FeeScheduleError(f"Fee schedule {name} must be a positive integer")
    if effective_from > effective_until:
        raise FeeScheduleError("Fee schedule effective range is invalid")
    if observed_at > start_timestamp_ms:
        raise FeeScheduleError("Fee schedule was not observed before the research period")
    if effective_from > start_timestamp_ms or effective_until < end_timestamp_ms:
        raise FeeScheduleError("Fee schedule does not cover the complete research period")

    return {
        "provider_id": provider_id,
        "instrument_id": instrument_id,
        "market_type": market_type,
        "maker_rate": str(maker_rate),
        "taker_rate": str(taker_rate),
        "fee_currency": fee_currency.upper(),
        "quote_currency": quote.upper(),
        "source": source.strip(),
        "observed_at_ms": observed_at,
        "effective_from_ms": effective_from,
        "effective_until_ms": effective_until,
        "execution_liquidity_role": "taker",
        "applied_rate": float(taker_rate),
    }
