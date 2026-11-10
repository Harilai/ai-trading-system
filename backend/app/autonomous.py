import asyncio
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from .config import settings
from .fx import invert_fx_observation, validate_fx_observation
from .market_data import MarketDataError
from .portfolio import PortfolioError


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
            CREATE TABLE IF NOT EXISTS strategy_research_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                instrument_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                eligible INTEGER NOT NULL,
                score REAL,
                validation_failures TEXT NOT NULL,
                result TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(task_id, instrument_id, strategy_id)
            );
            CREATE INDEX IF NOT EXISTS idx_strategy_history_lookup
                ON strategy_research_history(instrument_id, strategy_id, created_at DESC);
            INSERT OR IGNORE INTO agent_state(id, enabled, emergency_stop, updated_at)
            VALUES(1, 0, 0, CURRENT_TIMESTAMP);
            """)
        with self.connect() as db:
            columns = {row['name'] for row in db.execute('PRAGMA table_info(agent_state)')}
            if 'last_decision_enqueued_at' not in columns:
                db.execute('ALTER TABLE agent_state ADD COLUMN last_decision_enqueued_at TEXT')
            if 'last_equity_research_enqueued_at' not in columns:
                db.execute('ALTER TABLE agent_state ADD COLUMN last_equity_research_enqueued_at TEXT')
            task_columns = {row['name'] for row in db.execute('PRAGMA table_info(agent_tasks)')}
            if 'lease_token' not in task_columns:
                db.execute('ALTER TABLE agent_tasks ADD COLUMN lease_token TEXT')
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
                "UPDATE agent_tasks SET status='pending', lease_until=NULL, lease_token=NULL, updated_at=? "
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
        if lease_seconds < 1:
            raise ValueError("Task lease must be at least one second")
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
            lease_token = str(uuid4())
            changed = db.execute(
                "UPDATE agent_tasks SET status='running', attempts=attempts+1, lease_until=?, lease_token=?, updated_at=? "
                "WHERE id=? AND status='pending'",
                (lease, lease_token, now.isoformat(), row["id"]),
            ).rowcount
            if changed != 1:
                return None
            task = dict(row)
            task["attempts"] += 1
            task["lease_token"] = lease_token
            task["payload"] = json.loads(task["payload"])
            return task

    def extend_task_lease(
        self,
        task_id: str,
        lease_token: str,
        lease_seconds: int = 120,
    ) -> bool:
        if not lease_token or lease_seconds < 1:
            return False
        now = utcnow()
        lease = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self.connect() as db:
            changed = db.execute(
                "UPDATE agent_tasks SET lease_until=?, updated_at=? "
                "WHERE id=? AND status='running' AND lease_token=? AND lease_until>=?",
                (lease, now.isoformat(), task_id, lease_token, now.isoformat()),
            ).rowcount
        return changed == 1

    def active_task_ids(self) -> set[str]:
        now = utcnow().isoformat()
        with self.connect() as db:
            rows = db.execute(
                "SELECT id FROM agent_tasks WHERE status='running' AND lease_until>=?",
                (now,),
            ).fetchall()
        return {str(row["id"]) for row in rows}

    def finish_task(
        self,
        task_id: str,
        result: dict[str, Any],
        lease_token: str,
    ) -> bool:
        if not lease_token:
            return False
        now = utcnow().isoformat()
        with self.connect() as db:
            changed = db.execute(
                "UPDATE agent_tasks SET status='completed', result=?, lease_until=NULL, lease_token=NULL, updated_at=? "
                "WHERE id=? AND status='running' AND lease_token=? AND lease_until>=?",
                (json.dumps(result, ensure_ascii=False), now, task_id, lease_token, now),
            ).rowcount
        if changed != 1:
            return False
        self.audit("task_completed", {"task_id": task_id, "result": result})
        return True

    def fail_task(
        self,
        task: dict[str, Any],
        error: str,
        max_attempts: int = 3,
    ) -> bool:
        lease_token = task.get("lease_token")
        if not lease_token:
            return False
        now = utcnow()
        retry = task["attempts"] < max_attempts
        due = (now + timedelta(seconds=min(300, 15 * 2 ** (task["attempts"] - 1)))).isoformat()
        with self.connect() as db:
            changed = db.execute(
                "UPDATE agent_tasks SET status=?, due_at=?, error=?, lease_until=NULL, lease_token=NULL, updated_at=? "
                "WHERE id=? AND status='running' AND lease_token=? AND lease_until>=?",
                ("pending" if retry else "failed", due, error[:2000], now.isoformat(), task["id"], lease_token, now.isoformat()),
            ).rowcount
        if changed != 1:
            return False
        self.audit("task_retry_scheduled" if retry else "task_failed",
                   {"task_id": task["id"], "error": error[:500]})
        return True

    def requeue_failed_decision_task(self, task_id: str) -> bool:
        """Resume a durable approved order after bounded task retries are exhausted."""
        now = utcnow().isoformat()
        with self.lock, self.connect() as db:
            changed = db.execute(
                "UPDATE agent_tasks SET status='pending',attempts=0,due_at=?,lease_until=NULL,"
                "error=NULL,updated_at=? WHERE id=? AND kind='decision_cycle' AND status='failed'",
                (now, now, task_id),
            ).rowcount
        if changed:
            self.audit("approved_order_task_requeued", {"task_id": task_id})
        return changed == 1

    def remember(self, category: str, content: dict[str, Any], validated: bool = False) -> int:
        with self.connect() as db:
            cur = db.execute(
                "INSERT INTO agent_memory(category,content,validated,created_at) VALUES(?,?,?,?)",
                (category, json.dumps(content, ensure_ascii=False), int(validated), utcnow().isoformat()),
            )
            return int(cur.lastrowid)

    def record_strategy_research(
        self,
        task_id: str,
        market: dict[str, Any],
        timeframe: str,
        research: dict[str, Any],
    ) -> int:
        """Persist candidate-level validation evidence with a reproducible config fingerprint."""
        candidates = research.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return 0
        provider_id = str(market.get("provider_id") or "unknown")
        symbol = str(market.get("symbol") or "").upper()
        instrument_id = str(
            market.get("instrument_id") or f"{provider_id}:unknown:{symbol}"
        )
        if not symbol or not instrument_id:
            return 0
        config = {
            "method": research.get("method"),
            "cost_model": research.get("cost_model", {}),
            "promotion_gates": research.get("promotion_gates", {}),
        }
        config_fingerprint = hashlib.sha256(
            json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        now = utcnow().isoformat()
        rows = []
        for candidate in candidates:
            if not isinstance(candidate, dict) or not candidate.get("strategy_id"):
                continue
            strategy_id = str(candidate["strategy_id"])
            strategy_version = f"{strategy_id}:logic-v1:config-{config_fingerprint}"
            failures = candidate.get("validation_failures", [])
            result = {
                "description": candidate.get("description"),
                "in_sample": candidate.get("in_sample"),
                "out_of_sample": candidate.get("out_of_sample"),
                "benchmark_excess_return": candidate.get("benchmark_excess_return"),
                "beats_buy_and_hold": candidate.get("beats_buy_and_hold"),
                "walk_forward": candidate.get("walk_forward"),
                "cost_sensitivity": candidate.get("cost_sensitivity"),
                "score": candidate.get("score"),
                "market_regime": research.get("market_regime"),
            }
            rows.append((
                task_id, provider_id, instrument_id, symbol, str(timeframe or "unknown"),
                strategy_id, strategy_version, int(bool(candidate.get("eligible"))),
                candidate.get("score"), json.dumps(failures, ensure_ascii=False),
                json.dumps(result, ensure_ascii=False), now,
            ))
        if not rows:
            return 0
        with self.lock, self.connect() as db:
            changes_before = db.total_changes
            db.executemany(
                """
                INSERT OR IGNORE INTO strategy_research_history(
                    task_id, provider_id, instrument_id, symbol, timeframe,
                    strategy_id, strategy_version, eligible, score,
                    validation_failures, result, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                rows,
            )
            inserted_count = db.total_changes - changes_before
        self.audit("strategy_research_persisted", {
            "task_id": task_id, "instrument_id": instrument_id,
            "candidate_count": inserted_count, "config_fingerprint": config_fingerprint,
        })
        return inserted_count

    def strategy_history(
        self,
        *,
        limit: int = 100,
        strategy_id: str | None = None,
        instrument_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        clauses = []
        params: list[Any] = []
        if strategy_id:
            clauses.append("strategy_id=?")
            params.append(strategy_id)
        if instrument_id:
            clauses.append("instrument_id=?")
            params.append(instrument_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self.connect() as db:
            rows = [
                dict(row) for row in db.execute(
                    f"SELECT * FROM strategy_research_history {where} "
                    "ORDER BY created_at DESC, id DESC LIMIT ?",
                    params,
                )
            ]
        for row in rows:
            row["eligible"] = bool(row["eligible"])
            row["validation_failures"] = json.loads(row["validation_failures"])
            row["result"] = json.loads(row["result"])
        return rows

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

    def __init__(
        self,
        store: AutonomousStore,
        scout=None,
        chief=None,
        portfolio=None,
        equity_research=None,
        *,
        poll_interval_seconds: float = 5.0,
    ):
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self.store = store
        self.poll_interval_seconds = poll_interval_seconds
        self.scout = scout
        self.chief = chief
        self.portfolio = portfolio
        self.equity_research = equity_research
        self.lease_seconds = 120
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
            try:
                # Reclaim leases and refresh heartbeat on every iteration.
                self.store.recover_expired_tasks()
                state = self.store.state()
                self.store.heartbeat()
                if self.portfolio is not None:
                    reconciliation = await asyncio.to_thread(
                        self.portfolio.reconcile_order_intents
                    )
                    if reconciliation["reconciled_filled"] or reconciliation["reconciliation_required"]:
                        self.store.audit("paper_order_reconciliation", reconciliation)
                    for intent in await asyncio.to_thread(self.portfolio.list_order_intents, 1000):
                        if intent["status"] == "risk_approved":
                            self.store.requeue_failed_decision_task(intent["client_order_id"])
                if state["enabled"] and not state["emergency_stop"]:
                    self.store.schedule_decision_cycle(settings.decision_cycle_interval_seconds)
                    if self.equity_research is not None:
                        self.store.schedule_equity_research(settings.equity_research_interval_seconds)
                    task = self.store.claim_due_task(lease_seconds=self.lease_seconds)
                    if task:
                        heartbeat = asyncio.create_task(self._heartbeat_task_lease(task))
                        try:
                            result = await self._execute(task)
                            completed = self.store.finish_task(
                                task["id"], result, task["lease_token"]
                            )
                            if not completed:
                                self.store.audit("task_lease_lost", {"task_id": task["id"]})
                        except Exception as exc:
                            self.store.fail_task(task, str(exc))
                        finally:
                            heartbeat.cancel()
                            try:
                                await heartbeat
                            except asyncio.CancelledError:
                                pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Database/provider failures outside task execution must not kill
                # the persistent worker process. Retry on the next poll.
                try:
                    self.store.audit("worker_loop_error", {"error": str(exc)[:1000]})
                except Exception:
                    pass
            try:
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=self.poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass

    async def _heartbeat_task_lease(self, task: dict[str, Any]) -> None:
        interval = max(1.0, self.lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                extended = await asyncio.to_thread(
                    self.store.extend_task_lease,
                    task["id"],
                    task["lease_token"],
                    self.lease_seconds,
                )
            except Exception as exc:
                try:
                    self.store.audit("task_lease_heartbeat_error", {
                        "task_id": task["id"], "error": str(exc)[:500],
                    })
                except Exception:
                    pass
                return
            if not extended:
                return

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
            for item in result.get("analyses", []):
                self.store.record_strategy_research(
                    task["id"], item.get("market", {}), result.get("timeframe", "unknown"),
                    item.get("strategy_research", {}),
                )
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

            # Reconcile the durable intent against the trade ledger before doing
            # any new research. A retry resumes the exact approved request.
            await asyncio.to_thread(self.portfolio.reconcile_order_intents)
            previous_order = await asyncio.to_thread(
                self.portfolio.get_trade_by_client_order_id,
                task["id"],
            )
            if previous_order is not None:
                filled_intent = await asyncio.to_thread(
                    self.portfolio.get_order_intent, task["id"],
                )
                if filled_intent and filled_intent["status"] == "reconciliation_required":
                    raise RuntimeError("Paper order requires reconciliation; replay is blocked")
                if filled_intent and filled_intent["status"] != "filled":
                    raise RuntimeError("Trade ledger exists without a completed order intent")
                result = {
                    "execution": "paper_order_replayed",
                    "order": previous_order,
                    "idempotent_replay": True,
                }
                self.store.remember("chief_decision", {"task_id": task["id"], **result}, validated=True)
                return result

            existing_intent = await asyncio.to_thread(
                self.portfolio.get_order_intent,
                task["id"],
            )
            if existing_intent is not None:
                if existing_intent["status"] == "risk_approved":
                    fresh = await asyncio.to_thread(
                        self.portfolio.order_intent_is_fresh,
                        task["id"],
                        settings.max_data_age_seconds,
                        settings.max_fx_data_age_seconds,
                    )
                    if not fresh:
                        await asyncio.to_thread(
                            self.portfolio.expire_order_intent,
                            task["id"],
                            "Approved intent market/FX evidence expired before paper fill",
                        )
                        result = {
                            "execution": "expired",
                            "order_intent": await asyncio.to_thread(
                                self.portfolio.get_order_intent, task["id"],
                            ),
                        }
                        self.store.remember("chief_decision", {"task_id": task["id"], **result}, validated=True)
                        return result
                    try:
                        resumed_order = await asyncio.to_thread(
                            self.portfolio.execute,
                            **existing_intent["request"],
                            require_order_intent=True,
                        )
                    except PortfolioError as exc:
                        await asyncio.to_thread(
                            self.portfolio.reject_order_intent,
                            task["id"],
                            str(exc),
                        )
                        result = {
                            "execution": "rejected",
                            "order_intent": await asyncio.to_thread(
                                self.portfolio.get_order_intent, task["id"],
                            ),
                        }
                    else:
                        result = {
                            "execution": "paper_order_resumed",
                            "order": resumed_order,
                            "idempotent_replay": True,
                        }
                    self.store.remember("chief_decision", {"task_id": task["id"], **result}, validated=True)
                    return result
                if existing_intent["status"] in {"rejected", "expired"}:
                    result = {
                        "execution": existing_intent["status"],
                        "order_intent": existing_intent,
                    }
                    self.store.remember("chief_decision", {"task_id": task["id"], **result}, validated=True)
                    return result
                raise RuntimeError(
                    f"Order intent {task['id']} is in {existing_intent['status']} state; manual reconciliation required"
                )

            unresolved_intents = [
                item for item in await asyncio.to_thread(self.portfolio.list_order_intents, 1000)
                if item["client_order_id"] != task["id"]
                and item["status"] in ("risk_approved", "reconciliation_required")
            ]
            if unresolved_intents:
                raise RuntimeError(
                    "New decision cycle blocked by unresolved paper order intent "
                    + unresolved_intents[0]["client_order_id"]
                )

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
            for item in scan.get("analyses", []):
                self.store.record_strategy_research(
                    task["id"], item.get("market", {}), scan.get("timeframe", "unknown"),
                    item.get("strategy_research", {}),
                )
            valuation_prices = {}
            for item in scan.get("analyses", []):
                market = item.get("market", {})
                symbol = str(market.get("symbol", "")).upper()
                provider_id = str(market.get("provider_id") or "ccxt:kraken")
                instrument_id = str(market.get("instrument_id") or f"{provider_id}:spot:{symbol}")
                price = market.get("bid") or market.get("last")
                if instrument_id in held_instruments and price is not None and float(price) > 0:
                    valuation_prices[instrument_id] = float(price)
            fx_rates = {}
            has_usd = any(
                item.get("currency") == "USD" and abs(float(item.get("balance", 0))) > 1e-12
                for item in unvalued_portfolio.get("cash_balances", [])
            ) or any(
                str(item.get("quote_currency") or "EUR").upper() == "USD"
                for item in unvalued_portfolio.get("positions", [])
            )
            if has_usd:
                try:
                    fx_provider = await asyncio.to_thread(
                        self.scout.market_data.get, "twelve_data"
                    )
                    eur_usd = await asyncio.to_thread(fx_provider.exchange_rate, "EUR/USD")
                    validated_fx = validate_fx_observation(
                        eur_usd,
                        "EUR/USD",
                        max_age_seconds=settings.max_fx_data_age_seconds,
                    )
                    fx_rates["USD"] = invert_fx_observation(validated_fx)
                except MarketDataError:
                    # Keep valuation incomplete; Chief will fail closed.
                    pass

            # USD positions need a fresh Twelve Data mark as well as FX before
            # the EUR portfolio can be used for autonomous crypto risk sizing.
            for position in unvalued_portfolio.get("positions", []):
                if str(position.get("quote_currency") or "EUR").upper() != "USD":
                    continue
                instrument_id = position.get("instrument_id")
                try:
                    quote = await asyncio.to_thread(
                        self.scout.market_data.ticker,
                        position["symbol"],
                        provider_id=position.get("provider_id") or "twelve_data",
                    )
                    if (
                        quote.get("last") is not None
                        and (not quote.get("symbol") or str(quote["symbol"]).upper() == str(position["symbol"]).upper())
                        and (not quote.get("currency") or str(quote["currency"]).upper() == "USD")
                        and not self.scout.market_data.is_stale(quote, settings.max_data_age_seconds)
                    ):
                        valuation_prices[instrument_id] = float(quote["last"])
                except (MarketDataError, TypeError, ValueError, OverflowError):
                    continue

            if fx_rates:
                portfolio = await asyncio.to_thread(
                    self.portfolio.snapshot,
                    valuation_prices,
                    fx_rates,
                )
            else:
                portfolio = await asyncio.to_thread(
                    self.portfolio.snapshot,
                    valuation_prices,
                )
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
                decision_market = (decision.evidence or {}).get("market") or {}
                risk_fx = (portfolio.get("fx_observations") or {}).get("USD") or {}
                execution_request = {
                    "symbol": proposal.symbol,
                    "side": proposal.side,
                    "quantity": decision.risk.approved_quantity,
                    "price": proposal.reference_price,
                    "fee_rate": settings.fee_rate,
                    "slippage_rate": settings.slippage_rate,
                    "max_fraction": settings.max_order_fraction,
                    "minimum": settings.min_order_value_eur,
                    "client_order_id": task["id"],
                    "provider_id": proposal.provider_id,
                    "instrument_id": proposal.instrument_id,
                    "strategy_id": decision.strategy_id if proposal.side == "buy" else None,
                    "stop_loss": proposal.stop_loss,
                    "take_profit": proposal.take_profit,
                    "quote_currency": "EUR",
                    "fx_rate_to_eur": 1.0,
                    "fx_provider_id": None,
                    "fx_timestamp": None,
                    "market_timestamp": decision_market.get("timestamp"),
                    "risk_fx_rate_to_eur": float(risk_fx.get("rate", 1.0)),
                    "risk_fx_provider_id": risk_fx.get("provider_id"),
                    "risk_fx_timestamp": risk_fx.get("timestamp"),
                }
                await asyncio.to_thread(
                    self.portfolio.record_order_intent,
                    task["id"],
                    execution_request,
                    decision.model_dump(),
                )
                if hasattr(self.portfolio, "order_intent_is_fresh"):
                    fresh = await asyncio.to_thread(
                        self.portfolio.order_intent_is_fresh,
                        task["id"],
                        settings.max_data_age_seconds,
                        settings.max_fx_data_age_seconds,
                    )
                    if not fresh:
                        await asyncio.to_thread(
                            self.portfolio.expire_order_intent,
                            task["id"],
                            "Market/FX evidence expired before paper fill",
                        )
                        result = {
                            "execution": "expired",
                            "order_intent": await asyncio.to_thread(
                                self.portfolio.get_order_intent, task["id"],
                            ),
                        }
                        self.store.remember("chief_decision", {"task_id": task["id"], **result}, validated=True)
                        return result
                try:
                    order = await asyncio.to_thread(
                        self.portfolio.execute,
                        **execution_request,
                        require_order_intent=True,
                    )
                except PortfolioError as exc:
                    await asyncio.to_thread(
                        self.portfolio.reject_order_intent,
                        task["id"],
                        str(exc),
                    )
                    execution = "rejected"
                else:
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
