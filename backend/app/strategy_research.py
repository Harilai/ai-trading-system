from __future__ import annotations

from typing import Callable, Sequence
from statistics import median
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


def _market_regime(closes: Sequence[float]) -> dict[str, float | str]:
    """Describe recent directionality and volatility without controlling execution."""
    if len(closes) < 21:
        return {"status": "insufficient_history", "method": "efficiency_ratio_and_rolling_volatility"}
    recent = [float(value) for value in closes[-21:]]
    changes = [current - previous for previous, current in zip(recent[:-1], recent[1:])]
    path_length = sum(abs(change) for change in changes)
    efficiency = abs(recent[-1] - recent[0]) / path_length if path_length > 0 else 0.0
    if efficiency >= 0.6:
        structure = "trending_up" if recent[-1] > recent[0] else "trending_down"
    else:
        structure = "range"

    returns = [
        math.log(current / previous)
        for previous, current in zip(closes[:-1], closes[1:])
    ]
    rolling_volatility = []
    for end in range(20, len(returns) + 1):
        window = returns[end - 20 : end]
        mean = sum(window) / 20
        variance = sum((value - mean) ** 2 for value in window) / 20
        rolling_volatility.append(math.sqrt(variance))
    current_volatility = rolling_volatility[-1]
    if max(rolling_volatility, default=0.0) <= 1e-12:
        volatility_state = "low"
        percentile = 0.0
    else:
        less = sum(value < current_volatility for value in rolling_volatility)
        equal = sum(value == current_volatility for value in rolling_volatility)
        percentile = (less + 0.5 * equal) / len(rolling_volatility)
        if percentile >= 0.8:
            volatility_state = "high"
        elif percentile <= 0.2:
            volatility_state = "low"
        else:
            volatility_state = "normal"
    return {
        "status": "available",
        "method": "20_bar_efficiency_ratio_and_rolling_log_return_volatility_percentile",
        "structure": structure,
        "volatility": volatility_state,
        "trend_efficiency_ratio": efficiency,
        "realized_volatility_20": current_volatility,
        "volatility_percentile": percentile,
    }


def _candle_data_quality(
    candles: Sequence[dict], *, expected_interval_seconds: float | None = None,
    calendar_model: str = "unspecified",
) -> dict:
    """Summarize coverage; fixed cadence requires an explicit venue model."""
    timestamps = [int(candle["timestamp"]) for candle in candles]
    intervals_ms = [
        current - previous
        for previous, current in zip(timestamps[:-1], timestamps[1:])
    ]
    median_interval_ms = median(intervals_ms) if intervals_ms else None
    if expected_interval_seconds is not None:
        if not isinstance(expected_interval_seconds, (int, float)) or expected_interval_seconds <= 0:
            raise ValueError("expected_interval_seconds must be positive")
        expected_interval_ms = expected_interval_seconds * 1000
        gap_intervals = [interval for interval in intervals_ms if interval > expected_interval_ms * 1.5]
        cadence_source = "explicit_expected_interval"
        estimated_missing_intervals = sum(
            max(0, round(interval / expected_interval_ms) - 1)
            for interval in gap_intervals
        )
    else:
        gap_intervals = (
            [interval for interval in intervals_ms if interval > median_interval_ms * 1.5]
            if median_interval_ms is not None else []
        )
        cadence_source = "median_observed_interval_heuristic"
        estimated_missing_intervals = sum(
            max(0, round(interval / median_interval_ms) - 1)
            for interval in gap_intervals
        ) if median_interval_ms else 0
    history_span_seconds = (
        (timestamps[-1] - timestamps[0]) / 1000.0 if len(timestamps) > 1 else 0.0
    )
    return {
        "status": "available" if len(timestamps) > 1 else "insufficient_history",
        "bars": len(timestamps),
        "history_span_seconds": history_span_seconds,
        "history_span_days": history_span_seconds / 86400.0,
        "median_candle_interval_seconds": (
            median_interval_ms / 1000.0 if median_interval_ms is not None else None
        ),
        "maximum_candle_interval_seconds": (
            max(intervals_ms) / 1000.0 if intervals_ms else None
        ),
        "gap_count": len(gap_intervals),
        "largest_gap_seconds": (
            max(gap_intervals) / 1000.0 if gap_intervals else 0.0
        ),
        "gap_detection_threshold_multiplier": 1.5,
        "gap_detection_method": (
            "intervals_above_1_5x_expected_cadence"
            if expected_interval_seconds is not None
            else "intervals_above_1_5x_median_cadence"
        ),
        "expected_candle_interval_seconds": expected_interval_seconds,
        "cadence_source": cadence_source,
        "estimated_missing_intervals": estimated_missing_intervals,
        "calendar_model": calendar_model,
        "calendar_adjustment": False,
    }


