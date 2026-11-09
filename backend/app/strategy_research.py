from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    description: str
    signal: Callable[[Sequence[float], int], bool]
    warmup: int


def _ema_series(values: Sequence[float], period: int) -> list[float]:
    if len(values) < period:
        raise ValueError("Insufficient history for EMA")
    alpha = 2.0 / (period + 1)
    current = sum(values[:period]) / period
    output = [current] * period
    for value in values[period:]:
        current = alpha * value + (1.0 - alpha) * current
        output.append(current)
    return output


def _rsi_at(values: Sequence[float], index: int, period: int = 14) -> float:
    if index < period:
        raise ValueError("Insufficient history for RSI")
    changes = [
        values[pos] - values[pos - 1]
        for pos in range(index - period + 1, index + 1)
    ]
    gains = sum(max(change, 0.0) for change in changes) / period
    losses = sum(max(-change, 0.0) for change in changes) / period
    if losses == 0:
        return 100.0 if gains else 50.0
    return 100.0 - 100.0 / (1.0 + gains / losses)


def _ema_cross(closes: Sequence[float], index: int) -> bool:
    fast = _ema_series(closes[: index + 1], 12)[-1]
    slow = _ema_series(closes[: index + 1], 26)[-1]
    return fast > slow


def _rsi_reversion(closes: Sequence[float], index: int) -> bool:
    value = _rsi_at(closes, index)
    return value < 30.0 if index == 14 else (
        _rsi_reversion_state(closes, index)
    )


def _rsi_reversion_state(closes: Sequence[float], index: int) -> bool:
    # Enter below 30 and remain invested until RSI recovers above 55.
    # The backtester supplies a desired-state signal; this function is a
    # stateless approximation that intentionally avoids anticipatory data.
    return _rsi_at(closes, index) < 30.0


def _bollinger_reversion(closes: Sequence[float], index: int) -> bool:
    window = closes[index - 19 : index + 1]
    mean = sum(window) / 20
    variance = sum((value - mean) ** 2 for value in window) / 20
    lower = mean - 2.0 * variance**0.5
    return closes[index] < lower


def _rsi_signal(closes: Sequence[float], index: int, in_position: bool) -> bool:
    value = _rsi_at(closes, index)
    if in_position:
        return value <= 55.0
    return value < 30.0


def _bollinger_signal(closes: Sequence[float], index: int, in_position: bool) -> bool:
    window = closes[index - 19 : index + 1]
    mean = sum(window) / 20
    variance = sum((value - mean) ** 2 for value in window) / 20
    deviation = variance**0.5
    if in_position:
        return closes[index] < mean
    return closes[index] < mean - 2.0 * deviation


def _ema_signal(closes: Sequence[float], index: int, in_position: bool) -> bool:
    fast = _ema_series(closes[: index + 1], 12)[-1]
    slow = _ema_series(closes[: index + 1], 26)[-1]
    return fast > slow


