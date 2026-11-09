import sqlite3
from pathlib import Path
from threading import RLock
from datetime import datetime, timezone

class PortfolioError(ValueError): pass

class PaperPortfolio:
    def __init__(self, database_path: str, initial_cash: float = 20.0):
        self.path = database_path
        self.lock = RLock()
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY CHECK(id=1), initial_cash REAL, cash REAL, realised_pnl REAL DEFAULT 0)")
            db.execute("CREATE TABLE IF NOT EXISTS positions (symbol TEXT PRIMARY KEY, quantity REAL, average_price REAL)")
            db.execute("CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, side TEXT, quantity REAL, price REAL, fee REAL, total REAL, created_at TEXT)")
            db.execute("INSERT OR IGNORE INTO account(id,initial_cash,cash,realised_pnl) VALUES(1,?,?,0)", (initial_cash, initial_cash))

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def snapshot(self):
        with self.connect() as db:
            account = db.execute("SELECT * FROM account WHERE id=1").fetchone()
            positions = [dict(x) for x in db.execute("SELECT * FROM positions ORDER BY symbol")]
        invested = sum(p["quantity"]*p["average_price"] for p in positions)
        return {"currency":"EUR","initial_cash":account["initial_cash"],"cash":account["cash"],
                "positions":positions,"invested_value":invested,"equity":account["cash"]+invested,
                "realised_pnl":account["realised_pnl"],"mode":"paper"}

    def execute(self, symbol, side, quantity, price, fee_rate, slippage_rate, max_fraction, minimum):
        if not symbol.endswith("/EUR"): raise PortfolioError("Only EUR-quoted markets are supported in Sprint 1")
        if side not in ("buy","sell"): raise PortfolioError("Invalid side")
        if quantity <= 0 or price <= 0: raise PortfolioError("Quantity and price must be positive")
        fill = price*(1+slippage_rate if side=="buy" else 1-slippage_rate)
        gross, fee = quantity*fill, quantity*fill*fee_rate
        total = gross+fee if side=="buy" else gross-fee
        if gross < minimum: raise PortfolioError(f"Minimum order value is {minimum:.2f} EUR")
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            account = db.execute("SELECT * FROM account WHERE id=1").fetchone()
            pos = db.execute("SELECT * FROM positions WHERE symbol=?", (symbol,)).fetchone()
            if side == "buy":
                if gross > account["cash"]*max_fraction: raise PortfolioError("Order exceeds maximum allocation per trade")
                if total > account["cash"]: raise PortfolioError("Insufficient cash")
                old_qty = pos["quantity"] if pos else 0
                old_cost = old_qty*pos["average_price"] if pos else 0
                new_qty = old_qty+quantity
                avg = (old_cost+gross)/new_qty
                db.execute("UPDATE account SET cash=cash-? WHERE id=1",(total,))
                db.execute("INSERT INTO positions VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET quantity=excluded.quantity, average_price=excluded.average_price",(symbol,new_qty,avg))
            else:
                if not pos or quantity > pos["quantity"]+1e-12: raise PortfolioError("Insufficient position to sell")
                pnl = (fill-pos["average_price"])*quantity-fee
                remain = pos["quantity"]-quantity
                db.execute("UPDATE account SET cash=cash+?, realised_pnl=realised_pnl+? WHERE id=1",(total,pnl))
                if remain <= 1e-12: db.execute("DELETE FROM positions WHERE symbol=?",(symbol,))
                else: db.execute("UPDATE positions SET quantity=? WHERE symbol=?",(remain,symbol))
            stamp = datetime.now(timezone.utc).isoformat()
            cur = db.execute("INSERT INTO trades(symbol,side,quantity,price,fee,total,created_at) VALUES(?,?,?,?,?,?,?)",(symbol,side,quantity,fill,fee,total,stamp))
            return {"id":cur.lastrowid,"symbol":symbol,"side":side,"quantity":quantity,"price":fill,"fee":fee,"total":total,"created_at":stamp,"mode":"paper"}

    def history(self, limit=50):
        with self.connect() as db:
            return [dict(x) for x in db.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?",(limit,))]
