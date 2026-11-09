import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from .config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AutonomousStore:
    """Durable operational state, task queue, observations and audit trail."""

    def __init__(self, path: str | None = None):
        self.path = path or settings.database_path
        self.lock = RLock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS agent_state (
                id INTEGER PRIMARY KEY CHECK(id=1),
                enabled INTEGER NOT NULL DEFAULT 0,
                emergency_stop INTEGER NOT NULL DEFAULT 0,
                last_heartbeat TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_tasks (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL,
                due_at TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                lease_until TEXT,
                result TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_agent_tasks_due ON agent_tasks(status, due_at);
            CREATE TABLE IF NOT EXISTS agent_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                validated INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                details TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT OR IGNORE INTO agent_state(id, enabled, emergency_stop, updated_at)
            VALUES(1, 0, 0, CURRENT_TIMESTAMP);
            """)
            # Tasks left running by a process crash are made eligible for retry.
            now = utcnow().isoformat()
            db.execute(
                "UPDATE agent_tasks SET status='pending', lease_until=NULL, updated_at=? "
                "WHERE status='running' AND (lease_until IS NULL OR lease_until < ?)",
                (now, now),
            )

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def audit(self, action: str, details: dict[str, Any] | None = None) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO agent_audit(action,details,created_at) VALUES(?,?,?)",
                (action, json.dumps(details or {}, ensure_ascii=False), utcnow().isoformat()),
            )

    def state(self) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM agent_state WHERE id=1").fetchone()
        return dict(row)

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        with self.connect() as db:
            db.execute(
                "UPDATE agent_state SET enabled=?, updated_at=? WHERE id=1",
                (int(enabled), utcnow().isoformat()),
            )
        self.audit("agent_resumed" if enabled else "agent_paused")
        return self.state()

    def emergency_stop(self) -> dict[str, Any]:
        with self.connect() as db:
            db.execute(
                "UPDATE agent_state SET enabled=0, emergency_stop=1, updated_at=? WHERE id=1",
                (utcnow().isoformat(),),
            )
        self.audit("emergency_stop_activated")
        return self.state()

    def reset_emergency_stop(self) -> dict[str, Any]:
        with self.connect() as db:
            db.execute(
                "UPDATE agent_state SET emergency_stop=0, updated_at=? WHERE id=1",
                (utcnow().isoformat(),),
            )
        self.audit("emergency_stop_reset")
        return self.state()

    def heartbeat(self) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE agent_state SET last_heartbeat=?, updated_at=? WHERE id=1",
                (utcnow().isoformat(), utcnow().isoformat()),
            )

    def enqueue(self, kind: str, payload: dict[str, Any], due_at: datetime | None = None) -> dict[str, Any]:
        now = utcnow().isoformat()
        task_id = str(uuid4())
        due = (due_at or utcnow()).isoformat()
        with self.connect() as db:
            db.execute(
                "INSERT INTO agent_tasks(id,kind,payload,status,due_at,created_at,updated_at) "
                "VALUES(?,?,?,'pending',?,?,?)",
                (task_id, kind, json.dumps(payload), due, now, now),
            )
        self.audit("task_enqueued", {"task_id": task_id, "kind": kind})
        return {"id": task_id, "kind": kind, "status": "pending", "due_at": due}

    def claim_due_task(self, lease_seconds: int = 120) -> dict[str, Any] | None:
        now = utcnow()
        lease = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM agent_tasks WHERE status='pending' AND due_at<=? "
                "ORDER BY due_at, created_at LIMIT 1",
                (now.isoformat(),),
            ).fetchone()
            if row is None:
                return None
            changed = db.execute(
                "UPDATE agent_tasks SET status='running', attempts=attempts+1, lease_until=?, updated_at=? "
                "WHERE id=? AND status='pending'",
                (lease, now.isoformat(), row["id"]),
            ).rowcount
            if changed != 1:
                return None
            task = dict(row)
            task["attempts"] += 1
            task["payload"] = json.loads(task["payload"])
            return task

    def finish_task(self, task_id: str, result: dict[str, Any]) -> None:
        now = utcnow().isoformat()
        with self.connect() as db:
            db.execute(
                "UPDATE agent_tasks SET status='completed', result=?, lease_until=NULL, updated_at=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False), now, task_id),
            )
        self.audit("task_completed", {"task_id": task_id, "result": result})

    def fail_task(self, task: dict[str, Any], error: str, max_attempts: int = 3) -> None:
        now = utcnow()
        retry = task["attempts"] < max_attempts
        due = (now + timedelta(seconds=min(300, 15 * 2 ** (task["attempts"] - 1)))).isoformat()
        with self.connect() as db:
            db.execute(
                "UPDATE agent_tasks SET status=?, due_at=?, error=?, lease_until=NULL, updated_at=? WHERE id=?",
                ("pending" if retry else "failed", due, error[:2000], now.isoformat(), task["id"]),
            )
        self.audit("task_retry_scheduled" if retry else "task_failed",
                   {"task_id": task["id"], "error": error[:500]})

    def remember(self, category: str, content: dict[str, Any], validated: bool = False) -> int:
        with self.connect() as db:
            cur = db.execute(
                "INSERT INTO agent_memory(category,content,validated,created_at) VALUES(?,?,?,?)",
                (category, json.dumps(content, ensure_ascii=False), int(validated), utcnow().isoformat()),
            )
            return int(cur.lastrowid)

    def overview(self) -> dict[str, Any]:
        state = self.state()
        with self.connect() as db:
            tasks = [dict(r) for r in db.execute(
                "SELECT id,kind,status,due_at,attempts,error,created_at,updated_at "
                "FROM agent_tasks ORDER BY created_at DESC LIMIT 50"
            )]
            memory = [dict(r) for r in db.execute(
                "SELECT id,category,content,validated,created_at FROM agent_memory ORDER BY id DESC LIMIT 50"
            )]
            audit = [dict(r) for r in db.execute(
                "SELECT id,action,details,created_at FROM agent_audit ORDER BY id DESC LIMIT 50"
            )]
        for item in memory:
            item["content"] = json.loads(item["content"])
        for item in audit:
            item["details"] = json.loads(item["details"])
        return {"state": state, "tasks": tasks, "memory": memory, "audit": audit}


class AutonomousAgent:
    """Persistent worker shell. Only safe, deterministic maintenance tasks execute in this phase."""

    def __init__(self, store: AutonomousStore):
        self.store = store
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping.clear()
            self._task = asyncio.create_task(self._run(), name="autonomous-agent-loop")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stopping.is_set():
            state = self.store.state()
            self.store.heartbeat()
            if state["enabled"] and not state["emergency_stop"]:
                task = self.store.claim_due_task()
                if task:
                    try:
                        result = await self._execute(task)
                        self.store.finish_task(task["id"], result)
                    except Exception as exc:
                        self.store.fail_task(task, str(exc))
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    async def _execute(self, task: dict[str, Any]) -> dict[str, Any]:
        if task["kind"] == "health_check":
            state = self.store.state()
            result = {"agent_enabled": bool(state["enabled"]), "live_trading": False}
            self.store.remember("operational_observation", {"task_id": task["id"], **result})
            return result
        raise ValueError(f"Unsupported task kind in this phase: {task['kind']}")
