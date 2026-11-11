from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from .contracts import AccountPosition, AccountProviderError


class PositionSnapshotError(RuntimeError):
    pass


class PositionSnapshotStore:
    """Append-only history of externally observed account positions.

    This ledger is deliberately separate from the paper portfolio. A missing
    position is interpreted as a disappearance only when the source explicitly
    marks the snapshot complete.
    """

    COVERAGE_STATES = {"complete", "partial"}

    def __init__(self, database_path: str):
        self.database_path = str(Path(database_path).expanduser().resolve())
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
                CREATE TABLE IF NOT EXISTS account_position_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    account_alias TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    market_type TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    coverage_status TEXT NOT NULL CHECK(coverage_status IN ('complete','partial')),
                    previous_snapshot_id TEXT,
                    reconciliation_status TEXT NOT NULL,
                    change_count INTEGER NOT NULL,
                    position_count INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    FOREIGN KEY(previous_snapshot_id)
                        REFERENCES account_position_snapshots(snapshot_id)
                );
                CREATE INDEX IF NOT EXISTS idx_position_snapshots_account
                    ON account_position_snapshots(
                        provider_id,account_alias,environment,market_type,
                        fetched_at DESC
                    );
                CREATE TABLE IF NOT EXISTS account_position_snapshot_items (
                    snapshot_id TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    market_type TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quote_currency TEXT NOT NULL,
                    average_entry_price TEXT,
                    mark_price TEXT,
                    market_value TEXT,
                    unrealized_pnl TEXT,
                    PRIMARY KEY(snapshot_id,instrument_id),
                    FOREIGN KEY(snapshot_id)
                        REFERENCES account_position_snapshots(snapshot_id)
                        ON DELETE RESTRICT
                );
                CREATE TRIGGER IF NOT EXISTS account_position_snapshots_no_update
                    BEFORE UPDATE ON account_position_snapshots
                    BEGIN SELECT RAISE(ABORT,'position snapshots are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS account_position_snapshots_no_delete
                    BEFORE DELETE ON account_position_snapshots
                    BEGIN SELECT RAISE(ABORT,'position snapshots are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS account_position_items_no_update
                    BEFORE UPDATE ON account_position_snapshot_items
                    BEGIN SELECT RAISE(ABORT,'position snapshot items are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS account_position_items_no_delete
                    BEFORE DELETE ON account_position_snapshot_items
                    BEGIN SELECT RAISE(ABORT,'position snapshot items are append-only'); END;
                """
            )

    @staticmethod
    def _parse_time(value: Any, field: str) -> datetime:
        if not isinstance(value, str):
            raise PositionSnapshotError(f"{field} timestamp is required")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise PositionSnapshotError(f"{field} timestamp is invalid") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise PositionSnapshotError(f"{field} timestamp must include a timezone")
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _normalize(cls, snapshot: dict) -> tuple[dict, list[dict]]:
        if not isinstance(snapshot, dict):
            raise PositionSnapshotError("Position snapshot must be an object")
        for field in ("provider_id", "account_alias", "environment", "market_type"):
            value = snapshot.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise PositionSnapshotError(f"Position snapshot {field} is invalid")
        if snapshot["market_type"].lower() not in {"spot", "cash", "margin", "future", "option", "perpetual"}:
            raise PositionSnapshotError("Position snapshot market_type is unsupported")
        if snapshot.get("read_only") is not True:
            raise PositionSnapshotError("Position snapshot must originate from a read-only connector")
        if snapshot.get("execution_enabled") is not False:
            raise PositionSnapshotError("Position snapshot cannot enable execution")
        if snapshot.get("withdrawals_enabled") is not False:
            raise PositionSnapshotError("Position snapshot cannot enable withdrawals")
        coverage = snapshot.get("coverage_status")
        if coverage not in cls.COVERAGE_STATES:
            raise PositionSnapshotError("coverage_status must be complete or partial")

        fetched_at = cls._parse_time(snapshot.get("fetched_at"), "fetched_at")
        age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
        if age < -60:
            raise PositionSnapshotError("Position snapshot timestamp is in the future")
        if age > 300:
            raise PositionSnapshotError("Position snapshot timestamp is stale")

        raw_positions = snapshot.get("positions")
        if not isinstance(raw_positions, list):
            raise PositionSnapshotError("Position snapshot positions must be a list")
        positions = []
        seen = set()
        for raw in raw_positions:
            try:
                position = AccountPosition.normalize(raw)
            except AccountProviderError as exc:
                raise PositionSnapshotError(str(exc)) from exc
            if position.market_type != snapshot["market_type"].lower():
                raise PositionSnapshotError("Position market_type does not match snapshot market_type")
            if position.instrument_id in seen:
                raise PositionSnapshotError(
                    f"Duplicate instrument position in snapshot: {position.instrument_id}"
                )
            seen.add(position.instrument_id)
            positions.append(position.__dict__)
        positions.sort(key=lambda item: item["instrument_id"])
        normalized = {
            "provider_id": snapshot["provider_id"].strip(),
            "account_alias": snapshot["account_alias"].strip(),
            "environment": snapshot["environment"].strip().lower(),
            "market_type": snapshot["market_type"].strip().lower(),
            "fetched_at": fetched_at.isoformat(),
            "coverage_status": coverage,
            "positions": positions,
        }
        return normalized, positions

    @staticmethod
    def _diff(previous: dict[str, dict], current: dict[str, dict], *, complete: bool) -> list[dict]:
        changes = []
        for instrument_id in sorted(set(previous) | set(current)):
            before, after = previous.get(instrument_id), current.get(instrument_id)
            if before is None:
                changes.append({
                    "instrument_id": instrument_id, "symbol": after["symbol"],
                    "state": "added", "previous_present": False, "current_present": True,
                    "previous_quantity": None, "current_quantity": after["quantity"],
                    "delta_quantity": None,
                })
                continue
            if after is None:
                changes.append({
                    "instrument_id": instrument_id, "symbol": before["symbol"],
                    "state": "absent" if complete else "not_observed",
                    "previous_present": True, "current_present": False,
                    "previous_quantity": before["quantity"], "current_quantity": None,
                    "delta_quantity": None,
                })
                continue
            changed = any(before.get(field) != after.get(field) for field in (
                "quantity", "side", "average_entry_price", "mark_price",
                "market_value", "unrealized_pnl", "quote_currency",
            ))
            delta = None
            if before["quantity"] is not None and after["quantity"] is not None:
                with localcontext() as context:
                    context.prec = 100
                    delta = format(
                        Decimal(after["quantity"]) - Decimal(before["quantity"]), "f"
                    )
                    if "." in delta:
                        delta = delta.rstrip("0").rstrip(".")
            changes.append({
                "instrument_id": instrument_id, "symbol": after["symbol"],
                "state": "changed" if changed else "unchanged",
                "previous_present": True, "current_present": True,
                "previous_quantity": before["quantity"],
                "current_quantity": after["quantity"], "delta_quantity": delta,
            })
        return changes

    def _record_snapshot_in_transaction(self, snapshot: dict, db) -> str:
        normalized, positions = self._normalize(snapshot)
        canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        snapshot_id = str(uuid4())
        recorded_at = datetime.now(timezone.utc).isoformat()
        account_key = (
            normalized["provider_id"], normalized["account_alias"],
            normalized["environment"], normalized["market_type"],
        )
        previous_row = db.execute(
            """
            SELECT snapshot_id,fetched_at,coverage_status
            FROM account_position_snapshots
            WHERE provider_id=? AND account_alias=? AND environment=? AND market_type=?
            ORDER BY fetched_at DESC,recorded_at DESC,snapshot_id DESC LIMIT 1
            """,
            account_key,
        ).fetchone()
        previous_id = previous_row["snapshot_id"] if previous_row else None
        if previous_row and normalized["fetched_at"] < previous_row["fetched_at"]:
            raise PositionSnapshotError("Position snapshot is older than the latest stored observation")
        previous = {}
        if previous_id:
            rows = db.execute(
                "SELECT * FROM account_position_snapshot_items WHERE snapshot_id=?",
                (previous_id,),
            ).fetchall()
            previous = {row["instrument_id"]: dict(row) for row in rows}
        current = {item["instrument_id"]: item for item in positions}
        # A partial observation may report changes to observed positions,
        # but cannot establish that omitted instruments have disappeared.
        changes = self._diff(
            previous, current,
            complete=normalized["coverage_status"] == "complete",
        ) if previous_id else []
        change_count = sum(item["state"] not in ("unchanged", "not_observed") for item in changes)
        status = "initial" if previous_id is None else (
            "partial" if normalized["coverage_status"] == "partial"
            else ("changed" if change_count else "unchanged")
        )
        db.execute(
            """
            INSERT INTO account_position_snapshots(
                snapshot_id,provider_id,account_alias,environment,market_type,
                fetched_at,recorded_at,coverage_status,previous_snapshot_id,
                reconciliation_status,change_count,position_count,content_hash
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                snapshot_id, *account_key, normalized["fetched_at"], recorded_at,
                normalized["coverage_status"], previous_id, status,
                change_count, len(positions), content_hash,
            ),
        )
        db.executemany(
            """
            INSERT INTO account_position_snapshot_items(
                snapshot_id,instrument_id,symbol,asset_class,market_type,quantity,
                side,quote_currency,average_entry_price,mark_price,market_value,unrealized_pnl
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    snapshot_id, item["instrument_id"], item["symbol"], item["asset_class"],
                    item["market_type"], item["quantity"], item["side"], item["quote_currency"],
                    item["average_entry_price"], item["mark_price"], item["market_value"],
                    item["unrealized_pnl"],
                )
                for item in positions
            ],
        )
        return snapshot_id

    def record_snapshot(self, snapshot: dict) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            snapshot_id = self._record_snapshot_in_transaction(snapshot, db)
        return self.get_snapshot(snapshot_id)

    def list_snapshots(
        self, *, provider_id: str | None = None, account_alias: str | None = None,
        environment: str | None = None, market_type: str | None = None, limit: int = 50,
    ) -> list[dict]:
        if not isinstance(limit, int) or not 1 <= limit <= 200:
            raise PositionSnapshotError("Position snapshot limit must be between 1 and 200")
        clauses, params = [], []
        for field, value in (
            ("provider_id", provider_id), ("account_alias", account_alias),
            ("environment", environment), ("market_type", market_type),
        ):
            if value:
                clauses.append(f"{field}=?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM account_position_snapshots {where} "
                "ORDER BY fetched_at DESC,recorded_at DESC,snapshot_id DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_snapshot(self, snapshot_id: str) -> dict | None:
        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            return None
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM account_position_snapshots WHERE snapshot_id=?",
                (snapshot_id.strip(),),
            ).fetchone()
            if row is None:
                return None
            current_rows = db.execute(
                "SELECT * FROM account_position_snapshot_items WHERE snapshot_id=? ORDER BY instrument_id",
                (snapshot_id.strip(),),
            ).fetchall()
            previous_rows = []
            if row["previous_snapshot_id"]:
                previous_rows = db.execute(
                    "SELECT * FROM account_position_snapshot_items WHERE snapshot_id=? ORDER BY instrument_id",
                    (row["previous_snapshot_id"],),
                ).fetchall()
        current = {item["instrument_id"]: dict(item) for item in current_rows}
        previous = {item["instrument_id"]: dict(item) for item in previous_rows}
        result = dict(row)
        result["positions"] = list(current.values())
        result["changes"] = self._diff(
            previous, current, complete=result["coverage_status"] == "complete",
        ) if result["previous_snapshot_id"] else []
        return result
