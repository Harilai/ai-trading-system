from datetime import datetime, timedelta

import pytest

from app.autonomous import AutonomousAgent, AutonomousStore, utcnow


def test_agent_starts_paused_and_emergency_stopped(tmp_path):
    store = AutonomousStore(str(tmp_path / "agent.db"))
    state = store.state()
    assert state["enabled"] == 0
    assert state["emergency_stop"] == 0
    store.emergency_stop()
    assert store.state()["emergency_stop"] == 1


def test_task_queue_persists_and_completes(tmp_path):
    path = str(tmp_path / "agent.db")
    store = AutonomousStore(path)
    task = store.enqueue("health_check", {"source": "test"}, due_at=utcnow() - timedelta(seconds=1))
    restarted = AutonomousStore(path)
    claimed = restarted.claim_due_task()
    assert claimed["id"] == task["id"]
    assert restarted.finish_task(task["id"], {"ok": True}, claimed["lease_token"]) is True
    assert restarted.overview()["tasks"][0]["status"] == "completed"


def test_expired_running_task_is_recovered_on_startup(tmp_path):
    path = str(tmp_path / "agent.db")
    store = AutonomousStore(path)
    task = store.enqueue("health_check", {})
    claimed = store.claim_due_task()
    assert claimed["id"] == task["id"]
    with store.connect() as db:
        db.execute(
            "UPDATE agent_tasks SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?",
            (task["id"],),
        )
    restarted = AutonomousStore(path)
    assert restarted.claim_due_task()["id"] == task["id"]


def test_expired_running_task_is_recovered_without_restart(tmp_path):
    store = AutonomousStore(str(tmp_path / "agent.db"))
    task = store.enqueue("health_check", {})
    claimed = store.claim_due_task()
    assert claimed["id"] == task["id"]

    with store.connect() as db:
        db.execute(
            "UPDATE agent_tasks SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?",
            (task["id"],),
        )

    assert store.recover_expired_tasks() == 1
    assert store.claim_due_task()["id"] == task["id"]


def test_unexpired_running_task_is_not_recovered(tmp_path):
    store = AutonomousStore(str(tmp_path / "agent.db"))
    task = store.enqueue("health_check", {})
    claimed = store.claim_due_task()
    assert claimed["id"] == task["id"]

    assert store.recover_expired_tasks() == 0
    with store.connect() as db:
        row = db.execute("SELECT status FROM agent_tasks WHERE id=?", (task["id"],)).fetchone()
    assert row["status"] == "running"


def test_decision_cycle_persists_proposal_only_result(tmp_path):
    import asyncio

    class FakeScout:
        def scan(self, include_symbols=None):
            return {"analyses": [{"market": {"symbol": "BTC/EUR"}}]}

    class FakeDecision:
        action = "hold"
        proposal = None
        risk = None

        def model_dump(self):
            return {"action": "hold", "rationale": "No validated opportunity"}

    class FakeChief:
        def decide(self, scan, portfolio, *, account_readiness=None):
            assert scan["analyses"]
            assert portfolio["cash"] == 20
            return FakeDecision()

    class FakePortfolio:
        def snapshot(self, valuation_prices=None):
            return {"cash": 20}

        def reconcile_order_intents(self):
            return {"reconciled_filled": 0, "reconciliation_required": 0}

        def get_order_intent(self, client_order_id):
            return None

        def list_order_intents(self, limit=100):
            return []

        def get_trade_by_client_order_id(self, client_order_id):
            return None

    store = AutonomousStore(str(tmp_path / "decision.db"))
    worker = AutonomousAgent(store, scout=FakeScout(), chief=FakeChief(), portfolio=FakePortfolio())
    task = {"id": "decision-test", "kind": "decision_cycle", "payload": {}}
    result = asyncio.run(worker._execute(task))
    assert result["execution"] == "no_order"
    assert result["decision"]["action"] == "hold"
    memory = store.overview()["memory"]
    assert memory[0]["category"] == "chief_decision"


