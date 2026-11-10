import pytest

from app.agents.charts import ChartsAgent
from app.agents.contracts import RiskContext, TradeProposal
from app.agents.risk import RiskManager
from app.chief import ChiefAgent


def proposal(**overrides):
    values = {
        "symbol": "BTC/EUR",
        "side": "buy",
        "quantity": 0.001,
        "reference_price": 10000,
        "stop_loss": 9700,
        "rationale": "Trend confirmation with defined downside risk",
    }
    values.update(overrides)
    return TradeProposal(**values)


def context(**overrides):
    values = {
        "equity_eur": 100,
        "available_cash_eur": 100,
        "max_order_fraction": 0.25,
        "max_stop_distance_fraction": 0.05,
        "estimated_fee_rate": 0.001,
    }
    values.update(overrides)
    return RiskContext(**values)


def test_risk_manager_accepts_bounded_buy():
    decision = RiskManager().evaluate(proposal(), context())
    assert decision.accepted is True
    assert decision.approved_quantity == pytest.approx(0.001)
    assert decision.estimated_notional_eur == pytest.approx(10.01)


@pytest.mark.parametrize(
    "trade,ctx,reason",
    [
        ({"symbol": "BTC/USDT"}, {}, "eur-quoted"),
        ({"stop_loss": None}, {}, "stop-loss"),
        ({"stop_loss": 10000}, {}, "below"),
        ({"stop_loss": 9000}, {}, "distance"),
        ({"quantity": 0.003}, {}, "allocation"),
        ({"quantity": 0.01}, {"equity_eur": 1000, "available_cash_eur": 50, "max_order_fraction": 1.0}, "cash"),
    ],
)
def test_risk_manager_rejects_unsafe_buy(trade, ctx, reason):
    decision = RiskManager().evaluate(proposal(**trade), context(**ctx))
    assert decision.accepted is False
    assert reason in decision.reason.lower()


def test_spot_sell_must_not_create_short_position():
    sell = proposal(side="sell", stop_loss=None, quantity=0.002)
    decision = RiskManager().evaluate(sell, context(positions={"BTC/EUR": 0.001}))
    assert decision.accepted is False
    assert "shorting is disabled" in decision.reason


def test_spot_sell_can_reduce_existing_position():
    sell = proposal(side="sell", stop_loss=None, quantity=0.001)
    decision = RiskManager().evaluate(sell, context(positions={"BTC/EUR": 0.002}))
    assert decision.accepted is True
    assert decision.approved_quantity == pytest.approx(0.001)


def test_charts_agent_returns_typed_analysis():
    candles = [
        {
            "timestamp": 1_700_000_000_000 + index * 60_000,
            "high": 100 + index + 1,
            "low": 100 + index - 1,
            "close": 100 + index,
        }
        for index in range(60)
    ]
    result = ChartsAgent().analyze("btc/eur", "1m", candles)
    assert result.symbol == "BTC/EUR"
    assert result.timeframe == "1m"
    assert result.indicators["ema_20"] > result.indicators["ema_50"]
    assert 0 <= result.indicators["rsi_14"] <= 100


def test_charts_agent_rejects_insufficient_history():
    with pytest.raises(ValueError):
        ChartsAgent().analyze("BTC/EUR", "1m", [])


def equity_proposal(**overrides):
    values = {
        "symbol": "AAPL",
        "provider_id": "twelve_data",
        "instrument_id": "twelve_data:cash_equity:AAPL",
        "asset_class": "equity",
        "market_type": "cash_equity",
        "side": "buy",
        "quantity": 1,
        "reference_price": 100,
        "stop_loss": 95,
        "rationale": "Owner initiated paper equity purchase with a defined stop",
        "source_agent": "manual_owner",
    }
    values.update(overrides)
    return TradeProposal(**values)


def equity_context(**overrides):
    values = {
        "equity_eur": 1000,
        "available_cash_eur": 0,
        "available_cash_by_currency": {"USD": 500},
        "quote_currency": "USD",
        "fx_rate_to_eur": 0.9,
        "max_order_fraction": 0.25,
        "max_stop_distance_fraction": 0.05,
        "estimated_fee_rate": 0.001,
        "estimated_slippage_rate": 0.001,
    }
    values.update(overrides)
    return RiskContext(**values)


def test_risk_manager_accepts_manual_usd_equity_with_fx_converted_exposure():
    decision = RiskManager().evaluate(equity_proposal(), equity_context())
    assert decision.accepted is True
    assert decision.estimated_notional_eur == pytest.approx(90.18009)


