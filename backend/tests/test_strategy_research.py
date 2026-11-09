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