def test_decision_cycle_executes_only_risk_approved_paper_order_once(tmp_path):
    import asyncio

    from datetime import datetime, timezone
    from app.agents.contracts import RiskDecision, TradeProposal
    from app.chief import ChiefDecision

    market_timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)

    class FakeScout:
        def scan(self, include_symbols=None):
            return {
                "analyses": [{
                    "market": {
                        "symbol": "BTC/EUR",
                        "provider_id": "ccxt:kraken",
                        "instrument_id": "ccxt:kraken:spot:BTC/EUR",
                        "ticker_timestamp_ms": market_timestamp,
                    },
                }],
                "included_positions": include_symbols or [],
            }

    class FakeChief:
        def decide(self, scan, portfolio, *, account_readiness=None):
            proposal = TradeProposal(
                symbol="BTC/EUR",
                side="buy",
                quantity=0.001,
                reference_price=10000,
                stop_loss=9800,
                take_profit=10400,
                rationale="Validated trend entry with bounded downside",
                source_agent="chief",
            )
            return ChiefDecision(
                action="buy",
                symbol="BTC/EUR",
                strategy_id="ema_12_26",
                rationale="Risk-approved paper entry",
                proposal=proposal,
                risk=RiskDecision(
                    accepted=True,
                    reason="passed",
                    approved_quantity=0.001,
                    estimated_notional_eur=10.03,
                ),
            )

    class FakePortfolio:
        def __init__(self):
            self.order = None
            self.intent = None
            self.execute_calls = 0

        def snapshot(self, valuation_prices=None):
            return {"cash": 20, "equity": 20, "positions": []}

        def reconcile_order_intents(self):
            return {"reconciled_filled": 0, "reconciliation_required": 0}

        def get_order_intent(self, client_order_id):
            return self.intent

        def list_order_intents(self, limit=100):
            return [self.intent] if self.intent else []

        def record_order_intent(self, client_order_id, request, decision):
            self.intent = {
                "client_order_id": client_order_id,
                "status": "risk_approved",
                "request": request,
                "decision": decision,
            }
            return self.intent

        def reject_order_intent(self, client_order_id, reason):
            self.intent["status"] = "rejected"
            self.intent["rejection_reason"] = reason

        def get_trade_by_client_order_id(self, client_order_id):
            if self.order and self.order["client_order_id"] == client_order_id:
                return self.order
            return None

        def execute(self, *args, **kwargs):
            self.execute_calls += 1
            self.order = {
                "symbol": kwargs["symbol"],
                "side": kwargs["side"],
                "client_order_id": kwargs["client_order_id"],
                "strategy_id": kwargs["strategy_id"],
                "stop_loss": kwargs["stop_loss"],
                "take_profit": kwargs["take_profit"],
            }
            self.intent["status"] = "filled"
            return self.order

    class ReadyReadiness:
        def evaluate(self, **kwargs):
            return {"status": "ready", "blockers": []}

    store = AutonomousStore(str(tmp_path / "paper-cycle.db"))
    portfolio = FakePortfolio()
    worker = AutonomousAgent(
        store,
        scout=FakeScout(),
        chief=FakeChief(),
        portfolio=portfolio,
        account_readiness=ReadyReadiness(),
    )
    task = {"id": "paper-decision-1", "kind": "decision_cycle", "payload": {}}

    first = asyncio.run(worker._execute(task))
    second = asyncio.run(worker._execute(task))

    assert first["execution"] == "paper_order"
    assert first["order"]["client_order_id"] == task["id"]
    assert first["order"]["strategy_id"] == "ema_12_26"
    assert first["order"]["stop_loss"] == 9800
    assert first["order"]["take_profit"] == 10400
    assert second["execution"] == "paper_order_replayed"
    assert portfolio.execute_calls == 1



