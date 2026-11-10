import pytest

from app.fx import invert_fx_observation, validate_fx_observation
from app.market_data import MarketDataError


def observation(rate=1.1, timestamp=1_800_000_000_000):
    return {
        "symbol": "EUR/USD",
        "rate": rate,
        "timestamp": timestamp,
        "provider_id": "twelve_data",
    }


def test_validate_fx_observation_accepts_fresh_positive_rate():
    result = validate_fx_observation(
        observation(), "eur/usd", max_age_seconds=60,
        now_ms=1_800_000_030_000,
    )
    assert result["symbol"] == "EUR/USD"
    assert result["base_currency"] == "EUR"
    assert result["quote_currency"] == "USD"
    assert result["rate"] == pytest.approx(1.1)
    assert result["age_seconds"] == pytest.approx(30)


def test_validate_fx_observation_rejects_stale_and_future_rates():
    with pytest.raises(MarketDataError, match="stale"):
        validate_fx_observation(
            observation(timestamp=1_800_000_000_000), "EUR/USD",
            max_age_seconds=30, now_ms=1_800_000_031_000,
        )
    with pytest.raises(MarketDataError, match="future"):
        validate_fx_observation(
            observation(timestamp=1_800_000_040_000), "EUR/USD",
            max_age_seconds=60, now_ms=1_800_000_000_000,
        )


@pytest.mark.parametrize("rate", [0, -1, float("nan"), float("inf"), -float("inf")])
def test_validate_fx_observation_rejects_invalid_rates(rate):
    with pytest.raises(MarketDataError):
        validate_fx_observation(
            observation(rate=rate), "EUR/USD", max_age_seconds=60,
            now_ms=1_800_000_000_000,
        )


def test_validate_fx_observation_rejects_pair_mismatch_and_missing_data():
    with pytest.raises(MarketDataError, match="does not match"):
        validate_fx_observation(
            observation(), "USD/EUR", max_age_seconds=60,
            now_ms=1_800_000_000_000,
        )
    with pytest.raises(MarketDataError):
        validate_fx_observation(
            None, "EUR/USD", max_age_seconds=60,
            now_ms=1_800_000_000_000,
        )


def test_invert_fx_observation_preserves_source_timestamp():
    original = validate_fx_observation(
        observation(rate=1.25), "EUR/USD", max_age_seconds=60,
        now_ms=1_800_000_000_000,
    )
    inverted = invert_fx_observation(original)
    assert inverted["symbol"] == "USD/EUR"
    assert inverted["rate"] == pytest.approx(0.8)
    assert inverted["timestamp"] == original["timestamp"]
    assert inverted["provider_id"] == original["provider_id"]
    assert inverted["inverted"] is True


def test_invert_fx_observation_rejects_overflowed_inverse():
    tiny = validate_fx_observation(
        observation(rate=1e-320), "EUR/USD", max_age_seconds=60,
        now_ms=1_800_000_000_000,
    )
    with pytest.raises(MarketDataError, match="Inverted FX rate"):
        invert_fx_observation(tiny)
