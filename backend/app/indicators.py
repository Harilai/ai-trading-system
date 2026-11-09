from collections.abc import Sequence
import math


def _check(values: Sequence[float], minimum: int) -> list[float]:
    data = [float(value) for value in values]
    if len(data) < minimum:
        raise ValueError(f"At least {minimum} values are required")
    if not all(math.isfinite(value) for value in data):
        raise ValueError("Indicator inputs must be finite")
    if any(value <= 0 for value in data):
        raise ValueError("Prices must be positive")
    return data


def sma(values: Sequence[float], period: int) -> float:
    if period < 1:
        raise ValueError("period must be >= 1")
    data = _check(values, period)
    return sum(data[-period:]) / period


def ema(values: Sequence[float], period: int) -> float:
    if period < 1:
        raise ValueError("period must be >= 1")
    data = _check(values, period)
    alpha = 2 / (period + 1)
    result = sum(data[:period]) / period
    for value in data[period:]:
        result = alpha * value + (1 - alpha) * result
    return result


def rsi(values: Sequence[float], period: int = 14) -> float:
    if period < 1:
        raise ValueError("period must be >= 1")
    data = _check(values, period + 1)
    changes = [
        current - previous
        for previous, current in zip(data[-period - 1 : -1], data[-period:])
    ]
    gains = sum(max(change, 0.0) for change in changes) / period
    losses = sum(max(-change, 0.0) for change in changes) / period
    if losses == 0:
        return 100.0 if gains else 50.0
    return 100 - 100 / (1 + gains / losses)


def macd(
    values: Sequence[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, float]:
    if min(fast, slow, signal) < 1:
        raise ValueError("MACD periods must be >= 1")
    if fast >= slow:
        raise ValueError("fast must be smaller than slow")
    data = _check(values, slow + signal)

    def series(period: int) -> list[float]:
        alpha = 2 / (period + 1)
        current = sum(data[:period]) / period
        output = []
        for index, value in enumerate(data):
            if index >= period:
                current = alpha * value + (1 - alpha) * current
            if index >= slow:
                output.append(current)
        return output

    fast_values = series(fast)
    slow_values = series(slow)
    line_values = [fast_value - slow_value for fast_value, slow_value in zip(fast_values, slow_values)]
    alpha = 2 / (signal + 1)
    signal_value = sum(line_values[:signal]) / signal
    for value in line_values[signal:]:
        signal_value = alpha * value + (1 - alpha) * signal_value
    line = line_values[-1]
    return {
        "macd": line,
        "signal": signal_value,
        "histogram": line - signal_value,
    }


def bollinger_bands(
    values: Sequence[float],
    period: int = 20,
) -> dict[str, float]:
    if period < 1:
        raise ValueError("period must be >= 1")
    data = _check(values, period)
    window = data[-period:]
    mean = sum(window) / period
    deviation = (
        sum((value - mean) ** 2 for value in window) / period
    ) ** 0.5
    return {
        "lower": mean - 2 * deviation,
        "middle": mean,
        "upper": mean + 2 * deviation,
    }


def atr(
    candles: Sequence[dict[str, float]],
    period: int = 14,
) -> float:
    if period < 1:
        raise ValueError("period must be >= 1")
    if len(candles) < period + 1:
        raise ValueError(f"At least {period + 1} candles are required")

    normalized = []
    for candle in candles:
        row = {
            key: float(candle[key])
            for key in ("open", "high", "low", "close")
        }
        if not all(math.isfinite(value) for value in row.values()):
            raise ValueError("ATR candle values must be finite")
        if min(row.values()) <= 0:
            raise ValueError("ATR prices must be positive")
        if row["high"] < max(row["open"], row["close"], row["low"]):
            raise ValueError("Invalid candle high")
        if row["low"] > min(row["open"], row["close"], row["high"]):
            raise ValueError("Invalid candle low")
        normalized.append(row)

    true_ranges = []
    for previous, current in zip(normalized[:-1], normalized[1:]):
        true_ranges.append(
            max(
                current["high"] - current["low"],
                abs(current["high"] - previous["close"]),
                abs(current["low"] - previous["close"]),
            )
        )
    return sum(true_ranges[-period:]) / period