def test_end_to_end_paper_buy_then_stop_loss_exit(tmp_path):
    import asyncio
    from datetime import datetime, timezone

    market_timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    from app.chief import ChiefAgent
    from app.portfolio import PaperPortfolio

    class MutableScout:
        def __init__(self):
            self.exit_mode = False

        def scan(self, include_symbols=None):
            if not self.exit_mode:
                return {
                    "analyses": [{
                        "market": {
                            "symbol": "BTC/EUR",
                            "last": 100.0,
                            "ask": 100.0,
                            "bid": 99.9,
                            "quote_volume_24h": 10000,
                            "ticker_timestamp_ms": market_timestamp,
                        },
                        "analysis": {"close": 100.0, "indicators": {"atr_14": 1.0}},
                        "strategy_research": {
                            "recommended_strategy": "ema_12_26",
                            "candidates": [{
                                "strategy_id": "ema_12_26",
                                "eligible": True,
                                "score": 0.1,
                                "current_entry_signal": True,
                                "current_hold_signal": True,
                                "out_of_sample": {"net_return": 0.1, "max_drawdown": 0.05, "trades": 8},
                            }],
                        },
                    }]
                }
            assert include_symbols == [{
                "symbol": "BTC/EUR",
                "provider_id": "ccxt:kraken",
                "instrument_id": "ccxt:kraken:spot:BTC/EUR",
            }]
            return {
                "analyses": [{
                    "market": {
                        "symbol": "BTC/EUR",
                        "last": 97.0,
                        "ask": 97.1,
                        "bid": 97.0,
                        "quote_volume_24h": 10000,
                        "ticker_timestamp_ms": market_timestamp,
                    },
                    "analysis": {"close": 97.0, "indicators": {"atr_14": 1.0}},
                    "strategy_research": {"recommended_strategy": None, "candidates": []},
                }]
            }

    store = AutonomousStore(str(tmp_path / "e2e-agent.db"))
    portfolio = PaperPortfolio(str(tmp_path / "e2e-paper.db"), initial_cash=20)
    scout = MutableScout()
    chief = ChiefAgent(minimum_order_value_eur=1)
    from app.account_readiness import PaperAccountReadiness
    from app.execution_adapters import PaperPortfolioScopedAdapter

    readiness = PaperAccountReadiness(
        portfolio,
        PaperPortfolioScopedAdapter(portfolio, account_id="paper-primary"),
    )
    worker = AutonomousAgent(
        store, scout=scout, chief=chief, portfolio=portfolio,
        account_readiness=readiness,
    )

    buy_task = {"id": "buy-task-1", "kind": "decision_cycle", "payload": {}}
    buy_result = asyncio.run(worker._execute(buy_task))
    assert buy_result["execution"] == "paper_order"
    position = portfolio.snapshot()["positions"][0]
    assert position["strategy_id"] == "ema_12_26"
    assert position["stop_loss"] == 98.0
    assert position["take_profit"] == 104.0

    scout.exit_mode = True
    sell_task = {"id": "sell-task-1", "kind": "decision_cycle", "payload": {}}
    sell_result = asyncio.run(worker._execute(sell_task))
    assert sell_result["execution"] == "paper_order"
    assert sell_result["decision"]["action"] == "sell"
    assert portfolio.snapshot()["positions"] == []
    assert [trade["side"] for trade in reversed(portfolio.history())] == ["buy", "sell"]


def test_scheduler_enqueues_one_decision_cycle_when_enabled(tmp_path):
    store = AutonomousStore(str(tmp_path / "schedule.db"))
    assert store.schedule_decision_cycle(300) is None

    store.set_enabled(True)
    first = store.schedule_decision_cycle(300)
    assert first["kind"] == "decision_cycle"
    assert first["status"] == "pending"
    assert store.schedule_decision_cycle(300) is None

    with store.connect() as db:
        rows = db.execute(
            "SELECT id FROM agent_tasks WHERE kind='decision_cycle' AND status='pending'"
        ).fetchall()
    assert len(rows) == 1


def test_scheduler_respects_interval_and_enqueues_again_after_due_time(tmp_path):
    store = AutonomousStore(str(tmp_path / "schedule.db"))
    store.set_enabled(True)
    first = store.schedule_decision_cycle(300)
    claimed = store.claim_due_task()
    assert store.finish_task(claimed["id"], {"execution": "no_order"}, claimed["lease_token"]) is True

    assert store.schedule_decision_cycle(300) is None
    with store.connect() as db:
        db.execute(
            "UPDATE agent_state SET last_decision_enqueued_at=? WHERE id=1",
            ((utcnow() - timedelta(seconds=301)).isoformat(),),
        )

    second = store.schedule_decision_cycle(300)
    assert second is not None
    assert second["id"] != first["id"]


def test_scheduler_does_not_enqueue_while_paused_or_emergency_stopped(tmp_path):
    store = AutonomousStore(str(tmp_path / "schedule.db"))
    store.set_enabled(True)
    store.emergency_stop()
    assert store.schedule_decision_cycle(300) is None


def test_scheduler_rejects_too_frequent_cycles(tmp_path):
    store = AutonomousStore(str(tmp_path / "schedule.db"))
    with pytest.raises(ValueError, match="60 seconds"):
        store.schedule_decision_cycle(30)


def test_equity_research_scheduler_is_independent_and_low_frequency(tmp_path):
    store = AutonomousStore(str(tmp_path / "equity-schedule.db"))
    assert store.schedule_equity_research(21600) is None
    store.set_enabled(True)
    first = store.schedule_equity_research(21600)
    assert first["kind"] == "equity_research"
    assert store.schedule_equity_research(21600) is None

    claimed = store.claim_due_task()
    assert claimed["kind"] == "equity_research"
    assert store.finish_task(claimed["id"], {"analysed": 2}, claimed["lease_token"]) is True
    assert store.schedule_equity_research(21600) is None
    with store.connect() as db:
        db.execute(
            "UPDATE agent_state SET last_equity_research_enqueued_at=? WHERE id=1",
            ((utcnow() - timedelta(seconds=21601)).isoformat(),),
        )
    second = store.schedule_equity_research(21600)
    assert second is not None
    assert second["id"] != first["id"]


