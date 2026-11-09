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
