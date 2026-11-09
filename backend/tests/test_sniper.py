import math

import pytest

from app.agents.sniper import SniperAgent


def candidate(**overrides):
    values = {
        "strategy_id": "ema_12_26",
        "eligible": True,
        "score": 0.12,
        "current_entry_signal": True,
    }
    values.update(overrides)
    return values


def market(**overrides):
    values = {
        "symbol": "BTC/EUR",
        "provider_id": "ccxt:kraken",
        "instrument_id": "ccxt:kraken:spot:BTC/EUR",
        "ask": 100.0,
        "last": 99.9,
    }
    values.update(overrides)
    return values


def analysis(**overrides):
    values = {"close": 100.0, "indicators": {"atr_14": 1.0}}
    values.update(overrides)
    return values


def test_sniper_builds_bounded_proposal_with_atr_protection_and_risk_sizing():
    sniper = SniperAgent()
    proposal = sniper.propose(
        market(),
        analysis(),
        candidate(),
        equity=1000.0,
        available_cash=1000.0,
    )

    assert proposal is not None
    assert proposal.side == "buy"
    assert proposal.source_agent == "sniper"
    assert proposal.instrument_id == "ccxt:kraken:spot:BTC/EUR"
    assert proposal.reference_price == pytest.approx(100.0)
    assert proposal.stop_loss == pytest.approx(98.0)
    assert proposal.take_profit == pytest.approx(104.0)
    assert proposal.quantity * proposal.reference_price <= 250.0
    assert proposal.quantity * (proposal.reference_price - proposal.stop_loss) <= 10.0


@pytest.mark.parametrize(
    "market_data,analysis_data,candidate_data,equity,cash",
    [
        (market(symbol="BTC/USDT"), analysis(), candidate(), 1000, 1000),
        (market(), analysis(), candidate(eligible=False), 1000, 1000),
        (market(), analysis(), candidate(current_entry_signal=False), 1000, 1000),
        (market(), analysis(indicators={"atr_14": 0}), candidate(), 1000, 1000),
        (market(), analysis(), candidate(), 1000, 0),
        (market(), analysis(), candidate(), 0, 1000),
        (market(ask=float("nan")), analysis(), candidate(), 1000, 1000),
        (market(), analysis(indicators={"atr_14": float("inf")}), candidate(), 1000, 1000),
        (market(), analysis(), candidate(score=float("nan")), 1000, 1000),
    ],
)
def test_sniper_fails_closed_on_invalid_or_unavailable_inputs(
    market_data, analysis_data, candidate_data, equity, cash
):
    proposal = SniperAgent().propose(
        market_data,
        analysis_data,
        candidate_data,
        equity=equity,
        available_cash=cash,
    )
    assert proposal is None


def test_sniper_respects_minimum_order_value():
    proposal = SniperAgent(minimum_order_value=100).propose(
        market(ask=10000.0),
        analysis(close=10000.0, indicators={"atr_14": 100.0}),
        candidate(),
        equity=20.0,
        available_cash=20.0,
    )
    assert proposal is None


def test_sniper_rejects_invalid_configuration():
    with pytest.raises(ValueError, match="risk_per_trade_fraction"):
        SniperAgent(max_order_fraction=0.1, risk_per_trade_fraction=0.2)
    with pytest.raises(ValueError, match="positive"):
        SniperAgent(atr_stop_multiple=0)
