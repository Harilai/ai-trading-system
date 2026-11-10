from app.chief import ChiefAgent
from app.strategy_research import StrategyResearchAgent


def _experimental_scan(*, entry=True, hold=True, price=100.0, atr_value=1.0):
    return {
        "analyses": [{
            "market": {
                "symbol": "BTC/EUR",
                "provider_id": "ccxt:kraken",
                "instrument_id": "ccxt:kraken:spot:BTC/EUR",
                "last": price,
                "bid": price - 0.1,
                "ask": price,
                "quote_currency": "EUR",
            },
            "analysis": {
                "close": price,
                "indicators": {"atr_14": atr_value},
            },
            "strategy_research": {
                "recommended_strategy": None,
                "recommended_experimental_strategy": "ema_12_26",
                "candidates": [{
                    "strategy_id": "ema_12_26",
                    "eligible": False,
                    "score": None,
                    "validation_failures": ["does_not_beat_buy_and_hold"],
                    "experimental_eligible": True,
                    "experimental_score": -0.02,
                    "experimental_validation_failures": [],
                    "current_entry_signal": entry,
                    "current_hold_signal": hold,
                    "out_of_sample": {
                        "net_return": -0.01,
                        "max_drawdown": 0.08,
                        "trades": 2,
                    },
                }],
            },
        }],
    }


def _portfolio(*, equity=10000.0, cash=10000.0, positions=None):
    return {
        "equity": equity,
        "cash": cash,
        "available_cash_for_manual_withdrawal": cash,
        "positions": positions or [],
        "valuation_complete": True,
    }


def test_experimental_paper_mode_is_disabled_by_default():
    decision = ChiefAgent().decide(_experimental_scan(), _portfolio())

    assert decision.action == "hold"
    assert decision.proposal is None
    assert "No eligible strategy" in decision.rationale


def test_demo_experimental_mode_can_execute_only_paper_tagged_strategy():
    chief = ChiefAgent(
        max_order_fraction=0.05,
        max_stop_distance_fraction=0.03,
        risk_per_trade_fraction=0.0025,
        allow_experimental_strategies=True,
    )

    decision = chief.decide(_experimental_scan(), _portfolio())

    assert decision.action == "buy"
    assert decision.risk is not None and decision.risk.accepted is True
    assert decision.strategy_id == "experimental:ema_12_26"
    assert decision.evidence["strategy_validation_status"] == "experimental"
    assert "not validated for profitability" in decision.rationale
    assert decision.proposal.quantity * decision.proposal.reference_price <= 500.0


def test_experimental_mode_selects_best_candidate_with_a_current_entry_signal():
    scan = _experimental_scan(entry=False)
    research = scan["analyses"][0]["strategy_research"]
    research["recommended_experimental_strategy"] = "ema_12_26"
    research["candidates"].append({
        "strategy_id": "rsi_14_reversion",
        "eligible": False,
        "experimental_eligible": True,
        "experimental_score": -0.01,
        "current_entry_signal": True,
        "current_hold_signal": True,
        "out_of_sample": {"net_return": -0.005, "max_drawdown": 0.04, "trades": 1},
        "validation_failures": ["insufficient_oos_trades"],
    })

    chief = ChiefAgent(
        max_order_fraction=0.05,
        max_stop_distance_fraction=0.03,
        risk_per_trade_fraction=0.0025,
        allow_experimental_strategies=True,
    )
    decision = chief.decide(scan, _portfolio())

    assert decision.action == "buy"
    assert decision.strategy_id == "experimental:rsi_14_reversion"


def test_experimental_paper_mode_still_obeys_tighter_risk_limits():
    chief = ChiefAgent(
        max_order_fraction=0.05,
        max_stop_distance_fraction=0.01,
        risk_per_trade_fraction=0.0025,
        allow_experimental_strategies=True,
    )

    decision = chief.decide(_experimental_scan(atr_value=1.0), _portfolio())

    assert decision.action == "hold"
    assert decision.risk is not None
    assert decision.risk.accepted is False
    assert "stop-loss distance" in decision.risk.reason.lower()


def test_experimental_position_can_still_exit_when_experimental_mode_is_disabled():
    chief = ChiefAgent(allow_experimental_strategies=False)
    position = {
        "symbol": "BTC/EUR",
        "provider_id": "ccxt:kraken",
        "instrument_id": "ccxt:kraken:spot:BTC/EUR",
        "quantity": 0.5,
        "strategy_id": "experimental:ema_12_26",
        "stop_loss": 90.0,
        "take_profit": 110.0,
    }

    decision = chief.decide(
        _experimental_scan(entry=False, hold=False),
        _portfolio(positions=[position]),
    )

    assert decision.action == "sell"
    assert decision.risk is not None and decision.risk.accepted is True
    assert decision.strategy_id == "experimental:ema_12_26"
    assert "experimental strategy exit signal" in decision.rationale


def test_strategy_research_exposes_separate_experimental_candidates():
    candles = [
        {
            "timestamp": 1_700_000_000_000 + index * 3_600_000,
            "open": 100.0 + index * 0.01,
            "high": (100.0 + index * 0.01) * 1.01,
            "low": (100.0 + index * 0.01) * 0.99,
            "close": 100.0 + index * 0.01,
            "volume": 1000.0,
        }
        for index in range(250)
    ]

    result = StrategyResearchAgent().research(candles)

    assert result["execution"] == "research_only"
    assert result["experimental_candidate_count"] == 3
    assert result["recommended_experimental_strategy"] in {
        "ema_12_26", "rsi_14_reversion", "bollinger_20_reversion",
    }
    assert all(
        item["experimental_eligible"] for item in result["candidates"]
    )
    # Experimental availability must never promote a strategy into the
    # production-quality/validated recommendation.
    assert result["recommended_strategy"] is None
    assert all(item["eligible"] is False for item in result["candidates"])