_VALIDATION_FAILURE_CATEGORIES = {
    "insufficient_oos_trades": "sample",
    "incomplete_walk_forward_windows": "sample",
    "insufficient_walk_forward_history": "sample",
    "insufficient_walk_forward_trades": "sample",
    "non_positive_oos_return": "performance",
    "insufficient_positive_walk_forward_windows": "performance",
    "non_positive_walk_forward_mean": "performance",
    "does_not_beat_buy_and_hold": "performance",
    "oos_drawdown_exceeded": "risk",
    "walk_forward_drawdown_exceeded": "risk",
    "fails_1_5x_cost_stress": "cost",
}


def _categorize_validation_failures(failures: Sequence[str]) -> dict[str, list[str]]:
    categories: dict[str, list[str]] = {}
    for failure in failures:
        category = _VALIDATION_FAILURE_CATEGORIES.get(failure, "other")
        categories.setdefault(category, []).append(failure)
    return categories


class StrategyResearchAgent:
    """Compares simple long-only candidates using chronological out-of-sample tests."""

    def __init__(
        self,
        *,
        fee_rate: float = 0.0026,
        slippage_rate: float = 0.0005,
        minimum_oos_trades: int = 3,
        minimum_walk_forward_trades: int = 1,
        minimum_walk_forward_bars: int = 40,
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
    def _passes_cost_stress(cost_sensitivity: dict, multiplier: str = "1.5") -> bool:
        scenario = cost_sensitivity.get(multiplier)
        if not isinstance(scenario, dict) or not scenario.get("available"):
            return False
        stressed_result = scenario.get("out_of_sample") or {}
        return (
            stressed_result.get("net_return", 0.0) > 0
            and scenario.get("beats_buy_and_hold") is True
        )

    @staticmethod
    def _specs():
        return [
            ("ema_12_26", "Long-only EMA 12/26 trend filter", _ema_signal, 26),
            ("rsi_14_reversion", "RSI(14) oversold entry / recovery exit", _rsi_signal, 15),
            ("bollinger_20_reversion", "Bollinger(20,2) lower-band entry / mean exit", _bollinger_signal, 20),
        ]

    def _backtest(self, candles, signal, warmup, start, end, *, fee_rate=None, slippage_rate=None):
        cash = 1.0
        units = 0.0
        entry_equity = 0.0
        entry_index = None
        closed_returns = []
        trade_durations = []
        equity_curve = [1.0]
        evaluated_bars = 0
        exposure_bars = 0
        effective_fee_rate = self.fee_rate if fee_rate is None else fee_rate
        effective_slippage_rate = (
            self.slippage_rate if slippage_rate is None else slippage_rate
        )
        fee_multiplier = 1.0 - effective_fee_rate
        buy_multiplier = fee_multiplier * (1.0 - effective_slippage_rate)
        sell_multiplier = fee_multiplier * (1.0 - effective_slippage_rate)

        for index in range(max(warmup, start), end - 1):
            closes = [float(candle["close"]) for candle in candles[: index + 1]]
            desired = signal(closes, index, units > 0)
            execution_price = float(candles[index + 1]["open"])
            if execution_price <= 0:
                raise ValueError("Candle prices must be positive")

            if units > 0 and not desired:
                proceeds = units * execution_price * sell_multiplier
                closed_returns.append(proceeds / entry_equity - 1.0)
                trade_durations.append(index + 1 - entry_index)
                cash += proceeds
                units = 0.0
                entry_equity = 0.0
                entry_index = None
            elif units == 0 and desired:
                entry_equity = cash * self.max_position_fraction
                units = entry_equity * buy_multiplier / execution_price
                cash -= entry_equity
                entry_index = index + 1

            evaluated_bars += 1
            if units > 0:
                exposure_bars += 1

            marked = cash + units * float(candles[index + 1]["close"])
            equity_curve.append(marked)

        if units > 0:
            final_price = float(candles[end - 1]["close"])
            proceeds = units * final_price * sell_multiplier
            closed_returns.append(proceeds / entry_equity - 1.0)
            trade_durations.append(end - 1 - entry_index)
            cash += proceeds
            units = 0.0
            # Replace the final mark with liquidation value. Appending a second
            # point here would create a fictitious extra return period.
            if len(equity_curve) > 1:
                equity_curve[-1] = cash
            else:
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
        period_returns = [
            current / previous - 1.0
            for previous, current in zip(equity_curve[:-1], equity_curve[1:])
            if previous > 0
        ]
        mean_period_return = (
            sum(period_returns) / len(period_returns) if period_returns else 0.0
        )
        return_variance = (
            sum((value - mean_period_return) ** 2 for value in period_returns)
            / len(period_returns)
            if period_returns else 0.0
        )
        return_volatility = math.sqrt(return_variance)
        downside_deviation = (
            math.sqrt(
                sum(min(value, 0.0) ** 2 for value in period_returns)
                / len(period_returns)
            )
            if period_returns else 0.0
        )
        winning_trades = [value for value in closed_returns if value > 0]
        losing_trades = [value for value in closed_returns if value < 0]
        average_win = sum(winning_trades) / len(winning_trades) if winning_trades else None
        average_loss = sum(losing_trades) / len(losing_trades) if losing_trades else None
        average_trade_return = (
            sum(closed_returns) / len(closed_returns) if closed_returns else None
        )
        payoff_ratio = (
            average_win / abs(average_loss)
            if average_win is not None and average_loss is not None and average_loss != 0
            else None
        )
        return {
            "net_return": cash - 1.0,
            "max_drawdown": max_drawdown,
            "trades": len(closed_returns),
            "win_rate": (
                len(winning_trades) / len(closed_returns) if closed_returns else 0.0
            ),
            "profit_factor": (profit_factor if math.isfinite(profit_factor) else None),
            "evaluation_bars": evaluated_bars,
            "exposure_bars": exposure_bars,
            "exposure_fraction": (
                exposure_bars / evaluated_bars if evaluated_bars else 0.0
            ),
            "average_trade_duration_bars": (
                sum(trade_durations) / len(trade_durations) if trade_durations else None
            ),
            "average_trade_return": average_trade_return,
            "average_win_return": average_win,
            "average_loss_return": average_loss,
            "payoff_ratio": payoff_ratio,
            "return_observations": len(period_returns),
            "mean_return_per_bar": mean_period_return,
            "return_volatility_per_bar": return_volatility,
            "sharpe_per_bar": (
                mean_period_return / return_volatility if return_volatility > 0 else None
            ),
            "sortino_per_bar": (
                mean_period_return / downside_deviation
                if downside_deviation > 0 else None
            ),
        }

    def research(
        self, candles: Sequence[dict], *, expected_interval_seconds: float | None = None,
        calendar_model: str = "unspecified",
    ) -> dict:
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

        if len(normalized) < 120:
            raise ValueError("At least 120 candles are required for strategy research")

        split = int(len(normalized) * 0.7)
        benchmark = self._backtest(
            normalized,
            lambda closes, index, in_position: True,
            0,
            split,
            len(normalized),
        )
        results = []
        data_quality = _candle_data_quality(
            normalized, expected_interval_seconds=expected_interval_seconds,
            calendar_model=calendar_model,
        )
        market_regime = _market_regime([row["close"] for row in normalized])
        stress_multipliers = (1.5, 2.0)
        benchmark_cost_sensitivity = {}
        for multiplier in stress_multipliers:
            stressed_fee = self.fee_rate * multiplier
            stressed_slippage = self.slippage_rate * multiplier
            if stressed_fee >= 1 or stressed_slippage >= 1:
                benchmark_cost_sensitivity[str(multiplier)] = {
                    "available": False,
                    "reason": "stressed_cost_rate_out_of_range",
                }
                continue
            benchmark_cost_sensitivity[str(multiplier)] = {
                "available": True,
                "fee_rate_per_side": stressed_fee,
                "slippage_rate_per_side": stressed_slippage,
                "out_of_sample": self._backtest(
                    normalized,
                    lambda closes, index, in_position: True,
                    0,
                    split,
                    len(normalized),
                    fee_rate=stressed_fee,
                    slippage_rate=stressed_slippage,
                ),
            }
        walk_forward_boundaries = [
            (int(len(normalized) * 0.4), int(len(normalized) * 0.6)),
            (int(len(normalized) * 0.6), int(len(normalized) * 0.8)),
            (int(len(normalized) * 0.8), len(normalized)),
        ]
        walk_forward_evaluation_bars = [
            max(0, end - start - 1)
            for start, end in walk_forward_boundaries
        ]
        sample_adequacy = {
            "status": (
                "adequate_for_configured_walk_forward_gates"
                if len(walk_forward_evaluation_bars) == 3
                and all(value >= self.minimum_walk_forward_bars for value in walk_forward_evaluation_bars)
                else "insufficient_for_configured_walk_forward_gates"
            ),
            "available_bars": len(normalized),
            "minimum_research_bars": 120,
            "out_of_sample_bars": len(normalized) - split,
            "walk_forward_evaluation_bars": walk_forward_evaluation_bars,
            "minimum_walk_forward_bars_per_window": self.minimum_walk_forward_bars,
        }
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
            cost_sensitivity = {}
            for multiplier in stress_multipliers:
                stressed_benchmark = benchmark_cost_sensitivity[str(multiplier)]
                stressed_fee = self.fee_rate * multiplier
                stressed_slippage = self.slippage_rate * multiplier
                if not stressed_benchmark["available"] or stressed_fee >= 1 or stressed_slippage >= 1:
                    cost_sensitivity[str(multiplier)] = {
                        "available": False,
                        "reason": "stressed_cost_rate_out_of_range",
                    }
                    continue
                stressed_result = self._backtest(
                    normalized,
                    signal,
                    warmup,
                    split,
                    len(normalized),
                    fee_rate=stressed_fee,
                    slippage_rate=stressed_slippage,
                )
                stressed_excess = (
                    stressed_result["net_return"]
                    - stressed_benchmark["out_of_sample"]["net_return"]
                )
                cost_sensitivity[str(multiplier)] = {
                    "available": True,
                    "fee_rate_per_side": stressed_fee,
                    "slippage_rate_per_side": stressed_slippage,
                    "out_of_sample": stressed_result,
                    "benchmark_excess_return": stressed_excess,
                    "beats_buy_and_hold": stressed_excess > 0,
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
            if not self._passes_cost_stress(cost_sensitivity, "1.5"):
                validation_failures.append("fails_1_5x_cost_stress")
            eligible = not validation_failures

            # Experimental paper eligibility is deliberately separate from
            # promotion. It checks data/history and drawdown bounds, but does
            # not claim profitability or require benchmark outperformance.
            experimental_validation_failures = []
            if len(walk_forward_windows) != 3:
                experimental_validation_failures.append("incomplete_walk_forward_windows")
            if any(
                window["evaluation_bars"] < self.minimum_walk_forward_bars
                for window in walk_forward_windows
            ):
                experimental_validation_failures.append("insufficient_walk_forward_history")
            if out_sample["max_drawdown"] > self.maximum_oos_drawdown:
                experimental_validation_failures.append("oos_drawdown_exceeded")
            if walk_forward["max_drawdown"] > self.maximum_oos_drawdown:
                experimental_validation_failures.append("walk_forward_drawdown_exceeded")
            experimental_eligible = not experimental_validation_failures
            experimental_score = (
                out_sample["net_return"]
                - 0.5 * max(out_sample["max_drawdown"], walk_forward["max_drawdown"])
                if experimental_eligible else None
            )

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
                    "cost_sensitivity": cost_sensitivity,
                    "eligible": eligible,
                    "validation_failures": validation_failures,
                    "validation_failure_categories": _categorize_validation_failures(validation_failures),
                    "score": score,
                    "experimental_eligible": experimental_eligible,
                    "experimental_validation_failures": experimental_validation_failures,
                    "experimental_validation_failure_categories": _categorize_validation_failures(experimental_validation_failures),
                    "experimental_score": experimental_score,
                    "current_entry_signal": signal(current_closes, current_index, False),
                    "current_hold_signal": signal(current_closes, current_index, True),
                }
            )
        eligible_results = [item for item in results if item["eligible"]]
        eligible_results.sort(key=lambda item: item["score"], reverse=True)
        experimental_results = [item for item in results if item["experimental_eligible"]]
        experimental_results.sort(key=lambda item: item["experimental_score"], reverse=True)
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
                "must_remain_profitable_and_beat_buy_and_hold_at_1_5x_costs": True,
            },
            "status": "available",
            "market_regime": market_regime,
            "sample_adequacy": sample_adequacy,
            "failure_summary": {
                category: sum(
                    1 for item in results
                    if item["validation_failure_categories"].get(category)
                )
                for category in ("sample", "performance", "risk", "cost", "other")
            },
            "data_quality": {
                **data_quality,
                "out_of_sample_bars": len(normalized) - split,
                "walk_forward_evaluation_bars": walk_forward_evaluation_bars,
            },
            "benchmark": {
                "name": "buy_and_hold",
                "out_of_sample": benchmark,
                "position_fraction": self.max_position_fraction,
            },
            "benchmark_cost_sensitivity": benchmark_cost_sensitivity,
            "candidate_count": len(results),
            "eligible_count": len(eligible_results),
            "recommended_strategy": (
                eligible_results[0]["strategy_id"] if eligible_results else None
            ),
            "experimental_candidate_count": len(experimental_results),
            "recommended_experimental_strategy": (
                experimental_results[0]["strategy_id"] if experimental_results else None
            ),
            "experimental_promotion_note": (
                "Experimental candidates are paper-only and are not validated for profitability; "
                "they may underperform buy-and-hold or lose money."
            ),
            "candidates": results,
            "execution": "research_only",
        }
