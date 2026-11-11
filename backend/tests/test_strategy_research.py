import math

import pytest

from app.strategy_research import StrategyResearchAgent


def candles_from_closes(closes):
    return [
        {
            "timestamp": 1_700_000_000_000 + index * 3_600_000,
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1000,
        }
        for index, close in enumerate(closes)
    ]


def test_research_requires_sufficient_history():
    with pytest.raises(ValueError, match="120"):
        StrategyResearchAgent().research(candles_from_closes([100.0] * 119))


def test_research_returns_chronological_out_of_sample_results():
    closes = [100 + index * 0.05 + 4 * math.sin(index / 5) for index in range(240)]
    result = StrategyResearchAgent(minimum_oos_trades=1).research(candles_from_closes(closes))
    assert result["method"] == "chronological_70_30_plus_three_walk_forward_diagnostics_next_bar_open_fills"
    assert result["benchmark"]["name"] == "buy_and_hold"
    assert result["benchmark"]["out_of_sample"]["trades"] == 1
    assert result["candidate_count"] == 3
    assert len(result["candidates"]) == 3
    assert all("out_of_sample" in item for item in result["candidates"])
    assert all("benchmark_excess_return" in item for item in result["candidates"])
    assert all(isinstance(item["beats_buy_and_hold"], bool) for item in result["candidates"])
    assert all(item["walk_forward"]["window_count"] == 3 for item in result["candidates"])
    assert all(
        [window["window"] for window in item["walk_forward"]["windows"]] == [1, 2, 3]
        for item in result["candidates"]
    )
    assert all(
        item["walk_forward"]["method"] == "three_expanding-history_out_of_sample_windows"
        for item in result["candidates"]
    )
    assert result["execution"] == "research_only"
    assert all(isinstance(item["current_entry_signal"], bool) for item in result["candidates"])
    assert all(isinstance(item["current_hold_signal"], bool) for item in result["candidates"])
    assert result["cost_model"]["max_position_fraction"] == pytest.approx(0.25)


def test_research_rejects_invalid_candle_prices():
    rows = candles_from_closes([100.0] * 120)
    rows[10]["low"] = 0
    with pytest.raises(ValueError, match="positive"):
        StrategyResearchAgent().research(rows)


def test_research_does_not_select_strategy_without_enough_oos_trades():
    closes = [100 + index * 0.01 for index in range(150)]
    result = StrategyResearchAgent(minimum_oos_trades=10).research(candles_from_closes(closes))
    assert result["recommended_strategy"] is None
    assert result["eligible_count"] == 0


def test_research_rejects_invalid_position_fraction():
    with pytest.raises(ValueError, match="max_position_fraction"):
        StrategyResearchAgent(max_position_fraction=1.5)



def test_walk_forward_windows_are_chronological_and_do_not_overlap():
    closes = [100 + index * 0.1 + 3 * math.sin(index / 7) for index in range(300)]
    result = StrategyResearchAgent(minimum_oos_trades=1).research(candles_from_closes(closes))
    windows = result["candidates"][0]["walk_forward"]["windows"]
    assert len(windows) == 3
    assert windows[0]["end_index"] == windows[1]["start_index"]
    assert windows[1]["end_index"] == windows[2]["start_index"]
    assert windows[0]["start_index"] < windows[1]["start_index"] < windows[2]["start_index"]
    assert all("net_return" in window and "max_drawdown" in window for window in windows)


def test_research_rejects_non_chronological_or_duplicate_candles():
    rows = candles_from_closes([100.0 + index for index in range(120)])
    rows[20]["timestamp"] = rows[19]["timestamp"]
    with pytest.raises(ValueError, match="strictly increasing"):
        StrategyResearchAgent().research(rows)

    rows = candles_from_closes([100.0 + index for index in range(120)])
    rows[20], rows[21] = rows[21], rows[20]
    with pytest.raises(ValueError, match="strictly increasing"):
        StrategyResearchAgent().research(rows)