def test_equity_research_scheduler_respects_pause_and_minimum_interval(tmp_path):
    store = AutonomousStore(str(tmp_path / "equity-schedule.db"))
    with pytest.raises(ValueError, match="3600 seconds"):
        store.schedule_equity_research(300)
    store.set_enabled(True)
    store.emergency_stop()
    assert store.schedule_equity_research(21600) is None


def test_equity_research_task_runs_read_only_and_persists_observation(tmp_path):
    import asyncio

    class FakeResearch:
        def __init__(self):
            self.calls = 0

        def scan(self):
            self.calls += 1
            return {
                "universe": "twelve_data_us_equities_and_etfs",
                "analysed": 1,
                "execution": "disabled_research_only",
            }

    store = AutonomousStore(str(tmp_path / "equity-research.db"))
    research = FakeResearch()
    worker = AutonomousAgent(store, equity_research=research)
    task = {"id": "equity-research-task", "kind": "equity_research", "payload": {}}
    result = asyncio.run(worker._execute(task))

    assert result["analysed"] == 1
    assert result["execution"] == "disabled_research_only"
    assert research.calls == 1
    memory = store.overview()["memory"]
    assert memory[0]["category"] == "equity_etf_research"
    assert memory[0]["content"]["task_id"] == task["id"]


def test_equity_research_task_fails_closed_without_provider(tmp_path):
    import asyncio

    store = AutonomousStore(str(tmp_path / "equity-research.db"))
    worker = AutonomousAgent(store)
    task = {"id": "equity-research-task", "kind": "equity_research", "payload": {}}
    with pytest.raises(RuntimeError, match="not configured"):
        asyncio.run(worker._execute(task))



def test_worker_survives_transient_store_failure_and_retries(tmp_path):
    import asyncio

    class FlakyStore(AutonomousStore):
        def __init__(self, path):
            super().__init__(path)
            self.state_calls = 0
            self.audit_events = []
            self.worker = None

        def state(self):
            self.state_calls += 1
            if self.state_calls == 1:
                raise RuntimeError("transient SQLite read failure")
            return {"enabled": 0, "emergency_stop": 0}

        def audit(self, action, details=None):
            self.audit_events.append((action, details or {}))

        def heartbeat(self):
            if self.state_calls >= 2:
                self.worker._stopping.set()

    store = FlakyStore(str(tmp_path / "worker-recovery.db"))
    worker = AutonomousAgent(store, poll_interval_seconds=0.01)
    store.worker = worker

    asyncio.run(worker._run())

    assert store.state_calls >= 2
    assert any(action == "worker_loop_error" for action, _ in store.audit_events)


def test_worker_rejects_non_positive_poll_interval(tmp_path):
    store = AutonomousStore(str(tmp_path / "worker-config.db"))
    with pytest.raises(ValueError, match="poll_interval_seconds"):
        AutonomousAgent(store, poll_interval_seconds=0)



