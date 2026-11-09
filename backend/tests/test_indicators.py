import math

import pytest

from app.indicators import atr, bollinger_bands, ema, macd, rsi, sma


def test_sma_and_ema_match_reference_values():
    values = [1, 2, 3, 4, 5]
    assert sma(values, 3) == pytest.approx(4)
    # EMA(3): SMA seed 2, then 3.5, then 4.25.
    assert ema(values, 3) == pytest.approx(4.25)


def test_rsi_matches_uptrend_downtrend_and_flat_references():
    assert rsi(list(range(1, 16)), 14) == pytest.approx(100)
    assert rsi(list(range(15, 0, -1)), 14) == pytest.approx(0)
    assert rsi([10] * 15, 14) == pytest.approx(50)


def test_macd_shape_and_histogram_identity():
    result = macd([float(value) for value in range(1, 41)])
    assert set(result) == {"macd", "signal", "histogram"}
    assert result["histogram"] == pytest.approx(result["macd"] - result["signal"])


def test_bollinger_bands_match_population_standard_deviation_reference():
    bands = bollinger_bands([float(value) for value in range(1, 21)])
    expected_deviation = math.sqrt(33.25)
    assert bands["middle"] == pytest.approx(10.5)
    assert bands["lower"] == pytest.approx(10.5 - 2 * expected_deviation)
    assert bands["upper"] == pytest.approx(10.5 + 2 * expected_deviation)


def test_atr_matches_true_range_reference():
    candles = [
        {"open": 9, "high": 11, "low": 8, "close": 10},
        {"open": 10, "high": 12, "low": 9, "close": 11},
        {"open": 11, "high": 13, "low": 10, "close": 12},
    ]
    assert atr(candles, period=2) == pytest.approx(3)


def test_insufficient_history():
    with pytest.raises(ValueError):
        sma([1, 2], 3)


@pytest.mark.parametrize("indicator", [
    lambda: sma([1, float("nan"), 3], 2),
    lambda: ema([1, 2, float("inf")], 2),
    lambda: rsi([1, 2, 3, float("nan")], 2),
    lambda: bollinger_bands([1, 2, float("inf")], 3),
    lambda: macd([float(value) for value in range(1, 40)] + [float("nan")]),
])
def test_price_indicators_reject_non_finite_values(indicator):
    with pytest.raises(ValueError, match="finite"):
        indicator()


def test_indicator_periods_must_be_positive():
    with pytest.raises(ValueError, match="period"):
        rsi([1, 2, 3], 0)
    with pytest.raises(ValueError, match="period"):
        bollinger_bands([1, 2], 0)
    with pytest.raises(ValueError, match="MACD periods"):
        macd([float(value) for value in range(1, 40)], signal=0)


def test_atr_rejects_non_finite_and_inconsistent_candles():
    valid = [
        {"open": 9, "high": 11, "low": 8, "close": 10},
        {"open": 10, "high": 12, "low": 9, "close": 11},
    ]
    invalid = [dict(row) for row in valid]
    invalid[1]["high"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        atr(invalid, period=1)

    invalid = [dict(row) for row in valid]
    invalid[1]["low"] = 12
    with pytest.raises(ValueError, match="Invalid candle"):
        atr(invalid, period=1)
