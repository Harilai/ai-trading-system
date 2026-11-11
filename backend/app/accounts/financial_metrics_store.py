from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
from uuid import uuid4


class FinancialMetricsError(RuntimeError):
    pass


# Alpaca account fields with distinct domain semantics. Missing optional values
# remain null; they are never synthesized from another metric.
REQUIRED_METRICS = ("cash", "equity", "buying_power")
OPTIONAL_METRICS = (
    "portfolio_value", "regt_buying_power", "non_marginable_buying_power",
    "options_buying_power", "cash_withdrawable", "cash_transferable",
    "pending_transfer_in", "pending_transfer_out", "long_market_value",
    "short_market_value", "initial_margin", "maintenance_margin",
    "last_equity", "last_cash", "accrued_fees", "pending_reg_taf_fees",
)
SIGNED_METRICS = {"equity", "last_equity"}
ALL_METRICS = REQUIRED_METRICS + OPTIONAL_METRICS
_IDENTIFIER = re.compile(r"[A-Za-z0-9._:/-]{1,128}")
_CURRENCY = re.compile(r"[A-Z]{3,8}")
_DECIMAL = re.compile(r"-?\d+(?:\.\d+)?")


def _decimal(value: Any, field: str, *, signed: bool) -> str:
    if not isinstance(value, str) or not _DECIMAL.fullmatch(value):
        raise FinancialMetricsError(f"Financial metric {field} must be a plain decimal string")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise FinancialMetricsError(f"Financial metric {field} is invalid") from None
    if not amount.is_finite() or (not signed and amount < 0):
        raise FinancialMetricsError(f"Financial metric {field} is outside the accepted range")
    if len(amount.as_tuple().digits) > 80 or abs(amount.as_tuple().exponent) > 80:
        raise FinancialMetricsError(f"Financial metric {field} precision is unsupported")
    normalized = format(amount, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def normalize_financial_metrics(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise FinancialMetricsError("Financial metrics snapshot must be an object")
    identity = {}
    for field in ("provider_id", "account_alias", "environment", "account_id"):
        value = raw.get(field)
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value.strip()):
            raise FinancialMetricsError(f"Financial metrics {field} is invalid")
        identity[field] = value.strip()
    if identity["environment"].lower() != "paper":
        raise FinancialMetricsError("Financial metrics are restricted to Paper accounts")
    currency = raw.get("currency")
    if not isinstance(currency, str) or not _CURRENCY.fullmatch(currency):
        raise FinancialMetricsError("Financial metrics currency is missing or invalid")
    status = raw.get("account_status")
    if status != "ACTIVE":
        raise FinancialMetricsError("Alpaca Paper account is not in ACTIVE status")
    fetched_at = raw.get("fetched_at")
    if not isinstance(fetched_at, str):
        raise FinancialMetricsError("Financial metrics fetched_at is required")
    try:
        stamp = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError:
        raise FinancialMetricsError("Financial metrics fetched_at is invalid") from None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise FinancialMetricsError("Financial metrics fetched_at must include a timezone")
    stamp = stamp.astimezone(timezone.utc)
    if (datetime.now(timezone.utc) - stamp).total_seconds() > 300 or (stamp - datetime.now(timezone.utc)).total_seconds() > 60:
        raise FinancialMetricsError("Financial metrics timestamp is stale or in the future")
    metrics = {}
    for field in ALL_METRICS:
        value = raw.get(field)
        if field in REQUIRED_METRICS and value is None:
            raise FinancialMetricsError(f"Required financial metric {field} is missing")
        metrics[field] = None if value is None else _decimal(
            value, field, signed=field in SIGNED_METRICS,
        )
    flags = {}
    for field in ("trading_blocked", "transfers_blocked", "account_blocked"):
        value = raw.get(field)
        if not isinstance(value, bool):
            raise FinancialMetricsError(f"Financial metrics {field} must be boolean")
        flags[field] = value
    return {
        **identity,
        "currency": currency,
        "account_status": status,
        "fetched_at": stamp.isoformat(),
        "timestamp_source": "retrieved_at",
        "coverage_status": "complete" if all(metrics[name] is not None for name in REQUIRED_METRICS) else "partial",
        "schema_version": 1,
        "metrics": metrics,
        "flags": flags,
    }


class AccountFinancialMetricsStore:
    """Append-only, idempotent ledger for semantically typed account-level metrics."""

    def __init__(self, database_path: str):
        self.database_path = str(Path(database_path).expanduser().resolve())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS account_financial_metric_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    account_alias TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    account_status TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    coverage_status TEXT NOT NULL CHECK(coverage_status IN ('complete','partial')),
                    schema_version INTEGER NOT NULL,
                    metrics_json TEXT NOT NULL,
                    flags_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    UNIQUE(provider_id,account_alias,environment,request_id)
                );
                CREATE INDEX IF NOT EXISTS idx_financial_metrics_account
                    ON account_financial_metric_snapshots(
                        provider_id,account_alias,environment,fetched_at DESC
                    );
                CREATE TRIGGER IF NOT EXISTS financial_metrics_no_update
                    BEFORE UPDATE ON account_financial_metric_snapshots
                    BEGIN SELECT RAISE(ABORT,'financial metric snapshots are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS financial_metrics_no_delete
                    BEFORE DELETE ON account_financial_metric_snapshots
                    BEGIN SELECT RAISE(ABORT,'financial metric snapshots are append-only'); END;
                """
            )

    def connect(self):
        db = sqlite3.connect(self.database_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("PRAGMA foreign_keys=ON")
        return _Connection(db)

    @staticmethod
    def _result(row: sqlite3.Row) -> dict:
        result = dict(row)
        result["metrics"] = json.loads(result.pop("metrics_json"))
        result["flags"] = json.loads(result.pop("flags_json"))
        return result

    def record_snapshot(self, request_id: str, raw: dict) -> dict:
        if not isinstance(request_id, str) or not _IDENTIFIER.fullmatch(request_id):
            raise FinancialMetricsError("Financial metrics request_id is invalid")
        normalized = normalize_financial_metrics(raw)
        canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        scope = (normalized["provider_id"], normalized["account_alias"], normalized["environment"], request_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                """SELECT * FROM account_financial_metric_snapshots
                   WHERE provider_id=? AND account_alias=? AND environment=? AND request_id=?""",
                scope,
            ).fetchone()
            if existing:
                if existing["content_hash"] != digest:
                    raise FinancialMetricsError("Financial metrics request_id is already bound to a different payload")
                return self._result(existing)
            latest = db.execute(
                """SELECT fetched_at FROM account_financial_metric_snapshots
                   WHERE provider_id=? AND account_alias=? AND environment=?
                   ORDER BY fetched_at DESC,recorded_at DESC LIMIT 1""",
                scope[:3],
            ).fetchone()
            if latest and normalized["fetched_at"] < latest["fetched_at"]:
                raise FinancialMetricsError("Financial metrics snapshot is older than the latest stored observation")
            snapshot_id = str(uuid4())
            recorded_at = datetime.now(timezone.utc).isoformat()
            db.execute(
                """INSERT INTO account_financial_metric_snapshots(
                    snapshot_id,request_id,provider_id,account_alias,environment,account_id,
                    currency,account_status,fetched_at,recorded_at,coverage_status,schema_version,
                    metrics_json,flags_json,content_hash
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    snapshot_id, request_id, normalized["provider_id"], normalized["account_alias"],
                    normalized["environment"], normalized["account_id"], normalized["currency"],
                    normalized["account_status"], normalized["fetched_at"], recorded_at,
                    normalized["coverage_status"], normalized["schema_version"],
                    json.dumps(normalized["metrics"], sort_keys=True),
                    json.dumps(normalized["flags"], sort_keys=True), digest,
                ),
            )
            row = db.execute(
                "SELECT * FROM account_financial_metric_snapshots WHERE snapshot_id=?",
                (snapshot_id,),
            ).fetchone()
            return self._result(row)

    def get_by_request_id(self, request_id: str, *, provider_id: str, account_alias: str) -> dict | None:
        if not isinstance(request_id, str) or not _IDENTIFIER.fullmatch(request_id):
            raise FinancialMetricsError("Financial metrics request_id is invalid")
        with self.connect() as db:
            row = db.execute(
                """SELECT * FROM account_financial_metric_snapshots
                   WHERE provider_id=? AND account_alias=? AND environment='paper' AND request_id=?""",
                (provider_id, account_alias, request_id),
            ).fetchone()
        return self._result(row) if row else None

    def list_snapshots(
        self, *, provider_id: str | None = None, account_alias: str | None = None,
        environment: str | None = None, limit: int = 50,
    ) -> list[dict]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise FinancialMetricsError("Financial metrics limit must be between 1 and 200")
        clauses, params = [], []
        for field, value in (("provider_id", provider_id), ("account_alias", account_alias), ("environment", environment)):
            if value is not None:
                clauses.append(f"{field}=?")
                params.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM account_financial_metric_snapshots" + where +
                " ORDER BY fetched_at DESC,recorded_at DESC LIMIT ?", params,
            ).fetchall()
        return [self._result(row) for row in rows]


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