def test_strategy_research_history_persists_versioned_candidate_evidence(tmp_path):
    path = str(tmp_path / "strategy-history.db")
    store = AutonomousStore(path)
    research = {
        "method": "chronological_70_30",
        "cost_model": {"fee_rate_per_side": 0.0026, "slippage_rate_per_side": 0.0005},
        "promotion_gates": {"must_beat_buy_and_hold": True},
        "market_regime": {"status": "available", "structure": "trending_up", "volatility": "normal"},
        "data_quality": {"bars": 250, "history_span_days": 10.375, "out_of_sample_bars": 75, "gap_count": 0},
        "candidates": [
            {
                "strategy_id": "ema_12_26",
                "description": "EMA trend",
                "eligible": False,
                "score": None,
                "validation_failures": ["does_not_beat_buy_and_hold"],
                "out_of_sample": {"net_return": -0.01, "trades": 4, "sharpe_per_bar": -0.12},
                "walk_forward": {"positive_windows": 1},
                "cost_sensitivity": {"2.0": {"beats_buy_and_hold": False}},
            },
            {
                "strategy_id": "rsi_14_reversion",
                "description": "RSI reversion",
                "eligible": True,
                "score": 0.12,
                "validation_failures": [],
                "out_of_sample": {"net_return": 0.08, "trades": 6},
                "walk_forward": {"positive_windows": 3},
            },
        ],
    }
    market = {
        "symbol": "BTC/EUR",
        "provider_id": "ccxt:kraken",
        "instrument_id": "ccxt:kraken:spot:BTC/EUR",
    }

    assert store.record_strategy_research("task-1", market, "1h", research) == 2
    # A retry of the same task cannot duplicate the research evidence.
    assert store.record_strategy_research("task-1", market, "1h", research) == 0
    rows = store.strategy_history(limit=10)
    assert len(rows) == 2
    by_id = {row["strategy_id"]: row for row in rows}
    rejected = by_id["ema_12_26"]
    assert rejected["eligible"] is False
    assert rejected["validation_failures"] == ["does_not_beat_buy_and_hold"]
    assert rejected["result"]["cost_sensitivity"]["2.0"]["beats_buy_and_hold"] is False
    assert rejected["result"]["market_regime"]["structure"] == "trending_up"
    assert rejected["result"]["data_quality"]["bars"] == 250
    assert rejected["result"]["out_of_sample"]["sharpe_per_bar"] == -0.12
    promoted = by_id["rsi_14_reversion"]
    assert promoted["eligible"] is True
    assert promoted["strategy_version"].startswith("rsi_14_reversion:logic-v1:config-")
    assert rejected["instrument_id"] == market["instrument_id"]

    restarted = AutonomousStore(path)
    assert len(restarted.strategy_history(limit=10)) == 2
    assert len(restarted.strategy_history(strategy_id="ema_12_26")) == 1
    assert len(restarted.strategy_history(instrument_id=market["instrument_id"])) == 2


def test_strategy_research_history_ignores_invalid_or_empty_research(tmp_path):
    store = AutonomousStore(str(tmp_path / "empty-strategy-history.db"))
    market = {"symbol": "BTC/EUR", "provider_id": "ccxt:kraken"}
    assert store.record_strategy_research("task-1", market, "1h", {}) == 0
    assert store.record_strategy_research(
        "task-2", {}, "1h", {"candidates": [{"strategy_id": "ema_12_26"}]}
    ) == 0
    assert store.strategy_history() == []


def test_market_scan_task_persists_strategy_candidates(tmp_path):
    import asyncio

    class FakeScout:
        def scan(self):
            return {
                "timeframe": "1h",
                "analyses": [{
                    "market": {
                        "symbol": "BTC/EUR",
                        "provider_id": "ccxt:kraken",
                        "instrument_id": "ccxt:kraken:spot:BTC/EUR",
                    },
                    "strategy_research": {
                        "method": "test",
                        "candidates": [{
                            "strategy_id": "ema_12_26",
                            "eligible": False,
                            "validation_failures": ["insufficient_oos_trades"],
                        }],
                    },
                }],
            }

    store = AutonomousStore(str(tmp_path / "market-scan-history.db"))
    worker = AutonomousAgent(store, scout=FakeScout())
    task = {"id": "scan-task-1", "kind": "market_scan", "payload": {}}
    result = asyncio.run(worker._execute(task))

    assert result["analysed"] if "analysed" in result else result["analyses"]
    history = store.strategy_history()
    assert len(history) == 1
    assert history[0]["task_id"] == "scan-task-1"
    assert history[0]["strategy_id"] == "ema_12_26"


def test_autonomous_crypto_cycle_values_usd_cash_with_fresh_fx(tmp_path):
    import asyncio
    from datetime import datetime, timezone

    from app.portfolio import PaperPortfolio

    timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)

    class FakeFxProvider:
        def exchange_rate(self, symbol):
            assert symbol == "EUR/USD"
            return {
                "symbol": "EUR/USD", "rate": 1.1,
                "timestamp": timestamp, "provider_id": "twelve_data",
            }

    class FakeMarketData:
        def get(self, provider_id):
            assert provider_id == "twelve_data"
            return FakeFxProvider()

    class FakeScout:
        market_data = FakeMarketData()

        def scan(self, include_symbols=None):
            return {"analyses": [], "included_positions": include_symbols or []}

    class FakeDecision:
        action = "hold"
        proposal = None
        risk = None

        def model_dump(self):
            return {"action": "hold", "rationale": "No entry candidate"}

    class CapturingChief:
        portfolio_snapshot = None

        def decide(self, scan, portfolio):
            self.portfolio_snapshot = portfolio
            return FakeDecision()

    portfolio = PaperPortfolio(str(tmp_path / "multi-currency-worker.db"), 100)
    portfolio.convert_currency(
        "EUR", "USD", 50, 1.1, 0, "worker-usd-cash", "twelve_data", timestamp,
    )
    chief = CapturingChief()
    store = AutonomousStore(str(tmp_path / "multi-currency-agent.db"))
    worker = AutonomousAgent(store, scout=FakeScout(), chief=chief, portfolio=portfolio)

    result = asyncio.run(worker._execute({
        "id": "multi-currency-decision",
        "kind": "decision_cycle",
        "payload": {},
    }))

    assert result["execution"] == "no_order"
    assert chief.portfolio_snapshot["valuation_complete"] is True
    assert chief.portfolio_snapshot["equity"] == pytest.approx(100)
    assert chief.portfolio_snapshot["fx_observations"]["USD"]["provider_id"] == "twelve_data"


