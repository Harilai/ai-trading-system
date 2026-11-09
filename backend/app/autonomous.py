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
                last_decision_enqueued_at TEXT,
                last_equity_research_enqueued_at TEXT,
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
        with self.connect() as db:
            columns = {row['name'] for row in db.execute('PRAGMA table_info(agent_state)')}
            if 'last_decision_enqueued_at' not in columns:
                db.execute('ALTER TABLE agent_state ADD COLUMN last_decision_enqueued_at TEXT')
            if 'last_equity_research_enqueued_at' not in columns:
                db.execute('ALTER TABLE agent_state ADD COLUMN last_equity_research_enqueued_at TEXT')
        # Recover expired work on startup as well as during the worker loop.
        self.recover_expired_tasks()

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

    def recover_expired_tasks(self) -> int:
        """Return expired leases to the queue; safe to call repeatedly."""
        now = utcnow().isoformat()
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            recovered = db.execute(
                "UPDATE agent_tasks SET status='pending', lease_until=NULL, updated_at=? "
                "WHERE status='running' AND (lease_until IS NULL OR lease_until < ?)",
                (now, now),
            ).rowcount
        if recovered:
            self.audit("expired_tasks_recovered", {"count": recovered})
        return recovered

    def schedule_decision_cycle(self, interval_seconds: int = 300) -> dict[str, Any] | None:
        """Enqueue at most one periodic cycle; state and queue changes are atomic."""
        if interval_seconds < 60:
            raise ValueError("Decision-cycle interval must be at least 60 seconds")
        now = utcnow()
        now_text = now.isoformat()
        task_id = str(uuid4())
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute(
                "SELECT enabled, emergency_stop, last_decision_enqueued_at "
                "FROM agent_state WHERE id=1"
            ).fetchone()
            if not state["enabled"] or state["emergency_stop"]:
                return None
            active = db.execute(
                "SELECT id FROM agent_tasks WHERE kind='decision_cycle' "
                "AND status IN ('pending','running') LIMIT 1"
            ).fetchone()
            if active:
                return None
            last = state["last_decision_enqueued_at"]
            if last and now - datetime.fromisoformat(last) < timedelta(seconds=interval_seconds):
                return None
            db.execute(
                "INSERT INTO agent_tasks(id,kind,payload,status,due_at,created_at,updated_at) "
                "VALUES(?,'decision_cycle',?,'pending',?,?,?)",
                (task_id, json.dumps({"trigger": "scheduler"}), now_text, now_text, now_text),
            )
            db.execute(
                "UPDATE agent_state SET last_decision_enqueued_at=?, updated_at=? WHERE id=1",
                (now_text, now_text),
            )
        self.audit("scheduled_decision_cycle_enqueued", {"task_id": task_id})
        return {"id": task_id, "kind": "decision_cycle", "status": "pending", "due_at": now_text}

    def schedule_equity_research(self, interval_seconds: int = 21600) -> dict[str, Any] | None:
        """Enqueue a low-frequency read-only equity research scan without overlapping work."""
        if interval_seconds < 3600:
            raise ValueError("Equity research interval must be at least 3600 seconds")
        now = utcnow()
        now_text = now.isoformat()
        task_id = str(uuid4())
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute(
                "SELECT enabled, emergency_stop, last_equity_research_enqueued_at "
                "FROM agent_state WHERE id=1"
            ).fetchone()
            if not state["enabled"] or state["emergency_stop"]:
                return None
            active = db.execute(
                "SELECT id FROM agent_tasks WHERE kind='equity_research' "
                "AND status IN ('pending','running') LIMIT 1"
            ).fetchone()
            if active:
                return None
            last = state["last_equity_research_enqueued_at"]
            if last and now - datetime.fromisoformat(last) < timedelta(seconds=interval_seconds):
                return None
            db.execute(
                "INSERT INTO agent_tasks(id,kind,payload,status,due_at,created_at,updated_at) "
                "VALUES(?,'equity_research',?,'pending',?,?,?)",
                (task_id, json.dumps({"trigger": "scheduler", "read_only": True}), now_text, now_text, now_text),
            )
            db.execute(
                "UPDATE agent_state SET last_equity_research_enqueued_at=?, updated_at=? WHERE id=1",
                (now_text, now_text),
            )
        self.audit("scheduled_equity_research_enqueued", {"task_id": task_id})
        return {"id": task_id, "kind": "equity_research", "status": "pending", "due_at": now_text}

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
        now = utcnow().isoformat()
        with self.connect() as db:
            db.execute(
                "UPDATE agent_state SET last_heartbeat=?, updated_at=? WHERE id=1",
                (now, now),
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
                "UPDATE agent_tasks SET status='completed', result=?, lease_until=NULL, updated_at=? "
                "WHERE id=? AND status='running'",
                (json.dumps(result, ensure_ascii=False), now, task_id),
            )
        self.audit("task_completed", {"task_id": task_id, "result": result})

    def fail_task(self, task: dict[str, Any], error: str, max_attempts: int = 3) -> None:
        now = utcnow()
        retry = task["attempts"] < max_attempts
        due = (now + timedelta(seconds=min(300, 15 * 2 ** (task["attempts"] - 1)))).isoformat()
        with self.connect() as db:
            db.execute(
                "UPDATE agent_tasks SET status=?, due_at=?, error=?, lease_until=NULL, updated_at=? "
                "WHERE id=? AND status='running'",
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
    """Persistent worker for deterministic maintenance and market-analysis tasks."""

    def __init__(self, store: AutonomousStore, scout=None, chief=None, portfolio=None, equity_research=None):
        self.store = store
        self.scout = scout
        self.chief = chief
        self.portfolio = portfolio
        self.equity_research = equity_research
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
            # A live process must also reclaim leases that expire after startup.
            self.store.recover_expired_tasks()
            state = self.store.state()
            self.store.heartbeat()
            if state["enabled"] and not state["emergency_stop"]:
                self.store.schedule_decision_cycle(settings.decision_cycle_interval_seconds)
                if self.equity_research is not None:
                    self.store.schedule_equity_research(settings.equity_research_interval_seconds)
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
        if task["kind"] == "market_scan":
            if self.scout is None:
                raise RuntimeError("Market scanner is not configured")
            result = await asyncio.to_thread(self.scout.scan)
            self.store.remember("market_scan", {"task_id": task["id"], **result}, validated=True)
            return result
        if task["kind"] == "equity_research":
            if self.equity_research is None:
                raise RuntimeError("Equity/ETF research provider is not configured")
            result = await asyncio.to_thread(self.equity_research.scan)
            self.store.remember(
                "equity_etf_research",
                {"task_id": task["id"], **result},
                validated=True,
            )
            return result
        if task["kind"] == "decision_cycle":
            if self.scout is None or self.chief is None or self.portfolio is None:
                raise RuntimeError("Decision cycle dependencies are not configured")

            # A retry after a process crash must not place a second paper order.
            previous_order = await asyncio.to_thread(
                self.portfolio.get_trade_by_client_order_id,
                task["id"],
            )
            if previous_order is not None:
                result = {
                    "execution": "paper_order_replayed",
                    "order": previous_order,
                    "idempotent_replay": True,
                }
                self.store.remember("chief_decision", {"task_id": task["id"], **result}, validated=True)
                return result

            unvalued_portfolio = await asyncio.to_thread(self.portfolio.snapshot)
            held_positions = [
                {
                    "symbol": position["symbol"],
                    "provider_id": position.get("provider_id", "ccxt:kraken"),
                    "instrument_id": position.get("instrument_id"),
                }
                for position in unvalued_portfolio.get("positions", [])
            ]
            held_instruments = {
                item["instrument_id"] or f"{item['provider_id']}:spot:{item['symbol']}"
                for item in held_positions
            }
            scan = await asyncio.to_thread(self.scout.scan, held_positions)
            valuation_prices = {}
            for item in scan.get("analyses", []):
                market = item.get("market", {})
                symbol = str(market.get("symbol", "")).upper()
                provider_id = str(market.get("provider_id") or "ccxt:kraken")
                instrument_id = str(market.get("instrument_id") or f"{provider_id}:spot:{symbol}")
                price = market.get("bid") or market.get("last")
                if instrument_id in held_instruments and price is not None and float(price) > 0:
                    valuation_prices[instrument_id] = float(price)
            portfolio = await asyncio.to_thread(self.portfolio.snapshot, valuation_prices)
            decision = self.chief.decide(scan, portfolio)
            order = None
            execution = "no_order"
            if (
                decision.action in ("buy", "sell")
                and decision.proposal is not None
                and decision.risk is not None
                and decision.risk.accepted
            ):
                proposal = decision.proposal
                order = await asyncio.to_thread(
                    self.portfolio.execute,
                    proposal.symbol,
                    proposal.side,
                    decision.risk.approved_quantity,
                    proposal.reference_price,
                    settings.fee_rate,
                    settings.slippage_rate,
                    settings.max_order_fraction,
                    settings.min_order_value_eur,
                    client_order_id=task["id"],
                    provider_id=proposal.provider_id,
                    instrument_id=proposal.instrument_id,
                    strategy_id=decision.strategy_id if proposal.side == "buy" else None,
                    stop_loss=proposal.stop_loss,
                    take_profit=proposal.take_profit,
                )
                execution = "paper_order"
            result = {
                "scan": scan,
                "decision": decision.model_dump(),
                "order": order,
                "execution": execution,
            }
            self.store.remember("chief_decision", {"task_id": task["id"], **result}, validated=True)
            return result
        raise ValueError(f"Unsupported task kind in this phase: {task['kind']}")
