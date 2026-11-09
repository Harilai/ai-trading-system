from __future__ import annotations

from typing import Callable, Sequence
import math


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
        minimum_walk_forward_trades: int = 1,
        minimum_walk_forward_bars: int = 60,
        minimum_positive_walk_forward_windows: int = 2,
        maximum_oos_drawdown: float = 0.25,
        max_position_fraction: float = 0.25,
    ):
        if not 0 <= fee_rate < 1 or not 0 <= slippage_rate < 1:
            raise ValueError("Fee and slippage rates must be in [0, 1)")
        if (
            minimum_oos_trades < 1
            or minimum_walk_forward_trades < 1
            or minimum_walk_forward_bars < 2
            or not 1 <= minimum_positive_walk_forward_windows <= 3
            or not 0 < maximum_oos_drawdown < 1
        ):
            raise ValueError("Invalid strategy validation limits")
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.minimum_oos_trades = minimum_oos_trades
        self.minimum_walk_forward_trades = minimum_walk_forward_trades
        self.minimum_walk_forward_bars = minimum_walk_forward_bars
        self.minimum_positive_walk_forward_windows = minimum_positive_walk_forward_windows
        self.maximum_oos_drawdown = maximum_oos_drawdown
        if not 0 < max_position_fraction <= 1:
            raise ValueError("max_position_fraction must be in (0, 1]")
        self.max_position_fraction = max_position_fraction

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
                cash += proceeds
                units = 0.0
                entry_equity = 0.0
            elif units == 0 and desired:
                entry_equity = cash * self.max_position_fraction
                units = entry_equity * buy_multiplier / execution_price
                cash -= entry_equity

            marked = cash + units * float(candles[index + 1]["close"])
            equity_curve.append(marked)

        if units > 0:
            final_price = float(candles[end - 1]["close"])
            proceeds = units * final_price * sell_multiplier
            closed_returns.append(proceeds / entry_equity - 1.0)
            cash += proceeds
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
            "profit_factor": (profit_factor if math.isfinite(profit_factor) else None),
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
            numeric_values = (
                row["open"], row["high"], row["low"], row["close"], row["volume"]
            )
            if not all(math.isfinite(value) for value in numeric_values):
                raise ValueError("Candle OHLCV values must be finite")
            if row["timestamp"] <= 0:
                raise ValueError("Candle timestamp must be positive")
            if min(row["open"], row["high"], row["low"], row["close"]) <= 0:
                raise ValueError("Candle prices must be positive")
            if row["volume"] < 0:
                raise ValueError("Candle volume cannot be negative")
            if row["high"] < max(row["open"], row["close"], row["low"]):
                raise ValueError("Invalid candle high")
            if row["low"] > min(row["open"], row["close"], row["high"]):
                raise ValueError("Invalid candle low")
            if normalized and row["timestamp"] <= normalized[-1]["timestamp"]:
                raise ValueError("Candle timestamps must be strictly increasing")
            normalized.append(row)

        split = int(len(normalized) * 0.7)
        benchmark = self._backtest(
            normalized,
            lambda closes, index, in_position: True,
            0,
            split,
            len(normalized),
        )
        results = []
        walk_forward_boundaries = [
            (int(len(normalized) * 0.4), int(len(normalized) * 0.6)),
            (int(len(normalized) * 0.6), int(len(normalized) * 0.8)),
            (int(len(normalized) * 0.8), len(normalized)),
        ]
        for strategy_id, description, signal, warmup in self._specs():
            in_sample = self._backtest(normalized, signal, warmup, warmup, split)
            out_sample = self._backtest(normalized, signal, warmup, split, len(normalized))
            walk_forward_windows = [
                {
                    "window": index + 1,
                    "start_index": start,
                    "end_index": end,
                    "evaluation_bars": max(0, end - max(warmup, start) - 1),
                    **self._backtest(normalized, signal, warmup, start, end),
                }
                for index, (start, end) in enumerate(walk_forward_boundaries)
                if end - max(warmup, start) >= 2
            ]
            wf_returns = [window["net_return"] for window in walk_forward_windows]
            walk_forward = {
                "method": "three_expanding-history_out_of_sample_windows",
                "window_count": len(walk_forward_windows),
                "positive_windows": sum(value > 0 for value in wf_returns),
                "mean_net_return": sum(wf_returns) / len(wf_returns) if wf_returns else 0.0,
                "worst_net_return": min(wf_returns) if wf_returns else 0.0,
                "max_drawdown": max(
                    (window["max_drawdown"] for window in walk_forward_windows),
                    default=0.0,
                ),
                "windows": walk_forward_windows,
            }
            benchmark_excess_return = out_sample["net_return"] - benchmark["net_return"]
            validation_failures = []
            if out_sample["trades"] < self.minimum_oos_trades:
                validation_failures.append("insufficient_oos_trades")
            if out_sample["net_return"] <= 0:
                validation_failures.append("non_positive_oos_return")
            if out_sample["max_drawdown"] > self.maximum_oos_drawdown:
                validation_failures.append("oos_drawdown_exceeded")
            if len(walk_forward_windows) != 3:
                validation_failures.append("incomplete_walk_forward_windows")
            if any(
                window["evaluation_bars"] < self.minimum_walk_forward_bars
                for window in walk_forward_windows
            ):
                validation_failures.append("insufficient_walk_forward_history")
            if any(
                window["trades"] < self.minimum_walk_forward_trades
                for window in walk_forward_windows
            ):
                validation_failures.append("insufficient_walk_forward_trades")
            if walk_forward["positive_windows"] < self.minimum_positive_walk_forward_windows:
                validation_failures.append("insufficient_positive_walk_forward_windows")
            if walk_forward["mean_net_return"] <= 0:
                validation_failures.append("non_positive_walk_forward_mean")
            if walk_forward["max_drawdown"] > self.maximum_oos_drawdown:
                validation_failures.append("walk_forward_drawdown_exceeded")
            if benchmark_excess_return <= 0:
                validation_failures.append("does_not_beat_buy_and_hold")
            eligible = not validation_failures
            score = (
                benchmark_excess_return
                + out_sample["net_return"]
                - 0.5 * max(out_sample["max_drawdown"], walk_forward["max_drawdown"])
                if eligible else None
            )
            current_index = len(normalized) - 1
            current_closes = [row["close"] for row in normalized]
            results.append(
                {
                    "strategy_id": strategy_id,
                    "description": description,
                    "in_sample": in_sample,
                    "out_of_sample": out_sample,
                    "benchmark_excess_return": benchmark_excess_return,
                    "beats_buy_and_hold": benchmark_excess_return > 0,
                    "walk_forward": walk_forward,
                    "eligible": eligible,
                    "validation_failures": validation_failures,
                    "score": score,
                    "current_entry_signal": signal(current_closes, current_index, False),
                    "current_hold_signal": signal(current_closes, current_index, True),
                }
            )
        eligible_results = [item for item in results if item["eligible"]]
        eligible_results.sort(key=lambda item: item["score"], reverse=True)
        return {
            "method": "chronological_70_30_plus_three_walk_forward_diagnostics_next_bar_open_fills",
            "cost_model": {
                "fee_rate_per_side": self.fee_rate,
                "slippage_rate_per_side": self.slippage_rate,
                "max_position_fraction": self.max_position_fraction,
            },
            "promotion_gates": {
                "minimum_oos_trades": self.minimum_oos_trades,
                "minimum_walk_forward_trades_per_window": self.minimum_walk_forward_trades,
                "minimum_walk_forward_bars_per_window": self.minimum_walk_forward_bars,
                "minimum_positive_walk_forward_windows": self.minimum_positive_walk_forward_windows,
                "maximum_drawdown": self.maximum_oos_drawdown,
                "must_beat_buy_and_hold": True,
            },
            "benchmark": {
                "name": "buy_and_hold",
                "out_of_sample": benchmark,
                "position_fraction": self.max_position_fraction,
            },
            "candidate_count": len(results),
            "eligible_count": len(eligible_results),
            "recommended_strategy": (
                eligible_results[0]["strategy_id"] if eligible_results else None
            ),
            "candidates": results,
            "execution": "research_only",
        }