def test_old_worker_cannot_finish_or_fail_after_lease_reclaim(tmp_path):
    store = AutonomousStore(str(tmp_path / "lease-fencing.db"))
    task = store.enqueue("health_check", {})
    old_claim = store.claim_due_task()
    assert old_claim["lease_token"]

    with store.connect() as db:
        db.execute(
            "UPDATE agent_tasks SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?",
            (task["id"],),
        )
    assert store.recover_expired_tasks() == 1
    new_claim = store.claim_due_task()
    assert new_claim["lease_token"] != old_claim["lease_token"]

    assert store.finish_task(task["id"], {"from": "old"}, old_claim["lease_token"]) is False
    assert store.fail_task(old_claim, "stale worker failure") is False
    assert store.finish_task(task["id"], {"from": "new"}, new_claim["lease_token"]) is True
    assert store.overview()["tasks"][0]["status"] == "completed"


def test_task_lease_heartbeat_extends_only_the_current_claim(tmp_path):
    store = AutonomousStore(str(tmp_path / "lease-heartbeat.db"))
    task = store.enqueue("health_check", {})
    claimed = store.claim_due_task(lease_seconds=5)
    assert store.active_task_ids() == {task["id"]}
    assert store.extend_task_lease(task["id"], claimed["lease_token"], 120) is True
    assert store.extend_task_lease(task["id"], "wrong-token", 120) is False

    with store.connect() as db:
        row = db.execute(
            "SELECT lease_until,lease_token FROM agent_tasks WHERE id=?",
            (task["id"],),
        ).fetchone()
    assert row["lease_token"] == claimed["lease_token"]
    assert (datetime.fromisoformat(row["lease_until"]) - utcnow()).total_seconds() > 60


def test_recovered_task_clears_old_lease_token(tmp_path):
    store = AutonomousStore(str(tmp_path / "lease-recover.db"))
    task = store.enqueue("health_check", {})
    first = store.claim_due_task()
    with store.connect() as db:
        db.execute(
            "UPDATE agent_tasks SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?",
            (task["id"],),
        )
    assert store.recover_expired_tasks() == 1
    with store.connect() as db:
        row = db.execute("SELECT lease_token FROM agent_tasks WHERE id=?", (task["id"],)).fetchone()
    assert row["lease_token"] is None
    second = store.claim_due_task()
    assert second["lease_token"] != first["lease_token"]


def test_expired_lease_cannot_be_renewed_or_completed_before_reclaim(tmp_path):
    store = AutonomousStore(str(tmp_path / "expired-lease.db"))
    task = store.enqueue("health_check", {})
    claimed = store.claim_due_task()
    with store.connect() as db:
        db.execute(
            "UPDATE agent_tasks SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?",
            (task["id"],),
        )
    assert store.extend_task_lease(task["id"], claimed["lease_token"], 120) is False
    assert store.finish_task(task["id"], {"ok": True}, claimed["lease_token"]) is False
    assert store.fail_task(claimed, "late failure") is False


def test_legacy_task_table_migrates_lease_token_column(tmp_path):
    import sqlite3

    path = str(tmp_path / "legacy-agent.db")
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE agent_tasks ("
            "id TEXT PRIMARY KEY,kind TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,"
            "due_at TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,lease_until TEXT,"
            "result TEXT,error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"
        )
    store = AutonomousStore(path)
    with store.connect() as db:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(agent_tasks)")}
    assert "lease_token" in columns


