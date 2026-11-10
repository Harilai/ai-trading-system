from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
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
                CREATE TABLE IF NOT EXISTS binance_demo_order_fills (
                    exchange_trade_id TEXT NOT NULL,
                    client_order_id TEXT NOT NULL,
                    exchange_order_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    price TEXT NOT NULL,
                    cost TEXT NOT NULL,
                    fee_cost TEXT,
                    fee_currency TEXT,
                    timestamp_ms INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY(symbol,exchange_trade_id),
                    FOREIGN KEY(client_order_id) REFERENCES binance_demo_orders(client_order_id)
                );
                CREATE INDEX IF NOT EXISTS idx_binance_demo_order_fills_order
                    ON binance_demo_order_fills(client_order_id,timestamp_ms,exchange_trade_id);
                CREATE TRIGGER IF NOT EXISTS binance_demo_order_fills_no_update
                    BEFORE UPDATE ON binance_demo_order_fills
                    BEGIN SELECT RAISE(ABORT,'demo order fills are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS binance_demo_order_fills_no_delete
                    BEFORE DELETE ON binance_demo_order_fills
                    BEGIN SELECT RAISE(ABORT,'demo order fills are append-only'); END;
                CREATE TABLE IF NOT EXISTS binance_demo_reconciliation_control (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    next_due_at TEXT NOT NULL,
                    lease_until TEXT,
                    lease_token TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS binance_demo_reconciliation_events (
                    event_id TEXT PRIMARY KEY,
                    lease_token TEXT,
                    action TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS binance_demo_reconciliation_events_no_update
                    BEFORE UPDATE ON binance_demo_reconciliation_events
                    BEGIN SELECT RAISE(ABORT,'demo reconciliation events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS binance_demo_reconciliation_events_no_delete
                    BEFORE DELETE ON binance_demo_reconciliation_events
                    BEGIN SELECT RAISE(ABORT,'demo reconciliation events are append-only'); END;
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
        max_order_quote = request.get("max_order_quote", "25")
        if not isinstance(client_order_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,36}", client_order_id):
            raise DemoOrderStoreError("client_order_id must contain 1-36 safe characters")
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9._-]{2,32}/[A-Z0-9._-]{2,24}", symbol):
            raise DemoOrderStoreError("Demo order symbol is invalid")
        if side not in ("buy", "sell"):
            raise DemoOrderStoreError("Demo order side must be buy or sell")
        normalized_amounts = {}
        for field, value in (("amount", amount), ("price", price)):
            if (
                not isinstance(value, str)
                or not re.fullmatch(r"\d+(?:\.\d+)?", value)
                or len(value) > 80
            ):
                raise DemoOrderStoreError(f"Demo order {field} must be a plain decimal string")
            try:
                parsed = Decimal(value)
            except (InvalidOperation, ValueError):
                raise DemoOrderStoreError(f"Demo order {field} is malformed") from None
            if not parsed.is_finite() or parsed <= 0:
                raise DemoOrderStoreError(f"Demo order {field} must be finite and positive")
            if len(parsed.as_tuple().digits) > 50 or abs(parsed.as_tuple().exponent) > 50:
                raise DemoOrderStoreError(f"Demo order {field} precision is unsupported")
            text = format(parsed, "f")
            if "." in text:
                text = text.rstrip("0").rstrip(".")
            normalized_amounts[field] = text
        if not isinstance(quote_asset, str) or not re.fullmatch(r"[A-Z0-9._-]{1,24}", quote_asset):
            raise DemoOrderStoreError("Demo order quote asset is invalid")
        if (
            not isinstance(max_order_quote, str)
            or not re.fullmatch(r"\d+(?:\.\d+)?", max_order_quote)
        ):
            raise DemoOrderStoreError("Demo order quote cap must be a plain decimal string")
        try:
            quote_cap = Decimal(max_order_quote)
        except (InvalidOperation, ValueError):
            raise DemoOrderStoreError("Demo order quote cap is malformed") from None
        if not quote_cap.is_finite() or quote_cap <= 0:
            raise DemoOrderStoreError("Demo order quote cap must be finite and positive")
        if len(quote_cap.as_tuple().digits) > 50 or abs(quote_cap.as_tuple().exponent) > 50:
            raise DemoOrderStoreError("Demo order quote cap precision is unsupported")
        quote_cap_text = format(quote_cap, "f")
        if "." in quote_cap_text:
            quote_cap_text = quote_cap_text.rstrip("0").rstrip(".")
        return {
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": side,
            "amount": normalized_amounts["amount"],
            "price": normalized_amounts["price"],
            "quote_asset": quote_asset,
            "max_order_quote": quote_cap_text,
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

            unresolved = db.execute(
                """
                SELECT COUNT(*) AS count FROM binance_demo_orders
                WHERE status IN (
                    'intent_created','submission_unknown','open','partially_filled',
                    'reconciliation_required'
                )
                """
            ).fetchone()["count"]
            if int(unresolved) > 0:
                raise DemoOrderConflict(
                    "A previous Demo order is still open or requires reconciliation"
                )

            completed_orders = db.execute(
                """
                SELECT client_order_id,filled FROM binance_demo_orders
                WHERE status IN ('filled','cancelled','expired') AND filled IS NOT NULL
                """
            ).fetchall()
            for completed_order in completed_orders:
                with localcontext() as context:
                    context.prec = 120
                    expected_filled = Decimal(completed_order["filled"] or "0")
                    if expected_filled <= 0:
                        continue
                    fill_rows = db.execute(
                        "SELECT amount FROM binance_demo_order_fills WHERE client_order_id=?",
                        (completed_order["client_order_id"],),
                    ).fetchall()
                    recorded_filled = sum((Decimal(item["amount"]) for item in fill_rows), Decimal("0"))
                if recorded_filled != expected_filled:
                    raise DemoOrderConflict(
                        "A previous Demo order has incomplete individual fill/fee evidence"
                    )

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
        lease_token: str | None = None,
    ) -> dict:
        if to_status not in _ALLOWED_TRANSITIONS:
            raise DemoOrderStoreError("Unknown Demo order status")
        stamp = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._assert_reconciliation_lease(db, lease_token)
            row = db.execute(
                "SELECT * FROM binance_demo_orders WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
            if row is None:
                raise DemoOrderStoreError("Demo order intent not found")
            current = row["status"]
            if to_status not in _ALLOWED_TRANSITIONS[current]:
                raise DemoOrderStoreError(f"Illegal Demo order transition: {current} -> {to_status}")

            # Treat exchange snapshots as monotonic evidence. A lower cumulative
            # fill, increased remaining quantity, changed exchange ID or impossible
            # quantity sum is not a harmless stale update: quarantine the order.
            next_filled = filled if filled is not None else row["filled"]
            next_remaining = remaining if remaining is not None else row["remaining"]
            next_average = average if average is not None else row["average"]
            inconsistent = False
            reason = None
            if exchange_order_id and row["exchange_order_id"] and exchange_order_id != row["exchange_order_id"]:
                inconsistent = True
                reason = "Exchange order ID changed for an immutable client order ID"
            try:
                amount_value = Decimal(row["amount"])
                filled_value = Decimal(next_filled or "0")
                remaining_value = Decimal(next_remaining) if next_remaining is not None else None
                old_filled_value = Decimal(row["filled"] or "0")
                old_remaining_value = Decimal(row["remaining"]) if row["remaining"] is not None else None
                if not filled_value.is_finite() or filled_value < old_filled_value:
                    inconsistent = True
                    reason = reason or "Cumulative filled quantity regressed or became invalid"
                if remaining_value is not None and (
                    not remaining_value.is_finite()
                    or remaining_value < 0
                    or (old_remaining_value is not None and remaining_value > old_remaining_value)
                ):
                    inconsistent = True
                    reason = reason or "Remaining quantity increased or became invalid"
                if remaining_value is not None and filled_value + remaining_value != amount_value:
                    inconsistent = True
                    reason = reason or "Filled plus remaining quantity differs from immutable order amount"
                if to_status == "filled" and (
                    filled_value != amount_value or remaining_value != 0
                ):
                    inconsistent = True
                    reason = reason or "Filled terminal state does not account for the full order amount"
                if to_status == "partially_filled" and (
                    filled_value <= 0 or remaining_value is None or remaining_value <= 0
                ):
                    inconsistent = True
                    reason = reason or "Partial-fill state lacks consistent positive filled and remaining quantities"
                if to_status == "open" and filled_value > 0:
                    inconsistent = True
                    reason = reason or "Open state cannot discard an observed partial fill"
                if to_status == "rejected" and filled_value > 0:
                    inconsistent = True
                    reason = reason or "Rejected order unexpectedly reports a fill"
                if next_average is not None:
                    average_value = Decimal(next_average)
                    if not average_value.is_finite() or average_value < 0:
                        inconsistent = True
                        reason = reason or "Average fill price is invalid"
            except (InvalidOperation, ValueError, TypeError):
                inconsistent = True
                reason = reason or "Exchange order quantities are malformed"

            if inconsistent and to_status != "reconciliation_required":
                to_status = "reconciliation_required"
                error = reason or "Exchange order snapshot is inconsistent"

            if to_status not in _ALLOWED_TRANSITIONS[current]:
                raise DemoOrderStoreError(f"Illegal Demo order transition: {current} -> {to_status}")

            quarantine = to_status == "reconciliation_required"
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
                    to_status,
                    None if quarantine and row["exchange_order_id"] else exchange_order_id,
                    exchange_status,
                    None if quarantine else filled,
                    None if quarantine else remaining,
                    None if quarantine else average,
                    error, stamp, client_order_id,
                ),
            )
            event_payload = dict(payload or {})
            if filled is not None:
                event_payload["observed_filled"] = filled
            if remaining is not None:
                event_payload["observed_remaining"] = remaining
            if average is not None:
                event_payload["observed_average"] = average
            if exchange_order_id is not None:
                event_payload["observed_exchange_order_id"] = exchange_order_id
            if error:
                event_payload["error"] = error
            self._insert_event(db, client_order_id, current, to_status, event_payload, stamp)
            updated = db.execute(
                "SELECT * FROM binance_demo_orders WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
        return self._row_dict(updated)



    @staticmethod
    def _assert_reconciliation_lease(db: sqlite3.Connection, lease_token: str | None) -> None:
        if lease_token is None:
            return
        now = datetime.now(timezone.utc).isoformat()
        row = db.execute(
            "SELECT lease_token,lease_until FROM binance_demo_reconciliation_control WHERE id=1"
        ).fetchone()
        if (
            row is None
            or row["lease_token"] != lease_token
            or not row["lease_until"]
            or row["lease_until"] < now
        ):
            raise DemoOrderConflict("Demo reconciliation lease was lost; stale worker write rejected")

    def claim_reconciliation_lease(
        self,
        *,
        interval_seconds: int = 30,
        lease_seconds: int = 30,
    ) -> dict | None:
        if not 15 <= interval_seconds <= 3600 or not 5 <= lease_seconds <= 300:
            raise DemoOrderStoreError("Demo reconciliation interval or lease duration is out of range")
        now = datetime.now(timezone.utc)
        now_text = now.isoformat()
        token = str(uuid4())
        lease_until = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """
                INSERT OR IGNORE INTO binance_demo_reconciliation_control(
                    id,next_due_at,lease_until,lease_token,attempts,updated_at
                ) VALUES(1,?,NULL,NULL,0,?)
                """,
                (now_text, now_text),
            )
            row = db.execute(
                "SELECT * FROM binance_demo_reconciliation_control WHERE id=1"
            ).fetchone()
            if row["next_due_at"] > now_text or (
                row["lease_until"] is not None and row["lease_until"] > now_text
            ):
                return None
            attempts = int(row["attempts"]) + 1
            db.execute(
                """
                UPDATE binance_demo_reconciliation_control
                SET lease_until=?,lease_token=?,attempts=?,updated_at=? WHERE id=1
                """,
                (lease_until, token, attempts, now_text),
            )
            self._insert_reconciliation_event(
                db, token, "lease_claimed",
                {"attempt": attempts, "lease_until": lease_until},
                now_text,
            )
        return {"lease_token": token, "attempt": attempts, "lease_until": lease_until}

    @staticmethod
    def _insert_reconciliation_event(
        db: sqlite3.Connection,
        lease_token: str | None,
        action: str,
        details: dict,
        stamp: str,
    ) -> None:
        db.execute(
            """
            INSERT INTO binance_demo_reconciliation_events(
                event_id,lease_token,action,details_json,created_at
            ) VALUES(?,?,?,?,?)
            """,
            (
                str(uuid4()), lease_token, action,
                json.dumps(details, sort_keys=True, separators=(",", ":")), stamp,
            ),
        )

    def complete_reconciliation_lease(
        self,
        lease_token: str,
        *,
        success: bool,
        interval_seconds: int = 30,
        retry_base_seconds: int = 30,
    ) -> bool:
        if not lease_token or not 15 <= interval_seconds <= 3600 or retry_base_seconds < 1:
            return False
        now = datetime.now(timezone.utc)
        now_text = now.isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT attempts,lease_until FROM binance_demo_reconciliation_control "
                "WHERE id=1 AND lease_token=?",
                (lease_token,),
            ).fetchone()
            if row is None or not row["lease_until"] or row["lease_until"] < now_text:
                return False
            if success:
                delay = interval_seconds
                attempts = 0
                action = "cycle_completed"
            else:
                attempts = int(row["attempts"])
                delay = min(900, retry_base_seconds * (2 ** min(5, max(0, attempts - 1))))
                action = "cycle_failed"
            next_due = (now + timedelta(seconds=delay)).isoformat()
            changed = db.execute(
                """
                UPDATE binance_demo_reconciliation_control
                SET next_due_at=?,lease_until=NULL,lease_token=NULL,attempts=?,updated_at=?
                WHERE id=1 AND lease_token=? AND lease_until>=?
                """,
                (next_due, attempts, now_text, lease_token, now_text),
            ).rowcount
            if changed != 1:
                return False
            self._insert_reconciliation_event(
                db, lease_token, action,
                {"success": success, "retry_delay_seconds": delay, "attempt": int(row["attempts"])},
                now_text,
            )
        return True

    def reconciliation_events(self, *, limit: int = 50) -> list[dict]:
        if not isinstance(limit, int) or not 1 <= limit <= 200:
            raise DemoOrderStoreError("Demo reconciliation event limit must be between 1 and 200")
        with self.connect() as db:
            rows = db.execute(
                "SELECT lease_token,action,details_json,created_at "
                "FROM binance_demo_reconciliation_events ORDER BY created_at DESC,event_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "lease_token": row["lease_token"],
                "action": row["action"],
                "details": json.loads(row["details_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def list_reconciliation_candidates(self, *, limit: int = 20) -> list[dict]:
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise DemoOrderStoreError("Demo reconciliation candidate limit must be between 1 and 100")
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT * FROM binance_demo_orders
                WHERE status IN (
                    'intent_created','submission_unknown','open','partially_filled',
                    'filled','cancelled','expired'
                )
                ORDER BY updated_at,client_order_id LIMIT 1000
                """
            ).fetchall()
            candidates = []
            for row in rows:
                expected = Decimal(row["filled"] or "0")
                if row["status"] in ("filled", "cancelled", "expired"):
                    if expected <= 0:
                        continue
                    fill_rows = db.execute(
                        "SELECT amount FROM binance_demo_order_fills WHERE client_order_id=?",
                        (row["client_order_id"],),
                    ).fetchall()
                    with localcontext() as context:
                        context.prec = 120
                        recorded = sum((Decimal(item["amount"]) for item in fill_rows), Decimal("0"))
                    if recorded == expected:
                        continue
                candidates.append(self._row_dict(row))
                if len(candidates) >= limit:
                    break
        return candidates

    @staticmethod
    def _canonical_fill(fill: dict, order: sqlite3.Row) -> dict:
        if not isinstance(fill, dict):
            raise DemoOrderStoreError("Exchange trade record must be an object")
        trade_id = fill.get("trade_id")
        exchange_order_id = fill.get("exchange_order_id")
        symbol = fill.get("symbol")
        side = fill.get("side")
        if not isinstance(trade_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", trade_id):
            raise DemoOrderStoreError("Exchange trade ID is missing or invalid")
        if str(exchange_order_id) != str(order["exchange_order_id"]):
            raise DemoOrderConflict("Trade belongs to a different exchange order")
        if symbol != order["symbol"] or side != order["side"]:
            raise DemoOrderConflict("Trade identity does not match the immutable Demo order")
        normalized = {"trade_id": trade_id.strip()}
        for field in ("amount", "price", "cost"):
            value = fill.get(field)
            if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+)?", value) or len(value) > 100:
                raise DemoOrderStoreError(f"Exchange trade {field} must be a plain decimal string")
            try:
                parsed = Decimal(value)
            except (InvalidOperation, ValueError):
                raise DemoOrderStoreError(f"Exchange trade {field} is malformed") from None
            if not parsed.is_finite() or parsed <= 0 or len(parsed.as_tuple().digits) > 50:
                raise DemoOrderStoreError(f"Exchange trade {field} must be finite and positive")
            normalized[field] = format(parsed, "f")
        fee_cost = fill.get("fee_cost")
        fee_currency = fill.get("fee_currency")
        if fee_cost is None:
            raise DemoOrderStoreError("Exchange trade commission evidence is missing")
        if fee_cost is not None:
            if not isinstance(fee_cost, str) or not re.fullmatch(r"\d+(?:\.\d+)?", fee_cost) or len(fee_cost) > 100:
                raise DemoOrderStoreError("Exchange trade fee must be a plain decimal string")
            try:
                fee_value = Decimal(fee_cost)
            except (InvalidOperation, ValueError):
                raise DemoOrderStoreError("Exchange trade fee is malformed") from None
            if not fee_value.is_finite() or fee_value < 0:
                raise DemoOrderStoreError("Exchange trade fee must be finite and non-negative")
            normalized["fee_cost"] = format(fee_value, "f")
            if not isinstance(fee_currency, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,24}", fee_currency):
                raise DemoOrderStoreError("Exchange trade fee currency is required when fee is present")
            normalized["fee_currency"] = fee_currency.strip().upper()
        else:
            if fee_currency is not None:
                raise DemoOrderStoreError("Exchange trade fee currency cannot exist without a fee amount")
            normalized["fee_cost"] = None
            normalized["fee_currency"] = None
        timestamp_ms = fill.get("timestamp_ms")
        if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int) or timestamp_ms <= 0:
            raise DemoOrderStoreError("Exchange trade timestamp must be a positive integer")
        normalized["timestamp_ms"] = timestamp_ms
        normalized["exchange_order_id"] = str(exchange_order_id)
        normalized["symbol"] = symbol
        normalized["side"] = side
        return normalized

    def record_fills(
        self,
        client_order_id: str,
        fills: list[dict],
        *,
        lease_token: str | None = None,
    ) -> dict:
        if not isinstance(fills, list):
            raise DemoOrderStoreError("Exchange fills must be a list")
        inserted = 0
        stamp = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._assert_reconciliation_lease(db, lease_token)
            order = db.execute(
                "SELECT * FROM binance_demo_orders WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
            if order is None:
                raise DemoOrderStoreError("Demo order intent not found")
            if not order["exchange_order_id"]:
                raise DemoOrderConflict("Cannot record fills before the exchange order ID is confirmed")
            if order["status"] not in ("filled", "partially_filled", "cancelled", "expired"):
                raise DemoOrderConflict("Trade fills can only be reconciled for filled, partial or cancelled orders")
            for raw_fill in fills:
                fill = self._canonical_fill(raw_fill, order)
                payload_json = json.dumps(fill, sort_keys=True, separators=(",", ":"))
                existing = db.execute(
                    "SELECT payload_json,client_order_id FROM binance_demo_order_fills WHERE symbol=? AND exchange_trade_id=?",
                    (fill["symbol"], fill["trade_id"]),
                ).fetchone()
                if existing:
                    if existing["client_order_id"] != client_order_id or existing["payload_json"] != payload_json:
                        raise DemoOrderConflict("Exchange trade ID was reused with different fill evidence")
                    continue
                db.execute(
                    """
                    INSERT INTO binance_demo_order_fills(
                        exchange_trade_id,client_order_id,exchange_order_id,symbol,side,
                        amount,price,cost,fee_cost,fee_currency,timestamp_ms,payload_json,recorded_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        fill["trade_id"], client_order_id, fill["exchange_order_id"],
                        fill["symbol"], fill["side"], fill["amount"], fill["price"],
                        fill["cost"], fill["fee_cost"], fill["fee_currency"],
                        fill["timestamp_ms"], payload_json, stamp,
                    ),
                )
                inserted += 1

            rows = db.execute(
                "SELECT amount,fee_cost,fee_currency FROM binance_demo_order_fills WHERE client_order_id=?",
                (client_order_id,),
            ).fetchall()
            with localcontext() as context:
                context.prec = 120
                total_filled = sum((Decimal(row["amount"]) for row in rows), Decimal("0"))
                order_filled = Decimal(order["filled"] or "0")
                if total_filled > order_filled:
                    raise DemoOrderConflict("Individual trade fills exceed the exchange cumulative filled quantity")
                fee_totals: dict[str, Decimal] = {}
                for row in rows:
                    if row["fee_cost"] is not None:
                        currency = row["fee_currency"]
                        fee_totals[currency] = fee_totals.get(currency, Decimal("0")) + Decimal(row["fee_cost"])
            self._insert_event(
                db, client_order_id, order["status"], order["status"],
                {
                    "source": "individual_trade_fill_reconciliation",
                    "inserted_fills": inserted,
                    "total_fill_records": len(rows),
                    "individual_filled_amount": format(total_filled, "f"),
                    "exchange_cumulative_filled": format(order_filled, "f"),
                    "fill_coverage_complete": total_filled == order_filled,
                    "fees_by_currency": {key: format(value, "f") for key, value in sorted(fee_totals.items())},
                },
                stamp,
            )
        return {
            "client_order_id": client_order_id,
            "inserted_fills": inserted,
            "total_fill_records": len(rows),
            "individual_filled_amount": format(total_filled, "f"),
            "exchange_cumulative_filled": format(order_filled, "f"),
            "fill_coverage_complete": total_filled == order_filled,
            "fees_by_currency": {key: format(value, "f") for key, value in sorted(fee_totals.items())},
        }

    def list_fills(self, client_order_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT exchange_trade_id,exchange_order_id,symbol,side,amount,price,cost,
                       fee_cost,fee_currency,timestamp_ms,recorded_at
                FROM binance_demo_order_fills
                WHERE client_order_id=? ORDER BY timestamp_ms,exchange_trade_id
                """,
                (client_order_id,),
            ).fetchall()
        return [dict(row) for row in rows]

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
