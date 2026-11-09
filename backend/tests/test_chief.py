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


def test_risk_manager_rejects_futures_and_forex_until_profile_authorised():
    from app.agents.contracts import RiskContext, TradeProposal
    from app.agents.risk import RiskManager

    context = RiskContext(
        equity_eur=1000,
        available_cash_eur=1000,
        positions={},
    )
    for asset_class, market_type in [("crypto", "futures"), ("forex", "forex"), ("equity", "spot"), ("defi", "onchain")]:
        proposal = TradeProposal(
            symbol="BTC/EUR",
            asset_class=asset_class,
            market_type=market_type,
            side="buy",
            quantity=0.01,
            reference_price=100,
            stop_loss=98,
            rationale="Risk profile must remain disabled by default",
        )
        decision = RiskManager().evaluate(proposal, context)
        assert decision.accepted is False
        assert "Risk profile disabled" in decision.reason


def test_risk_manager_accepts_only_enabled_crypto_spot_profile():
    from app.agents.contracts import RiskContext, TradeProposal
    from app.agents.risk import RiskManager

    proposal = TradeProposal(
        symbol="BTC/EUR",
        provider_id="ccxt:kraken",
        asset_class="crypto",
        market_type="spot",
        side="buy",
        quantity=0.01,
        reference_price=100,
        stop_loss=98,
        take_profit=104,
        rationale="Validated spot trade with protective exit levels",
    )
    decision = RiskManager().evaluate(
        proposal,
        RiskContext(equity_eur=1000, available_cash_eur=1000),
    )
    assert decision.accepted is True


def test_chief_does_not_confuse_same_symbol_across_providers():
    scan = eligible_scan()
    scan["analyses"][0]["market"]["provider_id"] = "ccxt:kraken"
    scan["analyses"][0]["market"]["instrument_id"] = "ccxt:kraken:spot:BTC/EUR"
    other_position = {
        "symbol": "BTC/EUR",
        "provider_id": "ccxt:coinbase",
        "instrument_id": "ccxt:coinbase:spot:BTC/EUR",
        "quantity": 0.25,
    }
    decision = ChiefAgent().decide(scan, portfolio(positions=[other_position]))
    assert decision.action == "buy"
    assert decision.proposal.provider_id == "ccxt:kraken"
    assert decision.proposal.instrument_id == "ccxt:kraken:spot:BTC/EUR"


def test_chief_exit_targets_the_exact_provider_position():
    scan = eligible_scan(entry=False, hold=False)
    scan["analyses"][0]["market"]["provider_id"] = "ccxt:coinbase"
    scan["analyses"][0]["market"]["instrument_id"] = "ccxt:coinbase:spot:BTC/EUR"
    position = {
        "symbol": "BTC/EUR",
        "provider_id": "ccxt:coinbase",
        "instrument_id": "ccxt:coinbase:spot:BTC/EUR",
        "quantity": 0.25,
    }
    decision = ChiefAgent().decide(scan, portfolio(positions=[position]))
    assert decision.action == "sell"
    assert decision.proposal.provider_id == "ccxt:coinbase"
    assert decision.proposal.instrument_id == "ccxt:coinbase:spot:BTC/EUR"
    assert decision.proposal.quantity == 0.25
