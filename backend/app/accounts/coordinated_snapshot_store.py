from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4


class CoordinatedSnapshotError(RuntimeError):
    pass


class CoordinatedAccountSnapshotStore:
    """Atomically links existing balance and position ledgers without copying their rows."""

    def __init__(self, database_path: str, balance_store: Any, position_store: Any):
        self.database_path = str(Path(database_path).expanduser().resolve())
        self.balance_store = balance_store
        self.position_store = position_store
        if (balance_store.database_path != self.database_path
                or position_store.database_path != self.database_path):
            raise CoordinatedSnapshotError("Coordinated and child snapshot stores must share one database")
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS coordinated_account_snapshots (
                    capture_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    provider_id TEXT NOT NULL,
                    account_alias TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    market_type TEXT NOT NULL,
                    balance_snapshot_id TEXT NOT NULL,
                    position_snapshot_id TEXT NOT NULL,
                    balances_observed_at TEXT NOT NULL,
                    positions_observed_at TEXT NOT NULL,
                    balances_timestamp_source TEXT NOT NULL,
                    positions_timestamp_source TEXT NOT NULL,
                    balances_coverage TEXT NOT NULL,
                    positions_coverage TEXT NOT NULL,
                    max_skew_ms INTEGER NOT NULL,
                    capture_status TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY(balance_snapshot_id) REFERENCES account_snapshots(snapshot_id),
                    FOREIGN KEY(position_snapshot_id) REFERENCES account_position_snapshots(snapshot_id)
                );
                CREATE INDEX IF NOT EXISTS idx_coordinated_account_snapshots_scope
                    ON coordinated_account_snapshots(
                        provider_id,account_alias,environment,market_type,recorded_at DESC
                    );
                CREATE TRIGGER IF NOT EXISTS coordinated_account_snapshots_no_update
                    BEFORE UPDATE ON coordinated_account_snapshots
                    BEGIN SELECT RAISE(ABORT,'coordinated account snapshots are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS coordinated_account_snapshots_no_delete
                    BEFORE DELETE ON coordinated_account_snapshots
                    BEGIN SELECT RAISE(ABORT,'coordinated account snapshots are append-only'); END;
                """
            )

    @staticmethod
    def _timestamp(value: Any, field: str) -> datetime:
        if not isinstance(value, str) or not value.strip():
            raise CoordinatedSnapshotError(f"{field} timestamp is required")
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            raise CoordinatedSnapshotError(f"{field} timestamp is invalid") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise CoordinatedSnapshotError(f"{field} timestamp must include a timezone")
        parsed = parsed.astimezone(timezone.utc)
        age = (datetime.now(timezone.utc) - parsed).total_seconds()
        if age < -60 or age > 300:
            raise CoordinatedSnapshotError(f"{field} timestamp is stale or future-dated")
        return parsed

    @staticmethod
    def _scope(snapshot: dict, field: str) -> tuple[str, str, str, str]:
        if not isinstance(snapshot, dict):
            raise CoordinatedSnapshotError(f"{field} snapshot must be an object")
        values = tuple(snapshot.get(key) for key in (
            "provider_id", "account_alias", "environment", "market_type"
        ))
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise CoordinatedSnapshotError(f"{field} snapshot identity is incomplete")
        return values[0].strip(), values[1].strip(), values[2].strip().lower(), values[3].strip().lower()

    @staticmethod
    def _coverage(snapshot: dict, field: str) -> str:
        value = snapshot.get("coverage_status")
        if value not in {"complete", "partial"}:
            raise CoordinatedSnapshotError(f"{field} coverage must be explicitly complete or partial")
        return value

    def record_snapshot(
        self,
        request_id: str,
        balance_snapshot: dict,
        position_snapshot: dict,
        *,
        max_skew_seconds: int = 5,
    ) -> dict:
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id.strip()) > 128:
            raise CoordinatedSnapshotError("Capture request_id is invalid")
        request_id = request_id.strip()
        if not isinstance(max_skew_seconds, int) or isinstance(max_skew_seconds, bool) or not 0 <= max_skew_seconds <= 30:
            raise CoordinatedSnapshotError("Maximum timestamp skew must be between 0 and 30 seconds")

        balance_scope = self._scope(balance_snapshot, "Balance")
        position_scope = self._scope(position_snapshot, "Position")
        if balance_scope != position_scope:
            raise CoordinatedSnapshotError("Balance and position snapshot identities do not match")
        provider_id, account_alias, environment, market_type = balance_scope
        if market_type != "spot":
            raise CoordinatedSnapshotError("Coordinated balance snapshots currently support spot only")
        for field, snapshot in (("Balance", balance_snapshot), ("Position", position_snapshot)):
            if snapshot.get("read_only") is not True or snapshot.get("execution_enabled") is not False:
                raise CoordinatedSnapshotError(f"{field} snapshot must be read-only")
            if snapshot.get("withdrawals_enabled") is not False:
                raise CoordinatedSnapshotError(f"{field} snapshot must disable withdrawals")
            if snapshot.get("transfers_enabled", False) is not False or snapshot.get("wallet_signing_enabled", False) is not False:
                raise CoordinatedSnapshotError(f"{field} snapshot must disable transfers and wallet signing")
        balances_coverage = self._coverage(balance_snapshot, "Balance")
        positions_coverage = self._coverage(position_snapshot, "Position")
        balance_time = self._timestamp(balance_snapshot.get("fetched_at"), "Balance")
        position_time = self._timestamp(position_snapshot.get("fetched_at"), "Position")
        skew_ms = math.ceil(abs((balance_time - position_time).total_seconds()) * 1000)
        if skew_ms > max_skew_seconds * 1000:
            raise CoordinatedSnapshotError("Balance and position observations exceed the allowed timestamp skew")

        normalized_balances, _ = self.balance_store._normalize(balance_snapshot)
        normalized_positions, _ = self.position_store._normalize(position_snapshot)
        request_payload = {
            "scope": [provider_id, account_alias, environment, market_type],
            "balances": normalized_balances,
            "positions": normalized_positions,
            "balances_coverage": balances_coverage,
            "positions_coverage": positions_coverage,
            "balances_timestamp_source": balance_snapshot.get("timestamp_source", "provider"),
            "positions_timestamp_source": position_snapshot.get("timestamp_source", "provider"),
            "max_skew_ms": skew_ms,
        }
        for key in ("balances_timestamp_source", "positions_timestamp_source"):
            if request_payload[key] not in {"provider", "retrieved_at"}:
                raise CoordinatedSnapshotError(f"{key} is invalid")
        canonical_request = json.dumps(request_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        request_hash = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()

        capture_id = None
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT capture_id,request_hash FROM coordinated_account_snapshots WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise CoordinatedSnapshotError("Capture request_id was already used with different snapshot data")
                capture_id = existing["capture_id"]
            else:
                balance_id = self.balance_store._record_snapshot_in_transaction(balance_snapshot, db)
                position_id = self.position_store._record_snapshot_in_transaction(position_snapshot, db)
                balance_record = db.execute(
                    "SELECT content_hash FROM account_snapshots WHERE snapshot_id=?", (balance_id,)
                ).fetchone()
                position_record = db.execute(
                    "SELECT content_hash FROM account_position_snapshots WHERE snapshot_id=?", (position_id,)
                ).fetchone()
                group_payload = {
                    **request_payload,
                    "balance_snapshot_id": balance_id,
                    "position_snapshot_id": position_id,
                    "balance_content_hash": balance_record["content_hash"],
                    "position_content_hash": position_record["content_hash"],
                }
                content_hash = hashlib.sha256(json.dumps(
                    group_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8")).hexdigest()
                capture_id = str(uuid4())
                db.execute(
                    """
                    INSERT INTO coordinated_account_snapshots(
                        capture_id,request_id,provider_id,account_alias,environment,market_type,
                        balance_snapshot_id,position_snapshot_id,balances_observed_at,positions_observed_at,
                        balances_timestamp_source,positions_timestamp_source,balances_coverage,positions_coverage,
                        max_skew_ms,capture_status,request_hash,content_hash,recorded_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        capture_id, request_id, provider_id, account_alias, environment, market_type,
                        balance_id, position_id, balance_time.isoformat(), position_time.isoformat(),
                        request_payload["balances_timestamp_source"], request_payload["positions_timestamp_source"],
                        balances_coverage, positions_coverage, skew_ms,
                        "complete" if balances_coverage == positions_coverage == "complete" else "partial",
                        request_hash, content_hash, datetime.now(timezone.utc).isoformat(),
                    ),
                )
        return self.get_snapshot(capture_id)

    def get_by_request_id(self, request_id: str) -> dict | None:
        if not isinstance(request_id, str) or not request_id.strip():
            return None
        with self.connect() as db:
            row = db.execute(
                "SELECT capture_id FROM coordinated_account_snapshots WHERE request_id=?",
                (request_id.strip(),),
            ).fetchone()
        return self.get_snapshot(row["capture_id"]) if row else None

    def get_snapshot(self, capture_id: str) -> dict | None:
        if not isinstance(capture_id, str) or not capture_id.strip():
            return None
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM coordinated_account_snapshots WHERE capture_id=?",
                (capture_id.strip(),),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["balances"] = self.balance_store.get_snapshot(result["balance_snapshot_id"])
        result["positions"] = self.position_store.get_snapshot(result["position_snapshot_id"])
        return result

    def list_snapshots(
        self, *, provider_id: str | None = None, account_alias: str | None = None,
        environment: str | None = None, market_type: str | None = None, limit: int = 50,
    ) -> list[dict]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
            raise CoordinatedSnapshotError("Coordinated snapshot limit must be between 1 and 200")
        clauses, params = [], []
        for field, value in (
            ("provider_id", provider_id), ("account_alias", account_alias),
            ("environment", environment), ("market_type", market_type),
        ):
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise CoordinatedSnapshotError(f"Snapshot {field} filter is invalid")
                clauses.append(f"{field}=?")
                params.append(value.strip().lower() if field in {"environment", "market_type"} else value.strip())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self.connect() as db:
            rows = db.execute(
                f"SELECT capture_id FROM coordinated_account_snapshots {where} "
                "ORDER BY recorded_at DESC,capture_id DESC LIMIT ?", params,
            ).fetchall()
        return [item for row in rows if (item := self.get_snapshot(row["capture_id"])) is not None]
