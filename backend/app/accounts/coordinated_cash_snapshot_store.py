from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4


class CoordinatedCashSnapshotError(RuntimeError):
    pass


class CoordinatedCashSnapshotStore:
    """Atomically links Alpaca-style financial metrics and cash positions."""

    def __init__(self, database_path: str, financial_metrics_store: Any, position_store: Any):
        self.database_path = str(Path(database_path).expanduser().resolve())
        self.financial_metrics_store = financial_metrics_store
        self.position_store = position_store
        if (financial_metrics_store.database_path != self.database_path
                or position_store.database_path != self.database_path):
            raise CoordinatedCashSnapshotError("All cash snapshot stores must share one database")
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS coordinated_cash_account_snapshots (
                    capture_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    provider_id TEXT NOT NULL,
                    account_alias TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    market_type TEXT NOT NULL CHECK(market_type='cash'),
                    financial_metrics_snapshot_id TEXT NOT NULL,
                    position_snapshot_id TEXT NOT NULL,
                    financial_observed_at TEXT NOT NULL,
                    positions_observed_at TEXT NOT NULL,
                    financial_coverage TEXT NOT NULL,
                    positions_coverage TEXT NOT NULL,
                    max_skew_ms INTEGER NOT NULL,
                    capture_status TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY(financial_metrics_snapshot_id)
                        REFERENCES account_financial_metric_snapshots(snapshot_id),
                    FOREIGN KEY(position_snapshot_id)
                        REFERENCES account_position_snapshots(snapshot_id)
                );
                CREATE INDEX IF NOT EXISTS idx_coordinated_cash_scope
                    ON coordinated_cash_account_snapshots(provider_id,account_alias,environment,recorded_at DESC);
                CREATE TRIGGER IF NOT EXISTS coordinated_cash_no_update
                    BEFORE UPDATE ON coordinated_cash_account_snapshots
                    BEGIN SELECT RAISE(ABORT,'coordinated cash snapshots are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS coordinated_cash_no_delete
                    BEFORE DELETE ON coordinated_cash_account_snapshots
                    BEGIN SELECT RAISE(ABORT,'coordinated cash snapshots are append-only'); END;
            """)

    def connect(self):
        db = sqlite3.connect(self.database_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("PRAGMA foreign_keys=ON")
        return _Connection(db)

    @staticmethod
    def _timestamp(value: str, field: str) -> datetime:
        if not isinstance(value, str):
            raise CoordinatedCashSnapshotError(f"{field} timestamp is required")
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise CoordinatedCashSnapshotError(f"{field} timestamp is invalid") from None
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise CoordinatedCashSnapshotError(f"{field} timestamp must include a timezone")
        stamp = stamp.astimezone(timezone.utc)
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        if age < -60 or age > 300:
            raise CoordinatedCashSnapshotError(f"{field} timestamp is stale or future-dated")
        return stamp

    def record_snapshot(
        self, request_id: str, financial_metrics: dict, positions: dict, *, max_skew_seconds: int = 5,
    ) -> dict:
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id.strip()) > 128:
            raise CoordinatedCashSnapshotError("Capture request_id is invalid")
        request_id = request_id.strip()
        if isinstance(max_skew_seconds, bool) or not isinstance(max_skew_seconds, int) or not 0 <= max_skew_seconds <= 30:
            raise CoordinatedCashSnapshotError("Maximum timestamp skew must be between 0 and 30 seconds")

        from .financial_metrics_store import normalize_financial_metrics
        normalized_metrics = normalize_financial_metrics(financial_metrics)
        normalized_positions, _ = self.position_store._normalize(positions)
        scope = (
            normalized_metrics["provider_id"], normalized_metrics["account_alias"],
            normalized_metrics["environment"], "cash",
        )
        position_scope = (
            normalized_positions["provider_id"], normalized_positions["account_alias"],
            normalized_positions["environment"], normalized_positions["market_type"],
        )
        if position_scope != scope or normalized_positions["market_type"] != "cash":
            raise CoordinatedCashSnapshotError("Financial metrics and cash positions identity/scope do not match")
        if positions.get("read_only") is not True or positions.get("execution_enabled") is not False:
            raise CoordinatedCashSnapshotError("Cash positions must be strictly read-only")
        if positions.get("withdrawals_enabled") is not False:
            raise CoordinatedCashSnapshotError("Cash positions must disable withdrawals")
        if positions.get("transfers_enabled", False) is not False or positions.get("wallet_signing_enabled", False) is not False:
            raise CoordinatedCashSnapshotError("Cash positions must disable transfers and wallet signing")
        financial_coverage = normalized_metrics["coverage_status"]
        positions_coverage = normalized_positions["coverage_status"]
        financial_time = self._timestamp(normalized_metrics["fetched_at"], "Financial metrics")
        positions_time = self._timestamp(normalized_positions["fetched_at"], "Positions")
        skew_ms = math.ceil(abs((financial_time - positions_time).total_seconds()) * 1000)
        if skew_ms > max_skew_seconds * 1000:
            raise CoordinatedCashSnapshotError("Financial metrics and positions exceed allowed timestamp skew")

        request_payload = {
            "scope": scope,
            "account_id": normalized_metrics["account_id"],
            "currency": normalized_metrics["currency"],
            "metrics": normalized_metrics["metrics"],
            "flags": normalized_metrics["flags"],
            "positions": normalized_positions["positions"],
            "financial_coverage": financial_coverage,
            "positions_coverage": positions_coverage,
            "max_skew_ms": skew_ms,
        }
        canonical = json.dumps(request_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        request_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT capture_id,request_hash FROM coordinated_cash_account_snapshots WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise CoordinatedCashSnapshotError("Capture request_id was already used with different data")
                capture_id = existing["capture_id"]
            else:
                metrics_id = self.financial_metrics_store._record_snapshot_in_transaction(
                    request_id, financial_metrics, db,
                )
                position_id = self.position_store._record_snapshot_in_transaction(positions, db)
                metrics_row = db.execute(
                    "SELECT content_hash FROM account_financial_metric_snapshots WHERE snapshot_id=?",
                    (metrics_id,),
                ).fetchone()
                position_row = db.execute(
                    "SELECT content_hash FROM account_position_snapshots WHERE snapshot_id=?",
                    (position_id,),
                ).fetchone()
                content_payload = {
                    **request_payload,
                    "financial_metrics_snapshot_id": metrics_id,
                    "position_snapshot_id": position_id,
                    "financial_content_hash": metrics_row["content_hash"],
                    "position_content_hash": position_row["content_hash"],
                }
                content_hash = hashlib.sha256(json.dumps(
                    content_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                ).encode("utf-8")).hexdigest()
                capture_id = str(uuid4())
                db.execute(
                    """INSERT INTO coordinated_cash_account_snapshots(
                        capture_id,request_id,provider_id,account_alias,environment,market_type,
                        financial_metrics_snapshot_id,position_snapshot_id,financial_observed_at,
                        positions_observed_at,financial_coverage,positions_coverage,max_skew_ms,
                        capture_status,request_hash,content_hash,recorded_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        capture_id, request_id, *scope, metrics_id, position_id,
                        financial_time.isoformat(), positions_time.isoformat(),
                        financial_coverage, positions_coverage, skew_ms,
                        "complete" if financial_coverage == positions_coverage == "complete" else "partial",
                        request_hash, content_hash, datetime.now(timezone.utc).isoformat(),
                    ),
                )
        return self.get_snapshot(capture_id)

    def get_by_request_id(self, request_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT capture_id FROM coordinated_cash_account_snapshots WHERE request_id=?",
                (request_id,),
            ).fetchone()
        return self.get_snapshot(row["capture_id"]) if row else None

    def get_snapshot(self, capture_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM coordinated_cash_account_snapshots WHERE capture_id=?",
                (capture_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["financial_metrics"] = self.financial_metrics_store.get_snapshot_by_id(
            result["financial_metrics_snapshot_id"]
        )
        result["positions"] = self.position_store.get_snapshot(result["position_snapshot_id"])
        return result


class _Connection:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None:
            self.db.commit()
        else:
            self.db.rollback()
        self.db.close()
        return False
