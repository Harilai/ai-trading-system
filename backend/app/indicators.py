from collections.abc import Sequence

def _check(values: Sequence[float], minimum: int) -> list[float]:
    data = [float(x) for x in values]
    if len(data) < minimum:
        raise ValueError(f"At least {minimum} values are required")
    if any(x <= 0 for x in data):
        raise ValueError("Prices must be positive")
    return data

def sma(values: Sequence[float], period: int) -> float:
    if period < 1: raise ValueError("period must be >= 1")
    data = _check(values, period)
    return sum(data[-period:]) / period

def ema(values: Sequence[float], period: int) -> float:
    if period < 1: raise ValueError("period must be >= 1")
    data = _check(values, period)
    alpha = 2 / (period + 1)
    result = sum(data[:period]) / period
    for value in data[period:]:
        result = alpha * value + (1 - alpha) * result
    return result

def rsi(values: Sequence[float], period: int = 14) -> float:
    data = _check(values, period + 1)
    changes = [b - a for a, b in zip(data[-period-1:-1], data[-period:])]
    gains = sum(max(x, 0) for x in changes) / period
    losses = sum(max(-x, 0) for x in changes) / period
    if losses == 0: return 100.0 if gains else 50.0
    return 100 - 100 / (1 + gains / losses)

def macd(values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, float]:
    data = _check(values, slow + signal)
    if fast >= slow: raise ValueError("fast must be smaller than slow")
    def series(period: int) -> list[float]:
        alpha = 2 / (period + 1)
        current = sum(data[:period]) / period
        out = []
        for i, value in enumerate(data):
            if i >= period: current = alpha * value + (1-alpha) * current
            if i >= slow: out.append(current)
        return out
    fast_values = series(fast)
    slow_values = series(slow)
    line_values = [a-b for a,b in zip(fast_values, slow_values)]
    alpha = 2 / (signal + 1)
    signal_value = sum(line_values[:signal]) / signal
    for value in line_values[signal:]:
        signal_value = alpha * value + (1-alpha) * signal_value
    line = line_values[-1]
    return {"macd": line, "signal": signal_value, "histogram": line-signal_value}

def bollinger_bands(values: Sequence[float], period: int = 20) -> dict[str, float]:
    data = _check(values, period)
    window = data[-period:]
    mean = sum(window) / period
    std = (sum((x-mean)**2 for x in window) / period) ** 0.5
    return {"lower": mean-2*std, "middle": mean, "upper": mean+2*std}

def atr(candles: Sequence[dict[str, float]], period: int = 14) -> float:
    if len(candles) < period+1: raise ValueError(f"At least {period+1} candles are required")
    ranges = []
    for prev, cur in zip(candles[:-1], candles[1:]):
        ranges.append(max(cur["high"]-cur["low"], abs(cur["high"]-prev["close"]), abs(cur["low"]-prev["close"])))
    return sum(ranges[-period:]) / period
