import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4


class PortfolioError(ValueError):
    pass


class PaperPortfolio:
    def __init__(self, database_path: str, initial_cash: float = 20.0):
        self.path = database_path
        self.lock = RLock()
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS account ("
                "id INTEGER PRIMARY KEY CHECK(id=1), initial_cash REAL, cash REAL, "
                "realised_pnl REAL DEFAULT 0, withdrawn_profit REAL DEFAULT 0)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS positions ("
                "symbol TEXT PRIMARY KEY, quantity REAL, average_price REAL, "
                "strategy_id TEXT, stop_loss REAL, take_profit REAL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS trades ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, side TEXT, quantity REAL, "
                "price REAL, fee REAL, total REAL, created_at TEXT, client_order_id TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS withdrawal_requests ("
                "id TEXT PRIMARY KEY, amount REAL NOT NULL, status TEXT NOT NULL, "
                "profit_component REAL NOT NULL DEFAULT 0, requested_at TEXT NOT NULL, decided_at TEXT)"
            )
            self._ensure_column(db, "positions", "strategy_id", "TEXT")
            self._ensure_column(db, "positions", "stop_loss", "REAL")
            self._ensure_column(db, "positions", "take_profit", "REAL")
            self._ensure_column(db, "trades", "client_order_id", "TEXT")
            self._ensure_column(db, "account", "withdrawn_profit", "REAL NOT NULL DEFAULT 0")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_client_order_id "
                "ON trades(client_order_id) WHERE client_order_id IS NOT NULL"
            )
            db.execute(
                "INSERT OR IGNORE INTO account(id,initial_cash,cash,realised_pnl,withdrawn_profit) "
                "VALUES(1,?,?,0,0)",
                (initial_cash, initial_cash),
            )

    @staticmethod
    def _ensure_column(db, table: str, column: str, declaration: str) -> None:
        columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def snapshot(self, valuation_prices: dict[str, float] | None = None):
        with self.connect() as db:
            account = db.execute("SELECT * FROM account WHERE id=1").fetchone()
            positions = [
                dict(row)
                for row in db.execute("SELECT * FROM positions ORDER BY symbol")
            ]

        cost_basis_value = 0.0
        market_value = 0.0
        unrealised_pnl = 0.0
        valuation_complete = valuation_prices is not None or not positions
        for position in positions:
            symbol = position["symbol"]
            cost_basis = float(position["average_price"])
            quantity = float(position["quantity"])
            cost_basis_value += quantity * cost_basis
            current_price = None
            if valuation_prices is not None:
                supplied = valuation_prices.get(symbol)
                if supplied is not None and math.isfinite(float(supplied)) and float(supplied) > 0:
                    current_price = float(supplied)
            if current_price is None:
                valuation_complete = False
                current_price = cost_basis
            position_market_value = quantity * current_price
            position_unrealised = (current_price - cost_basis) * quantity
            market_value += position_market_value
            unrealised_pnl += position_unrealised
            position["market_price"] = current_price
            position["market_value"] = position_market_value
            position["unrealised_pnl"] = position_unrealised

        cash = float(account["cash"])
        net_realised_profit = max(
            0.0,
            float(account["realised_pnl"]) - float(account["withdrawn_profit"]),
        )
        withdrawal_reserve = net_realised_profit * 0.5
        reinvestment_reserve = net_realised_profit * 0.5
        return {
            "currency": "EUR",
            "initial_cash": account["initial_cash"],
            "cash": cash,
            "positions": positions,
            "invested_value": cost_basis_value,
            "market_value": market_value,
            "unrealised_pnl": unrealised_pnl,
            "equity": cash + market_value,
            "valuation_complete": valuation_complete,
            "valuation_source": "market" if valuation_prices is not None and valuation_complete else "cost_basis_or_partial",
            "realised_pnl": account["realised_pnl"],
            "net_realised_profit_available": net_realised_profit,
            "withdrawn_profit": account["withdrawn_profit"],
            "withdrawal_reserve": withdrawal_reserve,
            "reinvestment_reserve": reinvestment_reserve,
            "withdrawable_reserved_profit": min(cash, withdrawal_reserve),
            "available_cash_for_manual_withdrawal": cash,
            "mode": "paper",
        }

    def get_trade_by_client_order_id(self, client_order_id: str):
        if not client_order_id:
            return None
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM trades WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
        return dict(row) if row else None

    def execute(
        self,
        symbol,
        side,
        quantity,
        price,
        fee_rate,
        slippage_rate,
        max_fraction,
        minimum,
        *,
        client_order_id: str | None = None,
        strategy_id: str | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ):
        symbol = symbol.strip().upper()
        side = side.lower()
        if not symbol.endswith("/EUR"):
            raise PortfolioError("Only EUR-quoted markets are supported")
        if side not in ("buy", "sell"):
            raise PortfolioError("Invalid side")
        if quantity <= 0 or price <= 0:
            raise PortfolioError("Quantity and price must be positive")
        if fee_rate < 0 or slippage_rate < 0 or slippage_rate >= 1:
            raise PortfolioError("Fee and slippage rates are invalid")
        if not 0 < max_fraction <= 1:
            raise PortfolioError("Maximum allocation fraction must be in (0, 1]")
        if side == "buy":
            if stop_loss is not None and not 0 < stop_loss < price:
                raise PortfolioError("Buy stop-loss must be positive and below the reference price")
            if take_profit is not None and take_profit <= price:
                raise PortfolioError("Buy take-profit must be above the reference price")

        fill = price * (1 + slippage_rate if side == "buy" else 1 - slippage_rate)
        gross, fee = quantity * fill, quantity * fill * fee_rate
        total = gross + fee if side == "buy" else gross - fee
        if gross < minimum:
            raise PortfolioError(f"Minimum order value is {minimum:.2f} EUR")

        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if client_order_id:
                existing = db.execute(
                    "SELECT * FROM trades WHERE client_order_id=?",
                    (client_order_id,),
                ).fetchone()
                if existing:
                    result = dict(existing)
                    result["mode"] = "paper"
                    result["idempotent_replay"] = True
                    return result

            account = db.execute("SELECT * FROM account WHERE id=1").fetchone()
            position = db.execute(
                "SELECT * FROM positions WHERE symbol=?",
                (symbol,),
            ).fetchone()

            if side == "buy":
                if gross > account["cash"] * max_fraction:
                    raise PortfolioError("Order exceeds maximum allocation per trade")
                if total > account["cash"]:
                    raise PortfolioError("Insufficient cash")
                old_quantity = position["quantity"] if position else 0.0
                old_cost = old_quantity * position["average_price"] if position else 0.0
                new_quantity = old_quantity + quantity
                average_cost = (old_cost + gross + fee) / new_quantity
                effective_strategy = strategy_id or (position["strategy_id"] if position else None)
                effective_stop = stop_loss if stop_loss is not None else (
                    position["stop_loss"] if position else None
                )
                effective_target = take_profit if take_profit is not None else (
                    position["take_profit"] if position else None
                )
                db.execute("UPDATE account SET cash=cash-? WHERE id=1", (total,))
                db.execute(
                    "INSERT INTO positions(symbol,quantity,average_price,strategy_id,stop_loss,take_profit) "
                    "VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(symbol) DO UPDATE SET "
                    "quantity=excluded.quantity, average_price=excluded.average_price, "
                    "strategy_id=excluded.strategy_id, stop_loss=excluded.stop_loss, "
                    "take_profit=excluded.take_profit",
                    (symbol, new_quantity, average_cost, effective_strategy, effective_stop, effective_target),
                )
            else:
                if not position or quantity > position["quantity"] + 1e-12:
                    raise PortfolioError("Insufficient position to sell")
                realised_pnl = (fill - position["average_price"]) * quantity - fee
                remaining = position["quantity"] - quantity
                db.execute(
                    "UPDATE account SET cash=cash+?, realised_pnl=realised_pnl+? WHERE id=1",
                    (total, realised_pnl),
                )
                if remaining <= 1e-12:
                    db.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
                else:
                    db.execute("UPDATE positions SET quantity=? WHERE symbol=?", (remaining, symbol))

            stamp = datetime.now(timezone.utc).isoformat()
            cursor = db.execute(
                "INSERT INTO trades(symbol,side,quantity,price,fee,total,created_at,client_order_id) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (symbol, side, quantity, fill, fee, total, stamp, client_order_id),
            )
            return {
                "id": cursor.lastrowid,
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "price": fill,
                "fee": fee,
                "total": total,
                "created_at": stamp,
                "client_order_id": client_order_id,
                "mode": "paper",
                "idempotent_replay": False,
            }

    def request_withdrawal(self, amount: float) -> dict:
        if not math.isfinite(float(amount)) or amount <= 0:
            raise PortfolioError("Withdrawal amount must be a positive finite number")
        request_id = str(uuid4())
        stamp = datetime.now(timezone.utc).isoformat()
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            account = db.execute("SELECT cash FROM account WHERE id=1").fetchone()
            if amount > account["cash"] + 1e-9:
                raise PortfolioError("Withdrawal exceeds available cash; invested positions are not liquidated automatically")
            db.execute(
                "INSERT INTO withdrawal_requests(id,amount,status,requested_at) VALUES(?,?,'pending',?)",
                (request_id, float(amount), stamp),
            )
        return {"id": request_id, "amount": float(amount), "status": "pending", "requested_at": stamp}

    def approve_withdrawal(self, request_id: str) -> dict:
        stamp = datetime.now(timezone.utc).isoformat()
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            request = db.execute(
                "SELECT * FROM withdrawal_requests WHERE id=?",
                (request_id,),
            ).fetchone()
            if request is None:
                raise PortfolioError("Withdrawal request not found")
            if request["status"] != "pending":
                raise PortfolioError("Withdrawal request is not pending")
            account = db.execute("SELECT * FROM account WHERE id=1").fetchone()
            amount = float(request["amount"])
            if amount > account["cash"] + 1e-9:
                raise PortfolioError("Available cash changed; withdrawal cannot be approved")
            profit_available = max(
                0.0,
                float(account["realised_pnl"]) - float(account["withdrawn_profit"]),
            )
            profit_component = min(amount, profit_available)
            db.execute(
                "UPDATE account SET cash=cash-?, withdrawn_profit=withdrawn_profit+? WHERE id=1",
                (amount, profit_component),
            )
            db.execute(
                "UPDATE withdrawal_requests SET status='approved', profit_component=?, decided_at=? WHERE id=?",
                (profit_component, stamp, request_id),
            )
            result = dict(request)
            result.update({
                "status": "approved",
                "profit_component": profit_component,
                "decided_at": stamp,
            })
            return result

    def cancel_withdrawal(self, request_id: str) -> dict:
        stamp = datetime.now(timezone.utc).isoformat()
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            request = db.execute(
                "SELECT * FROM withdrawal_requests WHERE id=?",
                (request_id,),
            ).fetchone()
            if request is None:
                raise PortfolioError("Withdrawal request not found")
            if request["status"] != "pending":
                raise PortfolioError("Only pending withdrawal requests can be cancelled")
            db.execute(
                "UPDATE withdrawal_requests SET status='cancelled', decided_at=? WHERE id=?",
                (stamp, request_id),
            )
            result = dict(request)
            result.update({"status": "cancelled", "decided_at": stamp})
            return result

    def withdrawal_history(self, limit: int = 50):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM withdrawal_requests ORDER BY requested_at DESC LIMIT ?",
                    (limit,),
                )
            ]

    def history(self, limit=50):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM trades ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            ]