class StrategyResearchAgent:
    """Compares simple long-only candidates using chronological out-of-sample tests."""

    def __init__(
        self,
        *,
        fee_rate: float = 0.0026,
        slippage_rate: float = 0.0005,
        minimum_oos_trades: int = 3,
        maximum_oos_drawdown: float = 0.25,
    ):
        if not 0 <= fee_rate < 1 or not 0 <= slippage_rate < 1:
            raise ValueError("Fee and slippage rates must be in [0, 1)")
        if minimum_oos_trades < 1 or not 0 < maximum_oos_drawdown < 1:
            raise ValueError("Invalid strategy validation limits")
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.minimum_oos_trades = minimum_oos_trades
        self.maximum_oos_drawdown = maximum_oos_drawdown

    @staticmethod
    def _specs():
        return [
            ("ema_12_26", "Long-only EMA 12/26 trend filter", _ema_signal, 26),
            ("rsi_14_reversion", "RSI(14) oversold entry / recovery exit", _rsi_signal, 15),
            ("bollinger_20_reversion", "Bollinger(20,2) lower-band entry / mean exit", _bollinger_signal, 20),
        ]

    def _backtest(self, candles, signal, warmup, start, end):
        cash = 1.0
        units = 0.0
        entry_equity = 0.0
        closed_returns = []
        equity_curve = [1.0]
        fee_multiplier = 1.0 - self.fee_rate
        buy_multiplier = fee_multiplier * (1.0 - self.slippage_rate)
        sell_multiplier = fee_multiplier * (1.0 - self.slippage_rate)

        for index in range(max(warmup, start), end - 1):
            closes = [float(candle["close"]) for candle in candles[: index + 1]]
            desired = signal(closes, index, units > 0)
            execution_price = float(candles[index + 1]["open"])
            if execution_price <= 0:
                raise ValueError("Candle prices must be positive")

            if units > 0 and not desired:
                proceeds = units * execution_price * sell_multiplier
                closed_returns.append(proceeds / entry_equity - 1.0)
                cash = proceeds
                units = 0.0
                entry_equity = 0.0
            elif units == 0 and desired:
                entry_equity = cash
                units = cash * buy_multiplier / execution_price
                cash = 0.0

            marked = cash + units * float(candles[index + 1]["close"])
            equity_curve.append(marked)

        if units > 0:
            final_price = float(candles[end - 1]["close"])
            proceeds = units * final_price * sell_multiplier
            closed_returns.append(proceeds / entry_equity - 1.0)
            cash = proceeds
            units = 0.0
        equity_curve.append(cash)
        peak = equity_curve[0]
        max_drawdown = 0.0
        for equity in equity_curve:
            peak = max(peak, equity)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - equity) / peak)

        gains = sum(value for value in closed_returns if value > 0)
        losses = -sum(value for value in closed_returns if value < 0)
        profit_factor = gains / losses if losses > 0 else (float("inf") if gains > 0 else 0.0)
        return {
            "net_return": cash - 1.0,
            "max_drawdown": max_drawdown,
            "trades": len(closed_returns),
            "win_rate": (
                sum(value > 0 for value in closed_returns) / len(closed_returns)
                if closed_returns else 0.0
            ),
            "profit_factor": profit_factor,
        }

    def research(self, candles: Sequence[dict]) -> dict:
        if len(candles) < 120:
            raise ValueError("At least 120 candles are required for strategy research")
        normalized = []
        for candle in candles:
            row = {
                "timestamp": int(candle["timestamp"]),
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
                "volume": float(candle.get("volume", 0.0)),
            }
            if min(row["open"], row["high"], row["low"], row["close"]) <= 0:
                raise ValueError("Candle prices must be positive")
            if row["high"] < max(row["open"], row["close"], row["low"]):
                raise ValueError("Invalid candle high")
            if row["low"] > min(row["open"], row["close"], row["high"]):
                raise ValueError("Invalid candle low")
            normalized.append(row)

        split = int(len(normalized) * 0.7)
        results = []
        for strategy_id, description, signal, warmup in self._specs():
            in_sample = self._backtest(normalized, signal, warmup, warmup, split)
            out_sample = self._backtest(normalized, signal, warmup, split, len(normalized))
            eligible = (
                out_sample["trades"] >= self.minimum_oos_trades
                and out_sample["net_return"] > 0
                and out_sample["max_drawdown"] <= self.maximum_oos_drawdown
            )
            score = (
                out_sample["net_return"] - 0.5 * out_sample["max_drawdown"]
                if eligible else None
            )
            results.append(
                {
                    "strategy_id": strategy_id,
                    "description": description,
                    "in_sample": in_sample,
                    "out_of_sample": out_sample,
                    "eligible": eligible,
                    "score": score,
                }
            )
        eligible_results = [item for item in results if item["eligible"]]
        eligible_results.sort(key=lambda item: item["score"], reverse=True)
        return {
            "method": "chronological_70_30_next_bar_open_fills",
            "cost_model": {
                "fee_rate_per_side": self.fee_rate,
                "slippage_rate_per_side": self.slippage_rate,
            },
            "candidate_count": len(results),
            "eligible_count": len(eligible_results),
            "recommended_strategy": (
                eligible_results[0]["strategy_id"] if eligible_results else None
            ),
            "candidates": results,
            "execution": "research_only",
        }
