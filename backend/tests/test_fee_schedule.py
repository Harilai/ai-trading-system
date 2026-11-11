import pytest

from app.fee_schedule import FeeScheduleError, resolve_taker_fee


def valid_schedule(**overrides):
    schedule = {
        "provider_id": "ccxt:kraken",
        "instrument_id": "ccxt:kraken:spot:BTC/EUR",
        "market_type": "spot",
        "maker_rate": "0.0016",
        "taker_rate": "0.0026",
        "fee_currency": "EUR",
        "source": "provider_fee_metadata",
        "observed_at_ms": 1_699_999_000_000,
        "effective_from_ms": 1_699_999_000_000,
        "effective_until_ms": 1_700_100_000_000,
    }
    schedule.update(overrides)
    return schedule


def test_resolve_taker_fee_uses_exact_provider_instrument_scope():
    result = resolve_taker_fee(
        valid_schedule(),
        provider_id="ccxt:kraken",
        instrument_id="ccxt:kraken:spot:BTC/EUR",
        market_type="spot",
        quote_currency="EUR",
        start_timestamp_ms=1_700_000_000_000,
        end_timestamp_ms=1_700_050_000_000,
    )
    assert result["applied_rate"] == pytest.approx(0.0026)
    assert result["maker_rate"] == "0.0016"
    assert result["execution_liquidity_role"] == "taker"


@pytest.mark.parametrize("field,value", [
    ("provider_id", "ccxt:binance"),
    ("instrument_id", "ccxt:kraken:spot:ETH/EUR"),
    ("market_type", "margin"),
])
def test_fee_schedule_rejects_scope_mismatch(field, value):
    with pytest.raises(FeeScheduleError, match="does not match"):
        resolve_taker_fee(
            valid_schedule(**{field: value}),
            provider_id="ccxt:kraken",
            instrument_id="ccxt:kraken:spot:BTC/EUR",
            market_type="spot",
            quote_currency="EUR",
            start_timestamp_ms=1_700_000_000_000,
            end_timestamp_ms=1_700_050_000_000,
        )


def test_fee_schedule_rejects_non_quote_fee_currency():
    with pytest.raises(FeeScheduleError, match="quote-denominated"):
        resolve_taker_fee(
            valid_schedule(fee_currency="KRAKEN"),
            provider_id="ccxt:kraken",
            instrument_id="ccxt:kraken:spot:BTC/EUR",
            market_type="spot",
            quote_currency="EUR",
            start_timestamp_ms=1_700_000_000_000,
            end_timestamp_ms=1_700_050_000_000,
        )


def test_fee_schedule_rejects_schedule_observed_after_backtest_start():
    with pytest.raises(FeeScheduleError, match="not observed"):
        resolve_taker_fee(
            valid_schedule(observed_at_ms=1_700_000_000_001),
            provider_id="ccxt:kraken",
            instrument_id="ccxt:kraken:spot:BTC/EUR",
            market_type="spot",
            quote_currency="EUR",
            start_timestamp_ms=1_700_000_000_000,
            end_timestamp_ms=1_700_050_000_000,
        )


def test_fee_schedule_must_cover_entire_research_period():
    with pytest.raises(FeeScheduleError, match="complete research period"):
        resolve_taker_fee(
            valid_schedule(effective_until_ms=1_700_010_000_000),
            provider_id="ccxt:kraken",
            instrument_id="ccxt:kraken:spot:BTC/EUR",
            market_type="spot",
            quote_currency="EUR",
            start_timestamp_ms=1_700_000_000_000,
            end_timestamp_ms=1_700_050_000_000,
        )


@pytest.mark.parametrize("field,value", [
    ("maker_rate", "NaN"),
    ("taker_rate", "1"),
    ("taker_rate", "-0.01"),
    ("taker_rate", True),
])
def test_fee_schedule_rejects_invalid_rates(field, value):
    with pytest.raises(FeeScheduleError):
        resolve_taker_fee(
            valid_schedule(**{field: value}),
            provider_id="ccxt:kraken",
            instrument_id="ccxt:kraken:spot:BTC/EUR",
            market_type="spot",
            quote_currency="EUR",
            start_timestamp_ms=1_700_000_000_000,
            end_timestamp_ms=1_700_050_000_000,
        )
