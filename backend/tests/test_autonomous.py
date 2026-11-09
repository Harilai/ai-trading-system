from datetime import timedelta
from app.autonomous import AutonomousStore, utcnow


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


def test_expired_running_task_is_recovered(tmp_path):
    path = str(tmp_path / "agent.db")
    store = AutonomousStore(path)
    task = store.enqueue("health_check", {})
    claimed = store.claim_due_task()
    assert claimed["id"] == task["id"]
    with store.connect() as db:
        db.execute("UPDATE agent_tasks SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?", (task["id"],))
    restarted = AutonomousStore(path)
    assert restarted.claim_due_task()["id"] == task["id"]
