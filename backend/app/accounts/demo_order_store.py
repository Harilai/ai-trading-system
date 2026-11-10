from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
from uuid import uuid4


class DemoOrderStoreError(RuntimeError):
    pass


class DemoOrderConflict(DemoOrderStoreError):
    pass


_ALLOWED_TRANSITIONS = {
    "intent_created": {"submission_unknown", "rejected"},
    "submission_unknown": {
        "submission_unknown", "open", "partially_filled", "filled", "cancelled", "expired",
        "rejected", "reconciliation_required",
    },
    "open": {"open", "partially_filled", "filled", "cancelled", "expired", "reconciliation_required"},
    "partially_filled": {
        "partially_filled", "filled", "cancelled", "expired", "reconciliation_required",
    },
    "filled": set(),
    "cancelled": set(),
    "expired": set(),
    "rejected": set(),
    "reconciliation_required": set(),
}


class BinanceDemoOrderStore:
    """Durable idempotency and append-only audit for virtual Demo Trading orders."""

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
                CREATE TABLE IF NOT EXISTS binance_demo_orders (
                    client_order_id TEXT PRIMARY KEY,
                    request_json TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    price TEXT NOT NULL,
                    quote_asset TEXT NOT NULL,
                    status TEXT NOT NULL,
                    exchange_order_id TEXT,
                    exchange_status TEXT,
                    filled TEXT,
                    remaining TEXT,
                    average TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_binance_demo_orders_status
                    ON binance_demo_orders(status,updated_at DESC);
                CREATE TABLE IF NOT EXISTS binance_demo_order_events (
                    event_id TEXT PRIMARY KEY,
                    client_order_id TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(client_order_id) REFERENCES binance_demo_orders(client_order_id)
                );
                CREATE INDEX IF NOT EXISTS idx_binance_demo_order_events_order
                    ON binance_demo_order_events(client_order_id,created_at,event_id);
                CREATE TRIGGER IF NOT EXISTS binance_demo_order_intent_immutable
                    BEFORE UPDATE OF client_order_id,request_json,symbol,side,amount,price,quote_asset
                    ON binance_demo_orders
                    BEGIN SELECT RAISE(ABORT,'demo order intent is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS binance_demo_orders_no_delete
                    BEFORE DELETE ON binance_demo_orders
                    BEGIN SELECT RAISE(ABORT,'demo orders are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS binance_demo_order_events_no_update
                    BEFORE UPDATE ON binance_demo_order_events
                    BEGIN SELECT RAISE(ABORT,'demo order events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS binance_demo_order_events_no_delete
                    BEFORE DELETE ON binance_demo_order_events
                    BEGIN SELECT RAISE(ABORT,'demo order events are append-only'); END;
                """
            )

    @staticmethod
    def _canonical_request(request: dict) -> dict:
        if not isinstance(request, dict):
            raise DemoOrderStoreError("Demo order request must be an object")
        client_order_id = request.get("client_order_id")
        symbol = request.get("symbol")
        side = request.get("side")
        amount = request.get("amount")
        price = request.get("price")
        quote_asset = request.get("quote_asset")
        if not isinstance(client_order_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,36}", client_order_id):
            raise DemoOrderStoreError("client_order_id must contain 1-36 safe characters")
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9._-]{2,32}/[A-Z0-9._-]{2,24}", symbol):
            raise DemoOrderStoreError("Demo order symbol is invalid")
        if side not in ("buy", "sell"):
            raise DemoOrderStoreError("Demo order side must be buy or sell")
        for field, value in (("amount", amount), ("price", price)):
            if not isinstance(value, str) or not value or len(value) > 80:
                raise DemoOrderStoreError(f"Demo order {field} must be a decimal string")
        if not isinstance(quote_asset, str) or not re.fullmatch(r"[A-Z0-9._-]{1,24}", quote_asset):
            raise DemoOrderStoreError("Demo order quote asset is invalid")
        return {
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "price": price,
            "quote_asset": quote_asset,
        }

    @staticmethod
    def _row_dict(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        return result

    def prepare_intent(self, request: dict) -> tuple[dict, bool]:
        normalized = self._canonical_request(request)
        canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM binance_demo_orders WHERE client_order_id=?",
                (normalized["client_order_id"],),
            ).fetchone()
            if existing:
                if existing["request_json"] != canonical:
                    raise DemoOrderConflict("client_order_id is already bound to a different immutable intent")
                return self._row_dict(existing), False

            db.execute(
                """
                INSERT INTO binance_demo_orders(
                    client_order_id,request_json,symbol,side,amount,price,quote_asset,status,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,'intent_created',?,?)
                """,
                (
                    normalized["client_order_id"], canonical, normalized["symbol"],
                    normalized["side"], normalized["amount"], normalized["price"],
                    normalized["quote_asset"], now, now,
                ),
            )
            self._insert_event(
                db, normalized["client_order_id"], None, "intent_created",
                {"request": normalized}, now,
            )
            row = db.execute(
                "SELECT * FROM binance_demo_orders WHERE client_order_id=?",
                (normalized["client_order_id"],),
            ).fetchone()
        return self._row_dict(row), True

    @staticmethod
    def _insert_event(
        db: sqlite3.Connection,
        client_order_id: str,
        from_status: str | None,
        to_status: str,
        payload: dict,
        stamp: str,
    ) -> None:
        db.execute(
            """
            INSERT INTO binance_demo_order_events(
                event_id,client_order_id,from_status,to_status,payload_json,created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                str(uuid4()), client_order_id, from_status, to_status,
                json.dumps(payload, sort_keys=True, separators=(",", ":")), stamp,
            ),
        )

    def transition(
        self,
        client_order_id: str,
        to_status: str,
        *,
        exchange_order_id: str | None = None,
        exchange_status: str | None = None,
        filled: str | None = None,
        remaining: str | None = None,
        average: str | None = None,
        error: str | None = None,
        payload: dict | None = None,
    ) -> dict:
        if to_status not in _ALLOWED_TRANSITIONS:
            raise DemoOrderStoreError("Unknown Demo order status")
        stamp = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM binance_demo_orders WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
            if row is None:
                raise DemoOrderStoreError("Demo order intent not found")
            current = row["status"]
            if to_status not in _ALLOWED_TRANSITIONS[current]:
                raise DemoOrderStoreError(f"Illegal Demo order transition: {current} -> {to_status}")

            db.execute(
                """
                UPDATE binance_demo_orders SET
                    status=?,
                    exchange_order_id=COALESCE(?,exchange_order_id),
                    exchange_status=COALESCE(?,exchange_status),
                    filled=COALESCE(?,filled),
                    remaining=COALESCE(?,remaining),
                    average=COALESCE(?,average),
                    error=?,
                    updated_at=?
                WHERE client_order_id=?
                """,
                (
                    to_status, exchange_order_id, exchange_status, filled,
                    remaining, average, error, stamp, client_order_id,
                ),
            )
            event_payload = dict(payload or {})
            if error:
                event_payload["error"] = error
            self._insert_event(db, client_order_id, current, to_status, event_payload, stamp)
            updated = db.execute(
                "SELECT * FROM binance_demo_orders WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
        return self._row_dict(updated)

    def get_order(self, client_order_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM binance_demo_orders WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
        return self._row_dict(row)

    def list_orders(self, *, limit: int = 50) -> list[dict]:
        if not isinstance(limit, int) or not 1 <= limit <= 200:
            raise DemoOrderStoreError("Demo order limit must be between 1 and 200")
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM binance_demo_orders ORDER BY created_at DESC,client_order_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_dict(row) for row in rows]

    def list_events(self, client_order_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT from_status,to_status,payload_json,created_at
                FROM binance_demo_order_events
                WHERE client_order_id=? ORDER BY created_at,event_id
                """,
                (client_order_id,),
            ).fetchall()
        return [
            {
                "from_status": row["from_status"],
                "to_status": row["to_status"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def count_unresolved(self) -> int:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT COUNT(*) AS count FROM binance_demo_orders
                WHERE status IN (
                    'intent_created','submission_unknown','open','partially_filled',
                    'reconciliation_required'
                )
                """
            ).fetchone()
        return int(row["count"])
