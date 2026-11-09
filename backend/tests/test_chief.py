from app.chief import ChiefAgent


def eligible_scan(*, entry=True, hold=True, symbol="BTC/EUR", price=100.0, atr=1.0):
    return {
        "analyses": [
            {
                "market": {"symbol": symbol, "last": price, "ask": price, "bid": price - 0.1, "quote_volume_24h": 10000},
                "analysis": {"close": price, "indicators": {"atr_14": atr}},
                "strategy_research": {
                    "recommended_strategy": "ema_12_26",
                    "candidates": [
                        {
                            "strategy_id": "ema_12_26",
                            "eligible": True,
                            "score": 0.12,
                            "current_entry_signal": entry,
                            "current_hold_signal": hold,
                            "out_of_sample": {"net_return": 0.1, "max_drawdown": 0.05, "trades": 8},
                        }
                    ],
                },
            }
        ]
    }


def portfolio(*, equity=1000.0, cash=1000.0, positions=None):
    return {"equity": equity, "cash": cash, "positions": positions or []}


def test_chief_proposes_buy_only_after_strategy_and_risk_validation():
    decision = ChiefAgent().decide(eligible_scan(), portfolio())
    assert decision.action == "buy"
    assert decision.proposal is not None
    assert decision.proposal.stop_loss < decision.proposal.reference_price
    assert decision.proposal.take_profit > decision.proposal.reference_price
    assert decision.risk.accepted is True
    assert decision.proposal.quantity * decision.proposal.reference_price <= 250


def test_chief_holds_when_no_validated_strategy_exists():
    scan = eligible_scan()
    scan["analyses"][0]["strategy_research"]["recommended_strategy"] = None
    decision = ChiefAgent().decide(scan, portfolio())
    assert decision.action == "hold"
    assert decision.proposal is None


def test_chief_holds_when_current_strategy_has_no_entry_signal():
    decision = ChiefAgent().decide(eligible_scan(entry=False), portfolio())
    assert decision.action == "hold"


def test_chief_sells_only_when_exit_signal_and_position_exists():
    decision = ChiefAgent().decide(
        eligible_scan(entry=False, hold=False),
        portfolio(positions=[{"symbol": "BTC/EUR", "quantity": 0.25}]),
    )
    assert decision.action == "sell"
    assert decision.proposal.quantity == 0.25
    assert decision.risk.accepted is True


def test_chief_rejects_buy_if_stop_distance_exceeds_risk_limit():
    decision = ChiefAgent().decide(eligible_scan(atr=5.0), portfolio())
    assert decision.action == "hold"
    assert decision.risk is not None
    assert decision.risk.accepted is False


def test_chief_exits_on_persisted_stop_loss_even_without_strategy_recommendation():
    scan = eligible_scan()
    scan["analyses"][0]["strategy_research"]["recommended_strategy"] = None
    decision = ChiefAgent().decide(
        scan,
        portfolio(positions=[{
            "symbol": "BTC/EUR",
            "quantity": 0.25,
            "strategy_id": "ema_12_26",
            "stop_loss": 100.0,
            "take_profit": 110.0,
        }]),
    )
    assert decision.action == "sell"
    assert "stop-loss" in decision.proposal.rationale
    assert decision.risk.accepted is True


def test_chief_exits_on_persisted_take_profit():
    scan = eligible_scan(price=111.0)
    decision = ChiefAgent().decide(
        scan,
        portfolio(positions=[{
            "symbol": "BTC/EUR",
            "quantity": 0.25,
            "strategy_id": "ema_12_26",
            "stop_loss": 90.0,
            "take_profit": 110.0,
        }]),
    )
    assert decision.action == "sell"
    assert "take-profit" in decision.proposal.rationale
    assert decision.risk.accepted is True
