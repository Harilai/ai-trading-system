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
                "provider_id TEXT NOT NULL DEFAULT 'ccxt:kraken', "
                "instrument_id TEXT NOT NULL DEFAULT '', symbol TEXT NOT NULL, "
                "quantity REAL, average_price REAL, strategy_id TEXT, "
                "stop_loss REAL, take_profit REAL, "
                "PRIMARY KEY(provider_id, instrument_id))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS trades ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, side TEXT, quantity REAL, "
                "price REAL, fee REAL, total REAL, created_at TEXT, client_order_id TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS withdrawal_requests ("
                "id TEXT PRIMARY KEY, amount REAL NOT NULL, status TEXT NOT NULL, "
                "profit_component REAL NOT NULL DEFAULT 0, "
                "principal_component REAL NOT NULL DEFAULT 0, requested_at TEXT NOT NULL, decided_at TEXT)"
            )
            self._migrate_positions(db)
            self._ensure_column(db, "trades", "client_order_id", "TEXT")
            self._ensure_column(db, "trades", "provider_id", "TEXT NOT NULL DEFAULT 'ccxt:kraken'")
            self._ensure_column(db, "trades", "instrument_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(db, "account", "withdrawn_profit", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(
                db, "withdrawal_requests", "principal_component", "REAL NOT NULL DEFAULT 0"
            )
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
    def _migrate_positions(db) -> None:
        columns = {row["name"]: row for row in db.execute("PRAGMA table_info(positions)")}
        primary_key = {name for name, row in columns.items() if row["pk"]}
        if {"provider_id", "instrument_id"} <= columns.keys() and primary_key == {"provider_id", "instrument_id"}:
            return
        db.execute(
            "CREATE TABLE positions_provider_scoped ("
            "provider_id TEXT NOT NULL DEFAULT 'ccxt:kraken', "
            "instrument_id TEXT NOT NULL, symbol TEXT NOT NULL, quantity REAL, "
            "average_price REAL, strategy_id TEXT, stop_loss REAL, take_profit REAL, "
            "PRIMARY KEY(provider_id, instrument_id))"
        )
        if columns:
            db.execute(
                "INSERT INTO positions_provider_scoped "
                "(provider_id,instrument_id,symbol,quantity,average_price,strategy_id,stop_loss,take_profit) "
                "SELECT 'ccxt:kraken','ccxt:kraken:spot:' || UPPER(symbol),UPPER(symbol),"
                "quantity,average_price,"
                + ("strategy_id" if "strategy_id" in columns else "NULL") + ","
                + ("stop_loss" if "stop_loss" in columns else "NULL") + ","
                + ("take_profit" if "take_profit" in columns else "NULL")
                + " FROM positions"
            )
            db.execute("DROP TABLE positions")
        db.execute("ALTER TABLE positions_provider_scoped RENAME TO positions")

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
                for row in db.execute("SELECT * FROM positions ORDER BY provider_id, instrument_id")
            ]
            pending_withdrawals = float(db.execute(
                "SELECT COALESCE(SUM(amount),0) FROM withdrawal_requests WHERE status='pending'"
            ).fetchone()[0])

        cost_basis_value = 0.0
        market_value = 0.0
        unrealised_pnl = 0.0
        valuation_complete = valuation_prices is not None or not positions
        for position in positions:
            symbol = position["symbol"]
            instrument_id = position["instrument_id"]
            provider_id = position["provider_id"]
            cost_basis = float(position["average_price"])
            quantity = float(position["quantity"])
            cost_basis_value += quantity * cost_basis
            current_price = None
            if valuation_prices is not None:
                supplied = valuation_prices.get(instrument_id)
                if supplied is None:
                    supplied = valuation_prices.get(f"{provider_id}|{symbol}")
                if supplied is None:
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
        total_realised_profit = max(0.0, float(account["realised_pnl"]))
        net_realised_profit = max(
            0.0,
            total_realised_profit - float(account["withdrawn_profit"]),
        )
        withdrawal_reserve = max(
            0.0,
            total_realised_profit * 0.5 - float(account["withdrawn_profit"]),
        )
        reinvestment_reserve = total_realised_profit * 0.5
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
            "pending_withdrawals": pending_withdrawals,
            "available_cash_for_manual_withdrawal": max(0.0, cash - pending_withdrawals),
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
        provider_id: str = "ccxt:kraken",
        instrument_id: str | None = None,
        strategy_id: str | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ):
        symbol = symbol.strip().upper()
        provider_id = provider_id.strip()
        instrument_id = instrument_id or f"{provider_id}:spot:{symbol}"
        if not provider_id or not instrument_id:
            raise PortfolioError("Provider and instrument identity are required")
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
                "SELECT * FROM positions WHERE provider_id=? AND instrument_id=?",
                (provider_id, instrument_id),
            ).fetchone()

            if side == "buy":
                pending_withdrawals = float(db.execute(
                    "SELECT COALESCE(SUM(amount),0) FROM withdrawal_requests WHERE status='pending'"
                ).fetchone()[0])
                spendable_cash = max(0.0, float(account["cash"]) - pending_withdrawals)
                if gross > spendable_cash * max_fraction:
                    raise PortfolioError("Order exceeds maximum allocation of unreserved cash")
                if total > spendable_cash:
                    raise PortfolioError("Insufficient unreserved cash after pending withdrawals")
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
                    "INSERT INTO positions(provider_id,instrument_id,symbol,quantity,average_price,strategy_id,stop_loss,take_profit) "
                    "VALUES(?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(provider_id,instrument_id) DO UPDATE SET "
                    "symbol=excluded.symbol, quantity=excluded.quantity, average_price=excluded.average_price, "
                    "strategy_id=excluded.strategy_id, stop_loss=excluded.stop_loss, "
                    "take_profit=excluded.take_profit",
                    (provider_id, instrument_id, symbol, new_quantity, average_cost, effective_strategy, effective_stop, effective_target),
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
                    db.execute("DELETE FROM positions WHERE provider_id=? AND instrument_id=?", (provider_id, instrument_id))
                else:
                    db.execute("UPDATE positions SET quantity=? WHERE provider_id=? AND instrument_id=?", (remaining, provider_id, instrument_id))

            stamp = datetime.now(timezone.utc).isoformat()
            cursor = db.execute(
                "INSERT INTO trades(symbol,side,quantity,price,fee,total,created_at,client_order_id,provider_id,instrument_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (symbol, side, quantity, fill, fee, total, stamp, client_order_id, provider_id, instrument_id),
            )
            return {
                "id": cursor.lastrowid,
                "symbol": symbol,
                "provider_id": provider_id,
                "instrument_id": instrument_id,
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
            pending = float(db.execute(
                "SELECT COALESCE(SUM(amount),0) FROM withdrawal_requests WHERE status='pending'"
            ).fetchone()[0])
            available_cash = max(0.0, float(account["cash"]) - pending)
            if amount > available_cash + 1e-9:
                raise PortfolioError(
                    "Withdrawal exceeds available cash after pending requests; "
                    "invested positions are not liquidated automatically"
                )
            db.execute(
                "INSERT INTO withdrawal_requests("
                "id,amount,status,requested_at) VALUES(?,?,'pending',?)",
                (request_id, float(amount), stamp),
            )
        return {
            "id": request_id,
            "amount": float(amount),
            "status": "pending",
            "requested_at": stamp,
            "funding_source": "available_cash",
        }

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
            total_realised_profit = max(0.0, float(account["realised_pnl"]))
            withdrawal_reserve = max(
                0.0,
                total_realised_profit * 0.5 - float(account["withdrawn_profit"]),
            )
            profit_component = min(amount, withdrawal_reserve)
            principal_component = amount - profit_component
            db.execute(
                "UPDATE account SET cash=cash-?, withdrawn_profit=withdrawn_profit+? WHERE id=1",
                (amount, profit_component),
            )
            db.execute(
                "UPDATE withdrawal_requests SET status='approved', profit_component=?, "
                "principal_component=?, decided_at=? WHERE id=?",
                (profit_component, principal_component, stamp, request_id),
            )
            result = dict(request)
            result.update({
                "status": "approved",
                "profit_component": profit_component,
                "principal_component": principal_component,
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
