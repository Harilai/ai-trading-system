import pytest

from app.agents.charts import ChartsAgent
from app.agents.contracts import RiskContext, TradeProposal
from app.agents.risk import RiskManager


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
        ({"symbol": "BTC/USDT"}, {}, "EUR-quoted"),
        ({"stop_loss": None}, {}, "stop-loss"),
        ({"stop_loss": 10000}, {}, "below"),
        ({"stop_loss": 9000}, {}, "distance"),
        ({"quantity": 0.003}, {}, "allocation"),
        ({"quantity": 0.01}, {"available_cash_eur": 50}, "cash"),
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
