"""Provider-neutral, idempotent SQLite ledger for normalized order observations and fills."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any

from .contracts import AccountOrder, AccountOrderFill, AccountProviderError


class AccountOrderLedgerError(RuntimeError):
    pass


class AccountOrderLedgerConflict(AccountOrderLedgerError):
    pass


_TERMINAL = {"filled", "cancelled", "expired", "rejected"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class AccountOrderLedger:
    """Stores the latest order state plus append-only observation and fill history.

    This ledger records evidence only. It does not submit, cancel or retry orders.
    """

    def __init__(self, database_path: str):
        self.path = database_path
        self.lock = RLock()
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS account_orders (
                order_key TEXT PRIMARY KEY,
                provider_id TEXT NOT NULL,
                account_alias TEXT NOT NULL,
                environment TEXT NOT NULL,
                market_type TEXT NOT NULL,
                instrument_id TEXT NOT NULL,
                order_id TEXT,
                client_order_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_quantity TEXT NOT NULL,
                filled_quantity TEXT NOT NULL,
                remaining_quantity TEXT,
                average_fill_price TEXT,
                quote_currency TEXT NOT NULL,
                exchange_status TEXT,
                exchange_timestamp_ms INTEGER,
                observed_at TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                fill_history_complete INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_account_order_exchange_id
                ON account_orders(provider_id,account_alias,environment,market_type,instrument_id,order_id)
                WHERE order_id IS NOT NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_account_order_client_id
                ON account_orders(provider_id,account_alias,environment,market_type,instrument_id,client_order_id)
                WHERE client_order_id IS NOT NULL;
            CREATE TABLE IF NOT EXISTS account_order_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_key TEXT NOT NULL REFERENCES account_orders(order_key),
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_account_order_events_order
                ON account_order_events(order_key,event_id);
            CREATE TABLE IF NOT EXISTS account_order_fills (
                fill_key TEXT PRIMARY KEY,
                order_key TEXT NOT NULL REFERENCES account_orders(order_key),
                provider_id TEXT NOT NULL,
                account_alias TEXT NOT NULL,
                environment TEXT NOT NULL,
                market_type TEXT NOT NULL,
                instrument_id TEXT NOT NULL,
                trade_id TEXT NOT NULL,
                amount TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                UNIQUE(provider_id,account_alias,environment,market_type,instrument_id,trade_id)
            );
            CREATE INDEX IF NOT EXISTS idx_account_order_fills_order
                ON account_order_fills(order_key,timestamp_ms,trade_id);
            CREATE TRIGGER IF NOT EXISTS account_order_events_no_update
                BEFORE UPDATE ON account_order_events BEGIN
                    SELECT RAISE(ABORT,'account order events are append-only');
                END;
            CREATE TRIGGER IF NOT EXISTS account_order_events_no_delete
                BEFORE DELETE ON account_order_events BEGIN
                    SELECT RAISE(ABORT,'account order events are append-only');
                END;
            CREATE TRIGGER IF NOT EXISTS account_order_fills_no_update
                BEFORE UPDATE ON account_order_fills BEGIN
                    SELECT RAISE(ABORT,'account order fills are append-only');
                END;
            CREATE TRIGGER IF NOT EXISTS account_order_fills_no_delete
                BEFORE DELETE ON account_order_fills BEGIN
                    SELECT RAISE(ABORT,'account order fills are append-only');
                END;
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    @staticmethod
    def _scope(order: AccountOrder | AccountOrderFill) -> tuple[str, ...]:
        return (
            order.provider_id, order.account_alias, order.environment,
            order.market_type, order.instrument_id,
        )

    @staticmethod
    def _identity_key(order: AccountOrder) -> str:
        values = (*AccountOrderLedger._scope(order),
                  "client" if order.client_order_id is not None else "exchange",
                  order.client_order_id if order.client_order_id is not None else order.order_id)
        return hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()

    @staticmethod
    def _fill_key(fill: AccountOrderFill) -> str:
        values = (*AccountOrderLedger._scope(fill), fill.trade_id)
        return hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()

    @staticmethod
    def _identity_payload(order: AccountOrder) -> dict[str, Any]:
        return order.to_dict()

    def record_order(self, raw_order: AccountOrder | dict) -> dict:
        try:
            order = AccountOrder.normalize(raw_order)
        except AccountProviderError as exc:
            raise AccountOrderLedgerError(str(exc)) from None
        payload = self._identity_payload(order)
        payload_json = _canonical_json(payload)
        content_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        scope = self._scope(order)
        now = _utcnow()

        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            matches = {}
            for column, value in (("order_id", order.order_id), ("client_order_id", order.client_order_id)):
                if value is None:
                    continue
                row = db.execute(
                    f"SELECT * FROM account_orders WHERE provider_id=? AND account_alias=? AND environment=? "
                    f"AND market_type=? AND instrument_id=? AND {column}=?",
                    (*scope, value),
                ).fetchone()
                if row:
                    matches[row["order_key"]] = row
            if len(matches) > 1:
                raise AccountOrderLedgerConflict("Order ID and client order ID resolve to different ledger records")
            existing = next(iter(matches.values()), None)
            if existing is None:
                order_key = self._identity_key(order)
                db.execute(
                    "INSERT INTO account_orders(order_key,provider_id,account_alias,environment,market_type,instrument_id,"
                    "order_id,client_order_id,symbol,side,order_type,status,requested_quantity,filled_quantity,"
                    "remaining_quantity,average_fill_price,quote_currency,exchange_status,exchange_timestamp_ms,"
                    "observed_at,content_hash,fill_history_complete,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
                    (order_key, *scope, order.order_id, order.client_order_id, order.symbol,
                     order.side, order.order_type, order.status, order.requested_quantity,
                     order.filled_quantity, order.remaining_quantity, order.average_fill_price,
                     order.quote_currency, order.exchange_status, order.exchange_timestamp_ms,
                     order.observed_at, content_hash, now, now),
                )
                db.execute(
                    "INSERT INTO account_order_events(order_key,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                    (order_key, "order_observed", payload_json, now),
                )
                return {"order_key": order_key, "order": payload, "idempotent_replay": False}

            order_key = existing["order_key"]
            for column, observed in (("order_id", order.order_id), ("client_order_id", order.client_order_id)):
                if existing[column] is not None and observed is not None and existing[column] != observed:
                    raise AccountOrderLedgerConflict(f"Order {column} changed for an existing ledger identity")
            immutable = ("provider_id", "account_alias", "environment", "market_type", "instrument_id",
                         "symbol", "side", "order_type", "requested_quantity", "quote_currency")
            if any(existing[field] != getattr(order, field) for field in immutable):
                raise AccountOrderLedgerConflict("Immutable order identity or terms changed")
            if existing["content_hash"] == content_hash:
                return {"order_key": order_key, "order": payload, "idempotent_replay": True}
            if order.observed_at < existing["observed_at"]:
                raise AccountOrderLedgerConflict("Stale order observation cannot replace newer ledger state")
            if order.observed_at == existing["observed_at"]:
                raise AccountOrderLedgerConflict("Conflicting order evidence shares the same observation timestamp")
            if Decimal(order.filled_quantity) < Decimal(existing["filled_quantity"]):
                raise AccountOrderLedgerConflict("Cumulative filled quantity cannot regress")
            old_status, new_status = existing["status"], order.status
            if old_status == "reconciliation_required" and new_status != old_status:
                raise AccountOrderLedgerConflict("Quarantined order requires explicit operator resolution")
            if old_status in _TERMINAL and new_status not in {old_status, "reconciliation_required"}:
                raise AccountOrderLedgerConflict("Terminal order status cannot regress")
            if old_status == "partially_filled" and new_status not in {
                "partially_filled", "filled", "cancelled", "expired", "reconciliation_required",
            }:
                raise AccountOrderLedgerConflict("Partially filled order status cannot regress")
            if order.order_id is not None and existing["order_id"] is None:
                db.execute("UPDATE account_orders SET order_id=? WHERE order_key=?", (order.order_id, order_key))
            if order.client_order_id is not None and existing["client_order_id"] is None:
                db.execute("UPDATE account_orders SET client_order_id=? WHERE order_key=?", (order.client_order_id, order_key))
            # New cumulative fill evidence invalidates any prior claim that the
            # individual fill history covered the full order quantity.
            fill_complete = bool(existing["fill_history_complete"])
            if order.filled_quantity != existing["filled_quantity"]:
                fill_complete = False
            db.execute(
                "UPDATE account_orders SET status=?,filled_quantity=?,remaining_quantity=?,average_fill_price=?,"
                "exchange_status=?,exchange_timestamp_ms=?,observed_at=?,content_hash=?,fill_history_complete=?,updated_at=? "
                "WHERE order_key=?",
                (order.status, order.filled_quantity, order.remaining_quantity, order.average_fill_price,
                 order.exchange_status, order.exchange_timestamp_ms, order.observed_at, content_hash,
                 int(fill_complete), now, order_key),
            )
            db.execute(
                "INSERT INTO account_order_events(order_key,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (order_key, "order_observed", payload_json, now),
            )
            return {"order_key": order_key, "order": payload, "idempotent_replay": False}

    def record_fills(
        self,
        order_key: str,
        raw_fills: list[AccountOrderFill | dict],
        *,
        history_complete: bool,
    ) -> dict:
        if not isinstance(raw_fills, list) or not isinstance(history_complete, bool):
            raise AccountOrderLedgerError("Fill history and completeness flag are required")
        try:
            fills = [AccountOrderFill.normalize(item) for item in raw_fills]
        except AccountProviderError as exc:
            raise AccountOrderLedgerError(str(exc)) from None
        inserted = 0
        now = _utcnow()
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            order = db.execute("SELECT * FROM account_orders WHERE order_key=?", (order_key,)).fetchone()
            if order is None:
                raise AccountOrderLedgerError("Order not found in account ledger")
            for fill in fills:
                if self._scope(fill) != tuple(order[field] for field in (
                    "provider_id", "account_alias", "environment", "market_type", "instrument_id",
                )):
                    raise AccountOrderLedgerConflict("Fill provider/account/market scope does not match its order")
                if fill.symbol != order["symbol"] or fill.side != order["side"]:
                    raise AccountOrderLedgerConflict("Fill symbol or side does not match its order")
                if fill.order_id is not None and order["order_id"] is not None and fill.order_id != order["order_id"]:
                    raise AccountOrderLedgerConflict("Fill exchange order ID does not match its order")
                if fill.client_order_id is not None and order["client_order_id"] is not None and fill.client_order_id != order["client_order_id"]:
                    raise AccountOrderLedgerConflict("Fill client order ID does not match its order")
                if fill.order_id is None and fill.client_order_id != order["client_order_id"]:
                    raise AccountOrderLedgerConflict("Fill is not attributable to this order")
                if fill.client_order_id is None and fill.order_id != order["order_id"]:
                    raise AccountOrderLedgerConflict("Fill is not attributable to this order")
                fill_payload = fill.to_dict()
                fill_json = _canonical_json(fill_payload)
                fill_key = self._fill_key(fill)
                prior = db.execute(
                    "SELECT order_key,payload_json FROM account_order_fills WHERE fill_key=?",
                    (fill_key,),
                ).fetchone()
                if prior:
                    if prior["order_key"] != order_key or prior["payload_json"] != fill_json:
                        raise AccountOrderLedgerConflict("Trade ID was reused with different fill evidence")
                    continue
                db.execute(
                    "INSERT INTO account_order_fills(fill_key,order_key,provider_id,account_alias,environment,"
                    "market_type,instrument_id,trade_id,amount,timestamp_ms,payload_json,recorded_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (fill_key, order_key, *self._scope(fill), fill.trade_id, fill.amount,
                     fill.timestamp_ms, fill_json, now),
                )
                inserted += 1

            rows = db.execute(
                "SELECT amount FROM account_order_fills WHERE order_key=?", (order_key,),
            ).fetchall()
            total_amount = sum((Decimal(row["amount"]) for row in rows), Decimal("0"))
            order_filled = Decimal(order["filled_quantity"])
            if total_amount > order_filled:
                raise AccountOrderLedgerConflict("Individual fills exceed cumulative order filled quantity")
            coverage_complete = history_complete and total_amount == order_filled
            if history_complete and not coverage_complete:
                raise AccountOrderLedgerConflict("Complete fill history does not equal cumulative order quantity")
            changed = inserted > 0 or bool(order["fill_history_complete"]) != coverage_complete
            if changed:
                db.execute(
                    "UPDATE account_orders SET fill_history_complete=?,updated_at=? WHERE order_key=?",
                    (int(coverage_complete), now, order_key),
                )
                event = {
                    "inserted_fills": inserted,
                    "fill_count": len(rows),
                    "individual_filled_quantity": format(total_amount, "f"),
                    "exchange_cumulative_filled": format(order_filled, "f"),
                    "history_complete": history_complete,
                    "fill_coverage_complete": coverage_complete,
                }
                db.execute(
                    "INSERT INTO account_order_events(order_key,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                    (order_key, "fills_observed", _canonical_json(event), now),
                )
        return {
            "order_key": order_key,
            "inserted_fills": inserted,
            "fill_count": len(rows),
            "individual_filled_quantity": format(total_amount, "f"),
            "exchange_cumulative_filled": format(order_filled, "f"),
            "history_complete": history_complete,
            "fill_coverage_complete": coverage_complete,
        }

    def get_order(self, order_key: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM account_orders WHERE order_key=?", (order_key,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["fill_history_complete"] = bool(result["fill_history_complete"])
        result.pop("content_hash", None)
        return result

    def list_orders(self, *, limit: int = 100) -> list[dict]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise AccountOrderLedgerError("Order ledger limit must be between 1 and 1000")
        with self.connect() as db:
            keys = [row[0] for row in db.execute(
                "SELECT order_key FROM account_orders ORDER BY updated_at DESC,order_key LIMIT ?", (limit,),
            )]
        return [self.get_order(key) for key in keys]

    def list_fills(self, order_key: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload_json FROM account_order_fills WHERE order_key=? ORDER BY timestamp_ms,trade_id",
                (order_key,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def list_events(self, order_key: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT event_type,payload_json,created_at FROM account_order_events WHERE order_key=? ORDER BY event_id",
                (order_key,),
            ).fetchall()
        return [{"event_type": row[0], "payload": json.loads(row[1]), "created_at": row[2]} for row in rows]