def test_research_rejects_non_finite_or_negative_ohlcv_values():
    rows = candles_from_closes([100.0] * 120)
    rows[30]["close"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        StrategyResearchAgent().research(rows)

    rows = candles_from_closes([100.0] * 120)
    rows[30]["volume"] = -1
    with pytest.raises(ValueError, match="volume cannot be negative"):
        StrategyResearchAgent().research(rows)



def test_walk_forward_evidence_is_a_mandatory_promotion_gate():
    closes = [100 + index * 0.05 + 4 * math.sin(index / 5) for index in range(300)]
    result = StrategyResearchAgent(
        minimum_oos_trades=1,
        minimum_walk_forward_bars=60,
    ).research(candles_from_closes(closes))

    assert result["promotion_gates"]["must_beat_buy_and_hold"] is True
    assert result["promotion_gates"]["minimum_walk_forward_bars_per_window"] == 60
    assert result["eligible_count"] == 0
    assert result["recommended_strategy"] is None
    assert all(not item["eligible"] for item in result["candidates"])
    assert all(
        "insufficient_walk_forward_history" in item["validation_failures"]
        for item in result["candidates"]
    )


def test_candidate_reports_each_failed_promotion_gate():
    closes = [100 + index * 0.01 for index in range(180)]
    result = StrategyResearchAgent().research(candles_from_closes(closes))

    for candidate in result["candidates"]:
        assert isinstance(candidate["validation_failures"], list)
        assert candidate["eligible"] is (not candidate["validation_failures"])
        assert candidate["score"] is None



def test_research_reports_fee_and_slippage_stress_scenarios():
    closes = [100 + index * 0.03 + 2 * math.sin(index / 6) for index in range(300)]
    result = StrategyResearchAgent(minimum_oos_trades=1).research(candles_from_closes(closes))

    base_return = result["benchmark"]["out_of_sample"]["net_return"]
    stress = result["benchmark_cost_sensitivity"]
    assert stress["1.5"]["available"] is True
    assert stress["2.0"]["available"] is True
    assert stress["1.5"]["fee_rate_per_side"] > result["cost_model"]["fee_rate_per_side"]
    assert stress["2.0"]["slippage_rate_per_side"] > result["cost_model"]["slippage_rate_per_side"]
    assert stress["1.5"]["out_of_sample"]["net_return"] <= base_return
    assert stress["2.0"]["out_of_sample"]["net_return"] <= stress["1.5"]["out_of_sample"]["net_return"]
    assert all(
        set(candidate["cost_sensitivity"]) == {"1.5", "2.0"}
        for candidate in result["candidates"]
    )
    assert all(
        candidate["cost_sensitivity"]["1.5"]["available"]
        for candidate in result["candidates"]
    )



def test_default_walk_forward_gate_is_compatible_with_250_candle_scans():
    closes = [100 + index * 0.03 + 2 * math.sin(index / 6) for index in range(250)]
    result = StrategyResearchAgent().research(candles_from_closes(closes))

    assert result["promotion_gates"]["minimum_walk_forward_bars_per_window"] == 40
    for candidate in result["candidates"]:
        assert all(
            window["evaluation_bars"] >= 40
            for window in candidate["walk_forward"]["windows"]
        )
        assert "insufficient_walk_forward_history" not in candidate["validation_failures"]



def test_cost_stress_gate_requires_positive_return_and_benchmark_outperformance():
    passes = {
        "1.5": {
            "available": True,
            "out_of_sample": {"net_return": 0.01},
            "beats_buy_and_hold": True,
        }
    }
    assert StrategyResearchAgent._passes_cost_stress(passes) is True

    negative_return = {
        "1.5": {
            "available": True,
            "out_of_sample": {"net_return": -0.001},
            "beats_buy_and_hold": True,
        }
    }
    assert StrategyResearchAgent._passes_cost_stress(negative_return) is False

    underperforms = {
        "1.5": {
            "available": True,
            "out_of_sample": {"net_return": 0.01},
            "beats_buy_and_hold": False,
        }
    }
    assert StrategyResearchAgent._passes_cost_stress(underperforms) is False
    assert StrategyResearchAgent._passes_cost_stress({}) is False


def test_cost_stress_is_declared_as_a_mandatory_promotion_gate():
    closes = [100 + index * 0.03 + 2 * math.sin(index / 6) for index in range(300)]
    result = StrategyResearchAgent(minimum_oos_trades=1).research(candles_from_closes(closes))

    assert result["promotion_gates"][
        "must_remain_profitable_and_beat_buy_and_hold_at_1_5x_costs"
    ] is True
    for candidate in result["candidates"]:
        stress = candidate["cost_sensitivity"]["1.5"]
        assert "fails_1_5x_cost_stress" in candidate["validation_failures"] or (
            stress["available"]
            and stress["out_of_sample"]["net_return"] > 0
            and stress["beats_buy_and_hold"]
        )



def test_market_regime_detects_directional_trend_and_range():
    upward = [100.0 + index for index in range(120)]
    downward = list(reversed(upward))
    ranging = [100.0 + 5.0 * math.sin(index * math.pi / 5) for index in range(120)]

    up_result = StrategyResearchAgent().research(candles_from_closes(upward))
    down_result = StrategyResearchAgent().research(candles_from_closes(downward))
    range_result = StrategyResearchAgent().research(candles_from_closes(ranging))

    assert up_result["market_regime"]["structure"] == "trending_up"
    assert down_result["market_regime"]["structure"] == "trending_down"
    assert range_result["market_regime"]["structure"] == "range"
    assert all(
        result["market_regime"]["status"] == "available"
        for result in (up_result, down_result, range_result)
    )


def test_market_regime_volatility_percentile_is_tie_aware_and_detects_spikes():
    stable = [100.0 * (1.001 ** index) for index in range(140)]
    stable_result = StrategyResearchAgent().research(candles_from_closes(stable))
    assert stable_result["market_regime"]["volatility"] == "low"
    assert stable_result["market_regime"]["volatility_percentile"] <= 0.8

    volatile = list(stable)
    price = volatile[-1]
    for index in range(20):
        price *= 1.08 if index % 2 == 0 else 0.92
        volatile.append(price)
    volatile_result = StrategyResearchAgent().research(candles_from_closes(volatile))
    assert volatile_result["market_regime"]["volatility"] == "high"
    assert volatile_result["market_regime"]["volatility_percentile"] >= 0.8


def test_market_regime_is_diagnostic_and_does_not_bypass_promotion_gates():
    closes = [100.0 + index * 0.01 for index in range(180)]
    result = StrategyResearchAgent().research(candles_from_closes(closes))

    assert result["market_regime"]["status"] == "available"
    assert "market_regime" not in result["promotion_gates"]
    assert result["execution"] == "research_only"
    assert result["eligible_count"] == 0



def test_backtest_reports_trade_expectancy_exposure_and_duration():
    candles = candles_from_closes([100.0, 110.0, 105.0, 105.0, 105.0])
    result = StrategyResearchAgent()._backtest(
        candles,
        lambda closes, index, in_position: index == 0 and not in_position,
        0,
        0,
        len(candles),
    )

    assert result["trades"] == 1
    assert result["evaluation_bars"] == 4
    assert result["exposure_bars"] == 1
    assert result["exposure_fraction"] == pytest.approx(0.25)
    assert result["average_trade_duration_bars"] == pytest.approx(1.0)
    assert result["average_trade_return"] < 0
    assert result["average_loss_return"] == pytest.approx(result["average_trade_return"])
    assert result["average_win_return"] is None
    assert result["payoff_ratio"] is None
    assert result["return_observations"] == result["evaluation_bars"]


def test_backtest_uses_no_extra_return_period_for_final_liquidation():
    candles = candles_from_closes([100.0, 100.0, 101.0, 102.0, 103.0])
    result = StrategyResearchAgent()._backtest(
        candles,
        lambda closes, index, in_position: True,
        0,
        0,
        len(candles),
    )

    assert result["trades"] == 1
    assert result["evaluation_bars"] == 4
    assert result["return_observations"] == 4
    assert result["average_trade_duration_bars"] == pytest.approx(3.0)


def test_backtest_reports_undefined_risk_adjusted_metrics_without_variation():
    candles = candles_from_closes([100.0] * 5)
    result = StrategyResearchAgent()._backtest(
        candles,
        lambda closes, index, in_position: False,
        0,
        0,
        len(candles),
    )

    assert result["trades"] == 0
    assert result["exposure_fraction"] == 0
    assert result["average_trade_return"] is None
    assert result["payoff_ratio"] is None
    assert result["return_volatility_per_bar"] == 0
    assert result["sharpe_per_bar"] is None
    assert result["sortino_per_bar"] is None


def test_research_reports_candle_history_depth_and_regular_cadence():
    closes = [100.0 + index * 0.01 for index in range(240)]
    result = StrategyResearchAgent().research(candles_from_closes(closes))
    quality = result["data_quality"]

    assert quality["status"] == "available"
    assert quality["bars"] == 240
    assert quality["history_span_days"] == pytest.approx(239 / 24)
    assert quality["median_candle_interval_seconds"] == 3600
    assert quality["maximum_candle_interval_seconds"] == 3600
    assert quality["gap_count"] == 0
    assert quality["largest_gap_seconds"] == 0
    assert quality["out_of_sample_bars"] == 72
    assert len(quality["walk_forward_evaluation_bars"]) == 3


def test_research_detects_missing_candle_intervals_without_reordering_data():
    rows = candles_from_closes([100.0 + index * 0.01 for index in range(240)])
    rows = rows[:120] + rows[121:]
    result = StrategyResearchAgent().research(rows)
    quality = result["data_quality"]

    assert quality["bars"] == 239
    assert quality["median_candle_interval_seconds"] == 3600
    assert quality["gap_count"] == 1
    assert quality["largest_gap_seconds"] == 7200
    assert quality["calendar_adjustment"] is False
    assert quality["cadence_source"] == "median_observed_interval_heuristic"
    assert quality["estimated_missing_intervals"] == 1


def test_research_uses_explicit_expected_cadence_for_gap_diagnostics():
    rows = candles_from_closes([100.0 + index * 0.01 for index in range(240)])
    rows = rows[:120] + rows[122:]
    result = StrategyResearchAgent().research(
        rows, expected_interval_seconds=3600, calendar_model="crypto_24x7",
    )
    quality = result["data_quality"]
    assert quality["gap_count"] == 1
    assert quality["largest_gap_seconds"] == 10800
    assert quality["expected_candle_interval_seconds"] == 3600
    assert quality["cadence_source"] == "explicit_expected_interval"
    assert quality["estimated_missing_intervals"] == 2
    assert quality["calendar_model"] == "crypto_24x7"
    assert quality["calendar_adjustment"] is False


def test_research_rejects_invalid_expected_cadence():
    rows = candles_from_closes([100.0 + index * 0.01 for index in range(120)])
    with pytest.raises(ValueError, match="expected_interval_seconds"):
        StrategyResearchAgent().research(rows, expected_interval_seconds=0)


def test_research_reports_sample_adequacy_and_categorized_failure_reasons():
    closes = [100 + index * 0.01 for index in range(250)]
    result = StrategyResearchAgent().research(candles_from_closes(closes))

    assert result["sample_adequacy"] == {
        "status": "adequate_for_configured_walk_forward_gates",
        "available_bars": 250,
        "minimum_research_bars": 120,
        "out_of_sample_bars": 75,
        "walk_forward_evaluation_bars": [49, 49, 49],
        "minimum_walk_forward_bars_per_window": 40,
    }
    for candidate in result["candidates"]:
        categories = candidate["validation_failure_categories"]
        for failure in candidate["validation_failures"]:
            assert any(failure in failures for failures in categories.values())
        assert candidate["eligible"] is (not candidate["validation_failures"])
    for category in ("sample", "performance", "risk", "cost", "other"):
        expected = sum(
            bool(candidate["validation_failure_categories"].get(category))
            for candidate in result["candidates"]
        )
        assert result["failure_summary"][category] == expected


def test_research_marks_short_walk_forward_windows_as_sample_inadequacy():
    result = StrategyResearchAgent().research(candles_from_closes([100 + i * 0.1 for i in range(120)]))
    assert result["sample_adequacy"]["available_bars"] == 120
    assert result["sample_adequacy"]["status"] == "insufficient_for_configured_walk_forward_gates"
    assert result["sample_adequacy"]["walk_forward_evaluation_bars"] == [23, 23, 23]
    assert all(
        "insufficient_walk_forward_history"
        in candidate["validation_failure_categories"]["sample"]
        for candidate in result["candidates"]
    )


def test_research_validates_short_samples_before_reporting_insufficient_history():
    rows = candles_from_closes([100.0] * 60)
    rows[10]["close"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        StrategyResearchAgent().research(rows)
