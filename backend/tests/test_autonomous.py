from datetime import timedelta

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
    restarted.finish_task(task["id"], {"ok": True})
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
        def scan(self):
            return {"analyses": [{"market": {"symbol": "BTC/EUR"}}]}

    class FakeDecision:
        def model_dump(self):
            return {"action": "hold", "rationale": "No validated opportunity"}

    class FakeChief:
        def decide(self, scan, portfolio):
            assert scan["analyses"]
            assert portfolio["cash"] == 20
            return FakeDecision()

    class FakePortfolio:
        def snapshot(self):
            return {"cash": 20}

    store = AutonomousStore(str(tmp_path / "decision.db"))
    worker = AutonomousAgent(store, scout=FakeScout(), chief=FakeChief(), portfolio=FakePortfolio())
    task = {"id": "decision-test", "kind": "decision_cycle", "payload": {}}
    result = asyncio.run(worker._execute(task))
    assert result["execution"] == "proposal_only"
    assert result["decision"]["action"] == "hold"
    memory = store.overview()["memory"]
    assert memory[0]["category"] == "chief_decision"


def test_decision_cycle_executes_only_risk_approved_paper_order_once(tmp_path):
    import asyncio

    from app.agents.contracts import RiskDecision, TradeProposal
    from app.chief import ChiefDecision

    class FakeScout:
        def scan(self, include_symbols=None):
            return {"analyses": [], "included_positions": include_symbols or []}

    class FakeChief:
        def decide(self, scan, portfolio):
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
            self.execute_calls = 0

        def snapshot(self):
            return {"cash": 20, "equity": 20, "positions": []}

        def get_trade_by_client_order_id(self, client_order_id):
            if self.order and self.order["client_order_id"] == client_order_id:
                return self.order
            return None

        def execute(self, *args, **kwargs):
            self.execute_calls += 1
            self.order = {
                "symbol": args[0],
                "side": args[1],
                "client_order_id": kwargs["client_order_id"],
                "strategy_id": kwargs["strategy_id"],
                "stop_loss": kwargs["stop_loss"],
                "take_profit": kwargs["take_profit"],
            }
            return self.order

    store = AutonomousStore(str(tmp_path / "paper-cycle.db"))
    portfolio = FakePortfolio()
    worker = AutonomousAgent(store, scout=FakeScout(), chief=FakeChief(), portfolio=portfolio)
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