def test_decision_cycle_resumes_persisted_risk_approved_intent_without_rescanning(tmp_path):
    import asyncio
    from datetime import datetime, timezone

    from app.portfolio import PaperPortfolio

    market_timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    path = str(tmp_path / "resume-approved-order.db")
    task_id = "resume-approved-task"
    request = {
        "symbol": "BTC/EUR", "side": "buy", "quantity": 0.001, "price": 10000,
        "fee_rate": 0.001, "slippage_rate": 0.001, "max_fraction": 1,
        "minimum": 1, "client_order_id": task_id,
        "provider_id": "ccxt:kraken", "instrument_id": "ccxt:kraken:spot:BTC/EUR",
        "strategy_id": "ema_12_26", "stop_loss": 9800, "take_profit": 10400,
        "market_timestamp": market_timestamp,
    }
    portfolio = PaperPortfolio(path, 20)
    portfolio.record_order_intent(
        task_id, request, {"action": "buy", "risk": {"accepted": True}},
    )

    class ScoutMustNotRescan:
        def scan(self, *args, **kwargs):
            raise AssertionError("A persisted approved intent must resume without new market research")

    from app.account_readiness import PaperAccountReadiness
    from app.execution_adapters import PaperPortfolioScopedAdapter

    store = AutonomousStore(path)
    readiness = PaperAccountReadiness(
        portfolio,
        PaperPortfolioScopedAdapter(portfolio, account_id="paper-primary"),
    )
    worker = AutonomousAgent(
        store,
        scout=ScoutMustNotRescan(),
        chief=object(),
        portfolio=portfolio,
        account_readiness=readiness,
    )
    result = asyncio.run(worker._execute({
        "id": task_id, "kind": "decision_cycle", "payload": {},
    }))

    assert result["execution"] == "paper_order_resumed"
    assert result["order"]["client_order_id"] == task_id
    assert portfolio.get_order_intent(task_id)["status"] == "filled"
    assert len(portfolio.history()) == 1


def test_failed_decision_task_is_requeued_when_approved_order_is_unresolved(tmp_path):
    store = AutonomousStore(str(tmp_path / "requeue.db"))
    task = store.enqueue("decision_cycle", {}, due_at=utcnow() - timedelta(seconds=1))
    claimed = store.claim_due_task()
    assert claimed["id"] == task["id"]
    store.fail_task(claimed, "temporary database interruption", max_attempts=1)
    assert store.overview()["tasks"][0]["status"] == "failed"

    assert store.requeue_failed_decision_task(task["id"]) is True
    recovered = store.overview()["tasks"][0]
    assert recovered["status"] == "pending"
    assert recovered["attempts"] == 1
    assert store.requeue_failed_decision_task(task["id"]) is False


def test_failed_decision_task_requeue_has_a_finite_attempt_budget(tmp_path):
    store = AutonomousStore(str(tmp_path / "requeue-budget.db"))
    task = store.enqueue("decision_cycle", {}, due_at=utcnow() - timedelta(seconds=1))
    claimed = store.claim_due_task()
    store.fail_task(claimed, "temporary failure", max_attempts=1)
    with store.connect() as db:
        db.execute(
            "UPDATE agent_tasks SET attempts=6 WHERE id=?",
            (task["id"],),
        )
    assert store.requeue_failed_decision_task(task["id"], max_total_attempts=6) is False
    state = store.task_state(task["id"])
    assert state["status"] == "failed"
    assert state["attempts"] == 6


def test_decision_cycle_blocks_new_scan_when_another_intent_needs_reconciliation(tmp_path):
    import asyncio

    from app.portfolio import PaperPortfolio, PortfolioError

    path = str(tmp_path / "blocked-new-entry.db")
    portfolio = PaperPortfolio(path, 20)
    request = {
        "symbol": "BTC/EUR", "side": "buy", "quantity": 0.001, "price": 10000,
        "fee_rate": 0.001, "slippage_rate": 0.001, "max_fraction": 1,
        "minimum": 1, "client_order_id": "older-unresolved-intent",
        "provider_id": "ccxt:kraken", "instrument_id": "ccxt:kraken:spot:BTC/EUR",
        "strategy_id": None, "stop_loss": None, "take_profit": None,
    }
    portfolio.record_order_intent(
        "older-unresolved-intent", request, {"risk_accepted": True},
    )

    class ScoutMustNotRun:
        def scan(self, *args, **kwargs):
            raise AssertionError("New market scan must be blocked while another intent is unresolved")

    worker = AutonomousAgent(
        AutonomousStore(path), scout=ScoutMustNotRun(), chief=object(), portfolio=portfolio,
    )
    with pytest.raises(RuntimeError, match="blocked by unresolved"):
        asyncio.run(worker._execute({
            "id": "new-decision-task", "kind": "decision_cycle", "payload": {},
        }))