def test_risk_manager_rejects_equity_orders_from_autonomous_agents():
    decision = RiskManager().evaluate(
        equity_proposal(source_agent="sniper"),
        equity_context(),
    )
    assert decision.accepted is False
    assert "manual-owner only" in decision.reason


def test_risk_manager_rejects_equity_buy_without_usd_cash():
    decision = RiskManager().evaluate(
        equity_proposal(), equity_context(available_cash_by_currency={"USD": 0}),
    )
    assert decision.accepted is False
    assert "insufficient available usd" in decision.reason.lower()


def test_risk_manager_rejects_equity_order_above_eur_allocation_limit():
    decision = RiskManager().evaluate(
        equity_proposal(quantity=4),
        equity_context(available_cash_by_currency={"USD": 1000}),
    )
    assert decision.accepted is False
    assert "maximum eur portfolio allocation" in decision.reason.lower()


def test_risk_manager_allows_equity_sell_only_against_matching_position():
    sell = equity_proposal(side="sell", stop_loss=None, quantity=1)
    accepted = RiskManager().evaluate(
        sell, equity_context(positions={"twelve_data:cash_equity:AAPL": 2}),
    )
    rejected = RiskManager().evaluate(
        equity_proposal(side="sell", stop_loss=None, quantity=3),
        equity_context(positions={"twelve_data:cash_equity:AAPL": 2}),
    )
    assert accepted.accepted is True
    assert rejected.accepted is False
    assert "shorting is disabled" in rejected.reason


def test_risk_manager_rejects_equity_purchase_above_position_concentration_cap():
    decision = RiskManager().evaluate(
        equity_proposal(),
        equity_context(positions={"twelve_data:cash_equity:AAPL": 2}),
    )
    assert decision.accepted is False
    assert "instrument concentration" in decision.reason.lower()


def test_equity_concentration_uses_current_eur_market_value_when_available():
    decision = RiskManager().evaluate(
        equity_proposal(),
        equity_context(
            positions={"twelve_data:cash_equity:AAPL": 1},
            position_values_eur={"twelve_data:cash_equity:AAPL": 190},
        ),
    )
    assert decision.accepted is False
    assert "instrument concentration" in decision.reason.lower()



def test_chief_explains_validated_candidate_without_current_entry_signal():
    chief = ChiefAgent()
    decision = chief.decide(
        {
            "analyses": [{
                "market": {"symbol": "BTC/EUR", "last": 100, "ask": 100},
                "strategy_research": {
                    "recommended_strategy": "ema_12_26",
                    "candidates": [{
                        "strategy_id": "ema_12_26",
                        "eligible": True,
                        "score": 0.5,
                        "current_entry_signal": False,
                        "validation_failures": ["non_positive_oos_return"],
                        "experimental_validation_failures": ["insufficient_walk_forward_history"],
                    }],
                },
            }],
        },
        {"equity": 1000, "cash": 1000, "positions": []},
    )
    assert decision.action == "hold"
    assert decision.evidence["validation_failure_counts"]["promotion"]["non_positive_oos_return"] == 1
    assert decision.evidence["validation_failure_counts"]["experimental"]["insufficient_walk_forward_history"] == 1
    assert decision.evidence["markets_analysed"] == 1
    assert decision.evidence["validated_candidates"] == 1
    assert decision.evidence["validated_entry_signals"] == 0
    assert decision.evidence["candidates_without_entry_signal"] == 1
    assert decision.evidence["eligible_opportunities"] == 0


def test_chief_reports_experimental_candidate_without_entry_signal():
    chief = ChiefAgent(allow_experimental_strategies=True)
    decision = chief.decide(
        {
            "analyses": [{
                "market": {"symbol": "ETH/EUR", "last": 100, "ask": 100},
                "strategy_research": {
                    "recommended_experimental_strategy": "rsi_14_reversion",
                    "candidates": [{
                        "strategy_id": "rsi_14_reversion",
                        "eligible": False,
                        "experimental_eligible": True,
                        "experimental_score": 0.25,
                        "current_entry_signal": False,
                    }],
                },
            }],
        },
        {"equity": 1000, "cash": 1000, "positions": []},
    )
    assert decision.action == "hold"
    assert decision.evidence["experimental_candidates"] == 1
    assert decision.evidence["experimental_entry_signals"] == 0
    assert decision.evidence["eligible_opportunities"] == 0
