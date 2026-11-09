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
    assert result["method"] == "chronological_70_30_next_bar_open_fills"
    assert result["candidate_count"] == 3
    assert len(result["candidates"]) == 3
    assert all("out_of_sample" in item for item in result["candidates"])
    assert result["execution"] == "research_only"
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