def test_decision_cycle_expires_stale_approved_intent_instead_of_filling(tmp_path):
    import asyncio
    from datetime import datetime, timezone

    from app.portfolio import PaperPortfolio

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    task_id = "stale-approved-task"
    path = str(tmp_path / "stale-approved-order.db")
    request = {
        "symbol": "BTC/EUR", "side": "buy", "quantity": 0.001, "price": 10000,
        "fee_rate": 0.001, "slippage_rate": 0.001, "max_fraction": 1,
        "minimum": 1, "client_order_id": task_id,
        "provider_id": "ccxt:kraken", "instrument_id": "ccxt:kraken:spot:BTC/EUR",
        "strategy_id": "ema_12_26", "stop_loss": 9800, "take_profit": 10400,
        "market_timestamp": now_ms - 600_000,
    }
    portfolio = PaperPortfolio(path, 20)
    portfolio.record_order_intent(task_id, request, {"action": "buy", "risk": {"accepted": True}})

    class ScoutMustNotRescan:
        def scan(self, *args, **kwargs):
            raise AssertionError("Stale approved intent must expire without new scan")

    worker = AutonomousAgent(
        AutonomousStore(path), scout=ScoutMustNotRescan(), chief=object(), portfolio=portfolio,
    )
    result = asyncio.run(worker._execute({
        "id": task_id, "kind": "decision_cycle", "payload": {},
    }))

    assert result["execution"] == "expired"
    assert portfolio.get_order_intent(task_id)["status"] == "expired"
    assert portfolio.history() == []


def test_demo_order_reconciliation_runs_even_when_strategy_worker_is_paused(tmp_path):
    import asyncio

    class FakeDemoReconciler:
        def __init__(self):
            self.calls = 0

        def run_once(self):
            self.calls += 1
            return {"claimed": False, "processed": 0}

    store = AutonomousStore(str(tmp_path / "paused-demo-reconcile.db"))
    reconciler = FakeDemoReconciler()
    worker = AutonomousAgent(
        store,
        poll_interval_seconds=0.01,
        demo_reconciler=reconciler,
    )

    async def run_worker():
        await worker.start()
        await asyncio.sleep(0.06)
        await worker.stop()

    asyncio.run(run_worker())

    assert store.state()["enabled"] == 0
    assert reconciler.calls >= 1



def test_strategy_history_persists_experimental_evidence_and_entry_signals(tmp_path):
    store = AutonomousStore(str(tmp_path / "strategy-history.db"))
    research = {
        "method": "test",
        "recommended_experimental_strategy": "rsi_14_reversion",
        "experimental_candidate_count": 1,
        "candidates": [{
            "strategy_id": "rsi_14_reversion",
            "eligible": False,
            "validation_failures": ["non_positive_oos_return"],
            "score": None,
            "experimental_eligible": True,
            "experimental_validation_failures": [],
            "experimental_score": 0.42,
            "current_entry_signal": False,
            "current_hold_signal": True,
        }],
    }
    inserted = store.record_strategy_research(
        "task-experimental",
        {
            "provider_id": "ccxt:kraken",
            "instrument_id": "ccxt:kraken:spot:BTC/EUR",
            "symbol": "BTC/EUR",
        },
        "1h",
        research,
    )
    assert inserted == 1
    item = store.strategy_history(limit=1)[0]
    assert item["eligible"] is False
    assert item["experimental_eligible"] is True
    assert item["experimental_status"] == "recorded"
    assert item["result"]["experimental_validation_failures"] == []
    assert item["result"]["experimental_score"] == pytest.approx(0.42)
    assert item["result"]["current_entry_signal"] is False
    assert item["result"]["current_hold_signal"] is True
    assert item["result"]["recommended_experimental_strategy"] == "rsi_14_reversion"


def test_strategy_history_marks_legacy_experimental_status_unknown(tmp_path):
    store = AutonomousStore(str(tmp_path / "strategy-legacy.db"))
    # Simulate a row written by the pre-experimental schema/code: its result
    # JSON contains no experimental keys at all.
    with store.connect() as db:
        db.execute(
            """
            INSERT INTO strategy_research_history(
                task_id, provider_id, instrument_id, symbol, timeframe,
                strategy_id, strategy_version, eligible, score,
                validation_failures, result, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "task-legacy", "ccxt:kraken", "ccxt:kraken:spot:ETH/EUR",
                "ETH/EUR", "1h", "ema_12_26", "legacy-v1", 0, None,
                '["insufficient_oos_trades"]', '{"description":"legacy row"}',
                utcnow().isoformat(),
            ),
        )
    item = store.strategy_history(limit=1)[0]
    assert item["experimental_eligible"] is None
    assert item["experimental_status"] == "legacy"
