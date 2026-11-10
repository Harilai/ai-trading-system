import json
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
        try:
            initial_cash = float(initial_cash)
        except (TypeError, ValueError, OverflowError) as exc:
            raise PortfolioError("Initial cash must be a non-negative finite number") from exc
        if not math.isfinite(initial_cash) or initial_cash < 0:
            raise PortfolioError("Initial cash must be a non-negative finite number")
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
                "CREATE TABLE IF NOT EXISTS order_intents ("
                "client_order_id TEXT PRIMARY KEY, status TEXT NOT NULL, "
                "request_json TEXT NOT NULL, decision_json TEXT, "
                "trade_id INTEGER, rejection_reason TEXT, fill_json TEXT, "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS order_intent_events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, client_order_id TEXT NOT NULL, "
                "from_status TEXT, to_status TEXT NOT NULL, details_json TEXT, "
                "created_at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS currency_balances ("
                "currency TEXT PRIMARY KEY, balance REAL NOT NULL CHECK(balance >= 0), "
                "updated_at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS currency_conversions ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "client_conversion_id TEXT NOT NULL UNIQUE, "
                "from_currency TEXT NOT NULL, to_currency TEXT NOT NULL, "
                "from_amount REAL NOT NULL, gross_to_amount REAL NOT NULL, "
                "fee REAL NOT NULL, fee_eur REAL NOT NULL DEFAULT 0, "
                "net_to_amount REAL NOT NULL, rate REAL NOT NULL, "
                "fx_provider_id TEXT NOT NULL, fx_timestamp INTEGER NOT NULL, "
                "created_at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS withdrawal_requests ("
                "id TEXT PRIMARY KEY, amount REAL NOT NULL, status TEXT NOT NULL, "
                "profit_component REAL NOT NULL DEFAULT 0, "
                "non_reserved_component REAL NOT NULL DEFAULT 0, requested_at TEXT NOT NULL, decided_at TEXT)"
            )
            self._migrate_positions(db)
            self._ensure_column(db, "trades", "client_order_id", "TEXT")
            self._ensure_column(db, "trades", "provider_id", "TEXT NOT NULL DEFAULT 'ccxt:kraken'")
            self._ensure_column(db, "trades", "instrument_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(db, "account", "withdrawn_profit", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(db, "positions", "quote_currency", "TEXT NOT NULL DEFAULT 'EUR'")
            self._ensure_column(db, "positions", "average_price_eur", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(db, "trades", "quote_currency", "TEXT NOT NULL DEFAULT 'EUR'")
            self._ensure_column(db, "trades", "fx_rate_to_eur", "REAL NOT NULL DEFAULT 1")
            self._ensure_column(db, "trades", "total_eur", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(db, "trades", "fx_provider_id", "TEXT")
            self._ensure_column(db, "trades", "fx_timestamp", "INTEGER")
            self._ensure_column(db, "trades", "strategy_id", "TEXT")
            self._ensure_column(db, "trades", "stop_loss", "REAL")
            self._ensure_column(db, "trades", "take_profit", "REAL")
            self._ensure_column(db, "trades", "market_timestamp", "INTEGER")
            self._ensure_column(db, "trades", "market_timestamp", "INTEGER")
            self._ensure_column(db, "currency_conversions", "fee_eur", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(
                db, "withdrawal_requests", "non_reserved_component", "REAL NOT NULL DEFAULT 0"
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
            eur_cash = float(db.execute("SELECT cash FROM account WHERE id=1").fetchone()[0])
            stamp = datetime.now(timezone.utc).isoformat()
            db.execute(
                "INSERT OR IGNORE INTO currency_balances(currency,balance,updated_at) VALUES('EUR',?,?)",
                (eur_cash, stamp),
            )
            db.execute(
                "UPDATE positions SET average_price_eur=average_price WHERE quote_currency='EUR' "
                "AND average_price_eur=0"
            )
            db.execute(
                "UPDATE trades SET total_eur=total WHERE quote_currency='EUR' AND total_eur=0"
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

    def cash_balances(self) -> list[dict]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT currency,balance,updated_at FROM currency_balances ORDER BY currency"
                )
            ]

    def snapshot(
        self,
        valuation_prices: dict[str, float] | None = None,
        fx_rates: dict | None = None,
    ):
        fx_rates = fx_rates or {}
        with self.connect() as db:
            account = db.execute("SELECT * FROM account WHERE id=1").fetchone()
            positions = [
                dict(row)
                for row in db.execute("SELECT * FROM positions ORDER BY provider_id, instrument_id")
            ]
            balances = [
                dict(row)
                for row in db.execute(
                    "SELECT currency,balance,updated_at FROM currency_balances ORDER BY currency"
                )
            ]
            pending_withdrawals = float(db.execute(
                "SELECT COALESCE(SUM(amount),0) FROM withdrawal_requests WHERE status='pending'"
            ).fetchone()[0])

        cash_by_currency = {item["currency"]: float(item["balance"]) for item in balances}
        eur_cash = cash_by_currency.get("EUR", float(account["cash"]))
        valuation_complete = True
        cash_total_eur = eur_cash
        normalized_fx = {}
        for currency, raw in fx_rates.items():
            try:
                if isinstance(raw, dict):
                    rate = float(raw["rate"])
                    metadata = {
                        "symbol": raw.get("symbol"),
                        "provider_id": raw.get("provider_id"),
                        "timestamp": raw.get("timestamp"),
                        "inverted": bool(raw.get("inverted", False)),
                    }
                else:
                    rate = float(raw)
                    metadata = {"symbol": f"{currency}/EUR", "provider_id": None, "timestamp": None, "inverted": False}
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(rate) and rate > 0:
                normalized_fx[currency] = {"rate": rate, **metadata}

        cash_balance_details = []
        for currency, amount in cash_by_currency.items():
            if currency == "EUR":
                rate = 1.0
                metadata = {"provider_id": None, "timestamp": None, "symbol": "EUR/EUR"}
            else:
                fx = normalized_fx.get(currency)
                rate = fx["rate"] if fx else None
                metadata = fx or {"provider_id": None, "timestamp": None, "symbol": f"{currency}/EUR"}
                if rate is None and abs(amount) > 1e-12:
                    valuation_complete = False
            value_eur = amount * rate if rate is not None else (0.0 if abs(amount) <= 1e-12 else None)
            if value_eur is not None:
                cash_total_eur += value_eur if currency != "EUR" else 0
            cash_balance_details.append({
                "currency": currency,
                "balance": amount,
                "updated_at": next((item["updated_at"] for item in balances if item["currency"] == currency), None),
                "fx_rate_to_eur": rate,
                "value_eur": value_eur,
                "fx_provider_id": metadata.get("provider_id"),
                "fx_timestamp": metadata.get("timestamp"),
            })

        cost_basis_value = 0.0
        market_value = 0.0
        unrealised_pnl = 0.0
        for position in positions:
            symbol = position["symbol"]
            instrument_id = position["instrument_id"]
            provider_id = position["provider_id"]
            currency = str(position.get("quote_currency") or "EUR").upper()
            cost_basis_native = float(position["average_price"])
            cost_basis_eur = float(position.get("average_price_eur") or cost_basis_native)
            quantity = float(position["quantity"])
            cost_basis_value += quantity * cost_basis_eur
            current_price = None
            if valuation_prices is not None:
                supplied = valuation_prices.get(instrument_id)
                if supplied is None:
                    supplied = valuation_prices.get(f"{provider_id}|{symbol}")
                if supplied is None:
                    supplied = valuation_prices.get(symbol)
                if supplied is not None:
                    try:
                        candidate = float(supplied)
                        if math.isfinite(candidate) and candidate > 0:
                            current_price = candidate
                    except (TypeError, ValueError, OverflowError):
                        pass
            if current_price is None:
                valuation_complete = False
                current_price = cost_basis_native

            if currency == "EUR":
                fx_to_eur = 1.0
            else:
                fx = normalized_fx.get(currency)
                fx_to_eur = fx["rate"] if fx else None
                if fx_to_eur is None:
                    valuation_complete = False

            if fx_to_eur is None:
                position_market_value_eur = quantity * cost_basis_eur
                position_unrealised_eur = 0.0 if current_price == cost_basis_native else None
            else:
                position_market_value_eur = quantity * current_price * fx_to_eur
                position_unrealised_eur = (
                    quantity * (current_price * fx_to_eur - cost_basis_eur)
                )
            market_value += position_market_value_eur
            if position_unrealised_eur is None:
                valuation_complete = False
            else:
                unrealised_pnl += position_unrealised_eur
            position["quote_currency"] = currency
            position["market_price"] = current_price
            position["market_value_native"] = quantity * current_price
            position["market_value_eur"] = position_market_value_eur
            position["fx_rate_to_eur"] = fx_to_eur
            position["fx_provider_id"] = normalized_fx.get(currency, {}).get("provider_id")
            position["fx_timestamp"] = normalized_fx.get(currency, {}).get("timestamp")
            position["market_value"] = position_market_value_eur
            position["unrealised_pnl"] = position_unrealised_eur

        total_realised_profit = max(0.0, float(account["realised_pnl"]))
        net_realised_profit = max(
            0.0,
            total_realised_profit - float(account["withdrawn_profit"]),
        )
        withdrawal_reserve = max(
            0.0,
            total_realised_profit * 0.5 - float(account["withdrawn_profit"]),
        )
        return {
            "currency": "EUR",
            "initial_cash": account["initial_cash"],
            "cash": eur_cash,
            "cash_balances": cash_balance_details,
            "cash_total_eur": cash_total_eur,
            "fx_observations": normalized_fx,
            "positions": positions,
            "invested_value": cost_basis_value,
            "market_value": market_value,
            "unrealised_pnl": unrealised_pnl,
            "equity": cash_total_eur + market_value,
            "valuation_complete": valuation_complete,
            "valuation_source": "market_and_fx" if valuation_prices is not None and valuation_complete else "cost_basis_or_partial",
            "realised_pnl": account["realised_pnl"],
            "net_realised_profit_available": net_realised_profit,
            "withdrawn_profit": account["withdrawn_profit"],
            "withdrawal_reserve": withdrawal_reserve,
            "reinvestment_reserve": total_realised_profit * 0.5,
            "withdrawable_reserved_profit": min(eur_cash, withdrawal_reserve),
            "pending_withdrawals": pending_withdrawals,
            "available_cash_for_manual_withdrawal": max(0.0, eur_cash - pending_withdrawals),
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

    @staticmethod
    def _normalize_order_request(request: dict) -> dict:
        if not isinstance(request, dict):
            raise PortfolioError("Order request must be an object")
        try:
            symbol = str(request["symbol"]).strip().upper()
            provider_id = str(request.get("provider_id") or "ccxt:kraken").strip()
            client_order_id = request.get("client_order_id")
            if client_order_id is not None:
                client_order_id = str(client_order_id).strip()
            quote_currency = str(
                request.get("quote_currency")
                or (symbol.rsplit("/", 1)[-1] if "/" in symbol else "EUR")
            ).strip().upper()
            instrument_id = request.get("instrument_id") or f"{provider_id}:spot:{symbol}"
            normalized = {
                "symbol": symbol,
                "side": str(request["side"]).strip().lower(),
                "quantity": float(request["quantity"]),
                "price": float(request["price"]),
                "fee_rate": float(request["fee_rate"]),
                "slippage_rate": float(request["slippage_rate"]),
                "max_fraction": float(request["max_fraction"]),
                "minimum": float(request["minimum"]),
                "client_order_id": client_order_id,
                "provider_id": provider_id,
                "instrument_id": str(instrument_id),
                "strategy_id": request.get("strategy_id"),
                "stop_loss": float(request["stop_loss"]) if request.get("stop_loss") is not None else None,
                "take_profit": float(request["take_profit"]) if request.get("take_profit") is not None else None,
                "quote_currency": quote_currency,
                "fx_rate_to_eur": float(request.get("fx_rate_to_eur", 1.0)),
                "fx_provider_id": request.get("fx_provider_id"),
                "fx_timestamp": request.get("fx_timestamp"),
                "market_timestamp": request.get("market_timestamp"),
                "risk_fx_rate_to_eur": float(request.get("risk_fx_rate_to_eur", 1.0)),
                "risk_fx_provider_id": request.get("risk_fx_provider_id"),
                "risk_fx_timestamp": request.get("risk_fx_timestamp"),
            }
            if normalized["fx_timestamp"] is not None:
                normalized["fx_timestamp"] = int(normalized["fx_timestamp"])
            if normalized["market_timestamp"] is not None:
                normalized["market_timestamp"] = int(normalized["market_timestamp"])
            if normalized["risk_fx_timestamp"] is not None:
                normalized["risk_fx_timestamp"] = int(normalized["risk_fx_timestamp"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise PortfolioError("Order request contains missing or invalid fields") from exc
        if not symbol or not provider_id or not normalized["instrument_id"]:
            raise PortfolioError("Order request requires symbol, provider and instrument identity")
        return normalized

    @staticmethod
    def _canonical_json(value: dict) -> str:
        try:
            return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError, OverflowError) as exc:
            raise PortfolioError("Order intent must contain finite JSON-compatible values") from exc

    def record_order_intent(
        self,
        client_order_id: str,
        request: dict,
        decision: dict | None = None,
    ) -> dict:
        """Durably persist a risk-approved paper order before attempting its fill."""
        if not isinstance(client_order_id, str) or not client_order_id.strip():
            raise PortfolioError("Order intent requires a non-blank client_order_id")
        normalized_request = self._normalize_order_request(request)
        if normalized_request.get("client_order_id") != client_order_id:
            raise PortfolioError("Order intent request must carry the matching client_order_id")
        request_json = self._canonical_json(normalized_request)
        decision_json = self._canonical_json(decision) if decision is not None else None
        stamp = datetime.now(timezone.utc).isoformat()
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM order_intents WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
            if existing:
                if existing["request_json"] != request_json:
                    raise PortfolioError("client_order_id was already used for a different order intent")
                if decision_json is not None and existing["decision_json"] not in (None, decision_json):
                    raise PortfolioError("client_order_id was already used for a different approved decision")
                return self._decode_order_intent(existing)
            if str(request.get("side", "")).lower() == "buy":
                unresolved = db.execute(
                    "SELECT client_order_id FROM order_intents "
                    "WHERE status IN ('risk_approved','reconciliation_required') "
                    "AND client_order_id != ? LIMIT 1",
                    (client_order_id,),
                ).fetchone()
                if unresolved:
                    raise PortfolioError(
                        f"New entries are blocked by unresolved order intent {unresolved['client_order_id']}"
                    )
            trade = db.execute(
                "SELECT id FROM trades WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
            status = "filled" if trade else "risk_approved"
            db.execute(
                "INSERT INTO order_intents(client_order_id,status,request_json,decision_json,trade_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    client_order_id, status, request_json, decision_json,
                    int(trade["id"]) if trade else None, stamp, stamp,
                ),
            )
            self._insert_order_intent_event(
                db, client_order_id, None, status,
                {"decision": decision, "trade_id": int(trade["id"]) if trade else None},
                stamp,
            )
        return self.get_order_intent(client_order_id)

    @staticmethod
    def _insert_order_intent_event(
        db,
        client_order_id: str,
        from_status: str | None,
        to_status: str,
        details: dict | None,
        stamp: str,
    ) -> None:
        db.execute(
            "INSERT INTO order_intent_events(client_order_id,from_status,to_status,details_json,created_at) "
            "VALUES(?,?,?,?,?)",
            (
                client_order_id,
                from_status,
                to_status,
                PaperPortfolio._canonical_json(details or {}),
                stamp,
            ),
        )

    @staticmethod
    def _decode_order_intent(row) -> dict:
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        raw_decision = result.pop("decision_json")
        result["decision"] = json.loads(raw_decision) if raw_decision else None
        raw_fill = result.pop("fill_json")
        result["fill"] = json.loads(raw_fill) if raw_fill else None
        return result

    @staticmethod
    def _order_intent_matches_trade(request: dict, trade: dict) -> bool:
        try:
            symbol = str(request["symbol"]).strip().upper()
            side = str(request["side"]).strip().lower()
            quantity = float(request["quantity"])
            reference_price = float(request["price"])
            fee_rate = float(request["fee_rate"])
            slippage_rate = float(request["slippage_rate"])
            fx_rate = float(request.get("fx_rate_to_eur", 1.0))
            fill_price = reference_price * (1 + slippage_rate if side == "buy" else 1 - slippage_rate)
            gross = quantity * fill_price
            fee = gross * fee_rate
            total = gross + fee if side == "buy" else gross - fee
            total_eur = total * fx_rate
            expected = {
                "symbol": symbol,
                "side": side,
                "provider_id": request["provider_id"],
                "instrument_id": request["instrument_id"],
                "quote_currency": str(request.get("quote_currency") or "EUR").upper(),
                "strategy_id": request.get("strategy_id"),
                "stop_loss": request.get("stop_loss"),
                "take_profit": request.get("take_profit"),
                "market_timestamp": request.get("market_timestamp"),
            }
            for key, value in expected.items():
                if trade.get(key) != value:
                    if key in {"stop_loss", "take_profit"} and trade.get(key) is not None and value is not None:
                        if not math.isclose(float(trade[key]), float(value), rel_tol=1e-12, abs_tol=1e-12):
                            return False
                    else:
                        return False
            numeric = (
                ("quantity", quantity),
                ("price", fill_price),
                ("fee", fee),
                ("total", total),
                ("fx_rate_to_eur", fx_rate),
                ("total_eur", total_eur),
            )
            return all(
                math.isclose(float(trade[key]), value, rel_tol=1e-12, abs_tol=1e-12)
                for key, value in numeric
            ) and trade.get("fx_provider_id") == request.get("fx_provider_id") and trade.get("fx_timestamp") == request.get("fx_timestamp")
        except (KeyError, TypeError, ValueError, OverflowError):
            return False

    def get_order_intent(self, client_order_id: str) -> dict | None:
        if not isinstance(client_order_id, str) or not client_order_id.strip():
            return None
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM order_intents WHERE client_order_id=?",
                (client_order_id.strip(),),
            ).fetchone()
        return self._decode_order_intent(row) if row else None

    @staticmethod
    def _order_request_is_fresh(
        request: dict,
        max_data_age_seconds: int,
        max_fx_age_seconds: int,
        now_ms: int,
    ) -> bool:
        try:
            market_timestamp = int(request.get("market_timestamp"))
            max_data_age = int(max_data_age_seconds) * 1000
        except (TypeError, ValueError, OverflowError):
            return False
        if market_timestamp <= 0 or market_timestamp > now_ms or now_ms - market_timestamp > max_data_age:
            return False
        for timestamp_value in (
            request.get("risk_fx_timestamp"),
            request.get("fx_timestamp") if str(request.get("quote_currency") or "EUR").upper() == "USD" else None,
        ):
            if timestamp_value is None:
                continue
            try:
                timestamp = int(timestamp_value)
                max_fx_age = int(max_fx_age_seconds) * 1000
            except (TypeError, ValueError, OverflowError):
                return False
            if timestamp <= 0 or timestamp > now_ms or now_ms - timestamp > max_fx_age:
                return False
        if str(request.get("quote_currency") or "EUR").upper() == "USD" and request.get("fx_timestamp") is None:
            return False
        return True

    def order_intent_is_fresh(
        self,
        client_order_id: str,
        max_data_age_seconds: int,
        max_fx_age_seconds: int,
        now_ms: int | None = None,
    ) -> bool:
        intent = self.get_order_intent(client_order_id)
        if not intent or intent["status"] != "risk_approved":
            return False
        now = int(now_ms if now_ms is not None else datetime.now(timezone.utc).timestamp() * 1000)
        return self._order_request_is_fresh(
            intent["request"], max_data_age_seconds, max_fx_age_seconds, now
        )

    def expire_order_intent(self, client_order_id: str, reason: str) -> bool:
        if not isinstance(reason, str) or not reason.strip():
            raise PortfolioError("Order intent expiry requires a reason")
        with self.lock, self.connect() as db:
            stamp = datetime.now(timezone.utc).isoformat()
            current = db.execute(
                "SELECT status FROM order_intents WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
            if current is None:
                return False
            if current["status"] == "expired":
                return True
            if current["status"] != "risk_approved":
                return False
            changed = db.execute(
                "UPDATE order_intents SET status='expired',rejection_reason=?,updated_at=? "
                "WHERE client_order_id=? AND status='risk_approved'",
                (reason.strip()[:2000], stamp, client_order_id),
            ).rowcount
            if changed == 1:
                self._insert_order_intent_event(
                    db, client_order_id, "risk_approved", "expired",
                    {"reason": reason.strip()[:500]}, stamp,
                )
            return changed == 1

    def list_order_intent_events(self, client_order_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM order_intent_events WHERE client_order_id=? ORDER BY id",
                (client_order_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json") or "{}")
            result.append(item)
        return result

    def list_order_intents(self, limit: int = 100) -> list[dict]:
        if not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise PortfolioError("Order intent limit must be between 1 and 1000")
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM order_intents ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._decode_order_intent(row) for row in rows]

    def reject_order_intent(self, client_order_id: str, reason: str) -> None:
        if not isinstance(reason, str) or not reason.strip():
            raise PortfolioError("Order rejection requires a reason")
        with self.lock, self.connect() as db:
            stamp = datetime.now(timezone.utc).isoformat()
            current = db.execute(
                "SELECT status FROM order_intents WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
            if current is None:
                raise PortfolioError("Order intent does not exist")
            changed = db.execute(
                "UPDATE order_intents SET status='rejected', rejection_reason=?, updated_at=? "
                "WHERE client_order_id=? AND status='risk_approved'",
                (reason.strip()[:2000], stamp, client_order_id),
            ).rowcount
            if changed == 1:
                self._insert_order_intent_event(
                    db, client_order_id, current["status"], "rejected",
                    {"reason": reason.strip()[:500]}, stamp,
                )
            elif current["status"] != "rejected":
                raise PortfolioError("Order intent cannot transition to rejected from its current state")

    def reconcile_order_intents(
        self,
        active_task_ids: set[str] | None = None,
        max_data_age_seconds: int = 60,
        max_fx_age_seconds: int = 300,
    ) -> dict:
        """Reconcile fills and expire stale unfilled intents without guessing."""
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        now_ms = int(now_dt.timestamp() * 1000)
        active = active_task_ids or set()
        filled = 0
        uncertain = 0
        expired = 0
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM order_intents "
                "WHERE status IN ('risk_approved','filled','reconciliation_required')"
            ).fetchall()
            for row in rows:
                trade_row = db.execute(
                    "SELECT * FROM trades WHERE client_order_id=?",
                    (row["client_order_id"],),
                ).fetchone()
                if trade_row:
                    trade = dict(trade_row)
                    request = json.loads(row["request_json"])
                    if not self._order_intent_matches_trade(request, trade):
                        if row["status"] != "reconciliation_required":
                            db.execute(
                                "UPDATE order_intents SET status='reconciliation_required',"
                                "rejection_reason='Trade ledger does not match persisted order intent',updated_at=? "
                                "WHERE client_order_id=?",
                                (now, row["client_order_id"]),
                            )
                            self._insert_order_intent_event(
                                db, row["client_order_id"], row["status"], "reconciliation_required",
                                {"trade_id": trade["id"], "reason": "trade_intent_mismatch"}, now,
                            )
                        uncertain += 1
                    elif row["status"] != "filled" or row["trade_id"] != trade["id"]:
                        db.execute(
                            "UPDATE order_intents SET status='filled',trade_id=?,fill_json=?,"
                            "rejection_reason=NULL,updated_at=? WHERE client_order_id=?",
                            (trade["id"], self._canonical_json(trade), now, row["client_order_id"]),
                        )
                        self._insert_order_intent_event(
                            db, row["client_order_id"], row["status"], "filled",
                            {"trade_id": trade["id"], "reconciled": True}, now,
                        )
                        filled += 1
                elif row["status"] == "filled":
                    db.execute(
                        "UPDATE order_intents SET status='reconciliation_required',"
                        "rejection_reason='Filled intent has no matching trade ledger row',updated_at=? "
                        "WHERE client_order_id=?",
                        (now, row["client_order_id"]),
                    )
                    self._insert_order_intent_event(
                        db, row["client_order_id"], "filled", "reconciliation_required",
                        {"reason": "missing_trade"}, now,
                    )
                    uncertain += 1
                elif row["status"] == "risk_approved" and row["client_order_id"] not in active:
                    request = json.loads(row["request_json"])
                    if not self._order_request_is_fresh(
                        request, max_data_age_seconds, max_fx_age_seconds, now_ms
                    ):
                        reason = "Approved intent market/FX evidence expired before paper fill"
                        db.execute(
                            "UPDATE order_intents SET status='expired',rejection_reason=?,updated_at=? "
                            "WHERE client_order_id=? AND status='risk_approved'",
                            (reason, now, row["client_order_id"]),
                        )
                        self._insert_order_intent_event(
                            db, row["client_order_id"], "risk_approved", "expired",
                            {"reason": reason}, now,
                        )
                        expired += 1
        return {
            "reconciled_filled": filled,
            "reconciliation_required": uncertain,
            "expired": expired,
        }

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
        quote_currency: str | None = None,
        fx_rate_to_eur: float = 1.0,
        fx_provider_id: str | None = None,
        fx_timestamp: int | None = None,
        market_timestamp: int | None = None,
        risk_fx_rate_to_eur: float = 1.0,
        risk_fx_provider_id: str | None = None,
        risk_fx_timestamp: int | None = None,
    ):
        if not isinstance(symbol, str) or not isinstance(provider_id, str):
            raise PortfolioError("Symbol and provider identity must be text")
        symbol = symbol.strip().upper()
        provider_id = provider_id.strip()
        quote_currency = (
            str(quote_currency).strip().upper()
            if quote_currency is not None
            else (symbol.rsplit("/", 1)[-1] if "/" in symbol else "EUR")
        )
        instrument_id = instrument_id or f"{provider_id}:spot:{symbol}"
        if not provider_id or not instrument_id:
            raise PortfolioError("Provider and instrument identity are required")
        if client_order_id is not None:
            if not isinstance(client_order_id, str) or not client_order_id.strip():
                raise PortfolioError("client_order_id must not be blank")
            client_order_id = client_order_id.strip()
        if quote_currency not in {"EUR", "USD"}:
            raise PortfolioError("Only EUR and USD quote currencies are supported")
        side = side.lower() if isinstance(side, str) else ""
        if side not in ("buy", "sell"):
            raise PortfolioError("Invalid side")
        try:
            quantity = float(quantity)
            price = float(price)
            fee_rate = float(fee_rate)
            slippage_rate = float(slippage_rate)
            max_fraction = float(max_fraction)
            minimum = float(minimum)
            fx_rate_to_eur = float(fx_rate_to_eur)
            risk_fx_rate_to_eur = float(risk_fx_rate_to_eur)
            if stop_loss is not None:
                stop_loss = float(stop_loss)
            if take_profit is not None:
                take_profit = float(take_profit)
        except (TypeError, ValueError, OverflowError) as exc:
            raise PortfolioError("Execution values must be finite numbers") from exc
        numeric_values = [quantity, price, fee_rate, slippage_rate, max_fraction, minimum, fx_rate_to_eur, risk_fx_rate_to_eur]
        numeric_values.extend(value for value in (stop_loss, take_profit) if value is not None)
        if not all(math.isfinite(value) for value in numeric_values):
            raise PortfolioError("Execution values must be finite numbers")
        if quantity <= 0 or price <= 0 or fx_rate_to_eur <= 0 or risk_fx_rate_to_eur <= 0:
            raise PortfolioError("Quantity, price and FX rate must be positive")
        if quote_currency == "EUR" and not math.isclose(fx_rate_to_eur, 1.0, rel_tol=0, abs_tol=1e-12):
            raise PortfolioError("EUR-quoted instruments must use an EUR conversion rate of 1")
        if risk_fx_timestamp is not None:
            try:
                risk_fx_timestamp = int(risk_fx_timestamp)
            except (TypeError, ValueError, OverflowError) as exc:
                raise PortfolioError("Risk FX timestamp must be valid") from exc
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            if risk_fx_timestamp <= 0 or risk_fx_timestamp > now_ms:
                raise PortfolioError("Risk FX timestamp must be positive and non-future")
            if not isinstance(risk_fx_provider_id, str) or not risk_fx_provider_id.strip():
                raise PortfolioError("Risk FX timestamp requires provider provenance")
        if market_timestamp is not None:
            try:
                market_timestamp = int(market_timestamp)
            except (TypeError, ValueError, OverflowError) as exc:
                raise PortfolioError("Paper fills require a valid market timestamp") from exc
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            if market_timestamp <= 0 or market_timestamp > now_ms:
                raise PortfolioError("Paper fills require a positive non-future market timestamp")
        if quote_currency == "USD":
            if not isinstance(fx_provider_id, str) or not fx_provider_id.strip():
                raise PortfolioError("USD fills require an FX provider identity")
            try:
                fx_timestamp = int(fx_timestamp)
            except (TypeError, ValueError, OverflowError) as exc:
                raise PortfolioError("USD fills require a valid FX source timestamp") from exc
            if fx_timestamp <= 0:
                raise PortfolioError("USD fills require a valid FX source timestamp")
        if not 0 <= fee_rate < 1 or not 0 <= slippage_rate < 1:
            raise PortfolioError("Fee and slippage rates must be in [0, 1)")
        if not 0 < max_fraction <= 1:
            raise PortfolioError("Maximum allocation fraction must be in (0, 1]")
        if minimum < 0:
            raise PortfolioError("Minimum order value cannot be negative")
        if side == "buy":
            if stop_loss is not None and not 0 < stop_loss < price:
                raise PortfolioError("Buy stop-loss must be positive and below the reference price")
            if take_profit is not None and take_profit <= price:
                raise PortfolioError("Buy take-profit must be above the reference price")

        fill = price * (1 + slippage_rate if side == "buy" else 1 - slippage_rate)
        gross, fee = quantity * fill, quantity * fill * fee_rate
        total = gross + fee if side == "buy" else gross - fee
        total_eur = total * fx_rate_to_eur
        if not all(math.isfinite(value) and value > 0 for value in (fill, gross, total, total_eur)):
            raise PortfolioError("Calculated fill amounts must be positive finite numbers")
        if not math.isfinite(fee) or fee < 0:
            raise PortfolioError("Calculated execution fee must be finite and non-negative")
        if gross < minimum:
            raise PortfolioError(f"Minimum order value is {minimum:.2f} {quote_currency}")

        request_payload = self._normalize_order_request({
            "symbol": symbol, "side": side, "quantity": quantity, "price": price,
            "fee_rate": fee_rate, "slippage_rate": slippage_rate,
            "max_fraction": max_fraction, "minimum": minimum,
            "client_order_id": client_order_id, "provider_id": provider_id,
            "instrument_id": instrument_id, "strategy_id": strategy_id,
            "stop_loss": stop_loss, "take_profit": take_profit,
            "quote_currency": quote_currency, "fx_rate_to_eur": fx_rate_to_eur,
            "fx_provider_id": fx_provider_id, "fx_timestamp": fx_timestamp,
            "market_timestamp": market_timestamp,
            "risk_fx_rate_to_eur": risk_fx_rate_to_eur,
            "risk_fx_provider_id": risk_fx_provider_id,
            "risk_fx_timestamp": risk_fx_timestamp,
        })
        request_json = self._canonical_json(request_payload) if client_order_id else None

        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            intent_row = None
            if side == "buy":
                unresolved = db.execute(
                    "SELECT client_order_id,status FROM order_intents "
                    "WHERE status IN ('risk_approved','reconciliation_required') "
                    "AND client_order_id != ? LIMIT 1",
                    (client_order_id or "",),
                ).fetchone()
                if unresolved:
                    raise PortfolioError(
                        f"New entries are blocked by unresolved order intent {unresolved['client_order_id']}"
                    )
            if client_order_id:
                intent_row = db.execute(
                    "SELECT * FROM order_intents WHERE client_order_id=?",
                    (client_order_id,),
                ).fetchone()
                if intent_row:
                    if intent_row["request_json"] != request_json:
                        raise PortfolioError("client_order_id was already used for a different order intent")
                    if intent_row["status"] == "rejected":
                        raise PortfolioError("This order intent was already rejected")
                    if intent_row["status"] == "reconciliation_required":
                        raise PortfolioError("This order intent requires reconciliation before execution")
                    if intent_row["status"] == "expired":
                        raise PortfolioError("This order intent expired and cannot be executed")
                    if intent_row["status"] == "filled":
                        raise PortfolioError("Filled order intent has no matching trade; reconciliation is required")

                existing = db.execute(
                    "SELECT * FROM trades WHERE client_order_id=?",
                    (client_order_id,),
                ).fetchone()
                if existing:
                    prior = dict(existing)
                    same_intent = (
                        prior["symbol"] == symbol
                        and prior["side"] == side
                        and prior["provider_id"] == provider_id
                        and prior["instrument_id"] == instrument_id
                        and prior["quote_currency"] == quote_currency
                        and prior.get("strategy_id") == strategy_id
                        and math.isclose(float(prior["quantity"]), quantity, rel_tol=1e-12, abs_tol=1e-12)
                        and math.isclose(float(prior["price"]), fill, rel_tol=1e-12, abs_tol=1e-12)
                        and math.isclose(float(prior["fee"]), fee, rel_tol=1e-12, abs_tol=1e-12)
                        and math.isclose(float(prior["total"]), total, rel_tol=1e-12, abs_tol=1e-12)
                        and math.isclose(float(prior["fx_rate_to_eur"]), fx_rate_to_eur, rel_tol=1e-12, abs_tol=1e-12)
                        and prior.get("fx_provider_id") == fx_provider_id
                        and prior.get("fx_timestamp") == fx_timestamp
                        and prior.get("market_timestamp") == market_timestamp
                        and prior.get("stop_loss") == stop_loss
                        and prior.get("take_profit") == take_profit
                    )
                    if not same_intent:
                        raise PortfolioError("client_order_id was already used for a different paper order")
                    stamp = datetime.now(timezone.utc).isoformat()
                    if not intent_row:
                        db.execute(
                            "INSERT INTO order_intents(client_order_id,status,request_json,trade_id,fill_json,created_at,updated_at) "
                            "VALUES(?,'filled',?,?,?,?,?)",
                            (client_order_id, request_json, prior["id"], self._canonical_json(prior), stamp, stamp),
                        )
                        self._insert_order_intent_event(
                            db, client_order_id, None, "filled",
                            {"trade_id": prior["id"], "legacy_replay": True}, stamp,
                        )
                    else:
                        old_status = intent_row["status"]
                        db.execute(
                            "UPDATE order_intents SET status='filled',trade_id=?,fill_json=?,updated_at=? "
                            "WHERE client_order_id=?",
                            (prior["id"], self._canonical_json(prior), stamp, client_order_id),
                        )
                        if old_status != "filled":
                            self._insert_order_intent_event(
                                db, client_order_id, old_status, "filled",
                                {"trade_id": prior["id"], "reconciled_existing_trade": True}, stamp,
                            )
                    prior["mode"] = "paper"
                    prior["idempotent_replay"] = True
                    return prior

                if intent_row and intent_row["status"] == "filled":
                    raise PortfolioError("Filled order intent has no matching trade; reconciliation is required")
                if not intent_row:
                    stamp = datetime.now(timezone.utc).isoformat()
                    db.execute(
                        "INSERT INTO order_intents(client_order_id,status,request_json,created_at,updated_at) "
                        "VALUES(?,'risk_approved',?,?,?)",
                        (client_order_id, request_json, stamp, stamp),
                    )
                    self._insert_order_intent_event(
                        db, client_order_id, None, "risk_approved",
                        {"legacy_auto_approval": True}, stamp,
                    )

            db.execute(
                "INSERT OR IGNORE INTO currency_balances(currency,balance,updated_at) VALUES(?,0,?)",
                (quote_currency, datetime.now(timezone.utc).isoformat()),
            )
            balance_row = db.execute(
                "SELECT balance FROM currency_balances WHERE currency=?",
                (quote_currency,),
            ).fetchone()
            available_balance = float(balance_row["balance"])
            account = db.execute("SELECT * FROM account WHERE id=1").fetchone()
            position = db.execute(
                "SELECT * FROM positions WHERE provider_id=? AND instrument_id=?",
                (provider_id, instrument_id),
            ).fetchone()

            if position and str(position["quote_currency"] or "EUR").upper() != quote_currency:
                raise PortfolioError("Existing position quote currency does not match the requested instrument currency")

            if side == "buy":
                pending_withdrawals = 0.0
                if quote_currency == "EUR":
                    pending_withdrawals = float(db.execute(
                        "SELECT COALESCE(SUM(amount),0) FROM withdrawal_requests WHERE status='pending'"
                    ).fetchone()[0])
                spendable_cash = max(0.0, available_balance - pending_withdrawals)
                if total > spendable_cash:
                    if quote_currency == "EUR":
                        raise PortfolioError("Insufficient unreserved cash in EUR")
                    raise PortfolioError(f"Insufficient unreserved {quote_currency} cash")
                if gross > spendable_cash * max_fraction:
                    raise PortfolioError(
                        f"Order exceeds maximum allocation of unreserved cash in {quote_currency}"
                    )
                old_quantity = float(position["quantity"]) if position else 0.0
                old_cost_native = old_quantity * float(position["average_price"]) if position else 0.0
                old_cost_eur = old_quantity * float(position["average_price_eur"] or position["average_price"]) if position else 0.0
                new_quantity = old_quantity + quantity
                average_cost = (old_cost_native + gross + fee) / new_quantity
                average_cost_eur = (old_cost_eur + (gross + fee) * fx_rate_to_eur) / new_quantity
                effective_strategy = strategy_id or (position["strategy_id"] if position else None)
                effective_stop = stop_loss if stop_loss is not None else (
                    position["stop_loss"] if position else None
                )
                effective_target = take_profit if take_profit is not None else (
                    position["take_profit"] if position else None
                )
                stamp = datetime.now(timezone.utc).isoformat()
                db.execute(
                    "UPDATE currency_balances SET balance=balance-?,updated_at=? WHERE currency=?",
                    (total, stamp, quote_currency),
                )
                if quote_currency == "EUR":
                    db.execute("UPDATE account SET cash=cash-? WHERE id=1", (total,))
                db.execute(
                    "INSERT INTO positions(provider_id,instrument_id,symbol,quantity,average_price,strategy_id,stop_loss,take_profit,quote_currency,average_price_eur) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(provider_id,instrument_id) DO UPDATE SET "
                    "symbol=excluded.symbol, quantity=excluded.quantity, average_price=excluded.average_price, "
                    "strategy_id=excluded.strategy_id, stop_loss=excluded.stop_loss, "
                    "take_profit=excluded.take_profit, quote_currency=excluded.quote_currency, "
                    "average_price_eur=excluded.average_price_eur",
                    (provider_id, instrument_id, symbol, new_quantity, average_cost, effective_strategy, effective_stop, effective_target, quote_currency, average_cost_eur),
                )
            else:
                if not position or quote_currency != str(position["quote_currency"] or "EUR"):
                    raise PortfolioError("No matching position exists in the requested quote currency")
                if not position or quantity > position["quantity"] + 1e-12:
                    raise PortfolioError("Insufficient position to sell")
                average_cost_eur = float(position["average_price_eur"] or position["average_price"])
                realised_pnl_eur = (fill * fx_rate_to_eur - average_cost_eur) * quantity - fee * fx_rate_to_eur
                remaining = position["quantity"] - quantity
                stamp = datetime.now(timezone.utc).isoformat()
                db.execute(
                    "UPDATE currency_balances SET balance=balance+?,updated_at=? WHERE currency=?",
                    (total, stamp, quote_currency),
                )
                if quote_currency == "EUR":
                    db.execute(
                        "UPDATE account SET cash=cash+?, realised_pnl=realised_pnl+? WHERE id=1",
                        (total, realised_pnl_eur),
                    )
                else:
                    db.execute(
                        "UPDATE account SET realised_pnl=realised_pnl+? WHERE id=1",
                        (realised_pnl_eur,),
                    )
                if remaining <= 1e-12:
                    db.execute("DELETE FROM positions WHERE provider_id=? AND instrument_id=?", (provider_id, instrument_id))
                else:
                    db.execute("UPDATE positions SET quantity=? WHERE provider_id=? AND instrument_id=?", (remaining, provider_id, instrument_id))

            stamp = datetime.now(timezone.utc).isoformat()
            cursor = db.execute(
                "INSERT INTO trades(symbol,side,quantity,price,fee,total,created_at,client_order_id,provider_id,instrument_id,quote_currency,fx_rate_to_eur,total_eur,fx_provider_id,fx_timestamp,strategy_id,stop_loss,take_profit,market_timestamp) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (symbol, side, quantity, fill, fee, total, stamp, client_order_id, provider_id, instrument_id, quote_currency, fx_rate_to_eur, total_eur, fx_provider_id, fx_timestamp, strategy_id, stop_loss, take_profit, market_timestamp),
            )
            result = {
                "id": cursor.lastrowid,
                "symbol": symbol,
                "provider_id": provider_id,
                "instrument_id": instrument_id,
                "side": side,
                "quantity": quantity,
                "price": fill,
                "fee": fee,
                "total": total,
                "quote_currency": quote_currency,
                "fx_rate_to_eur": fx_rate_to_eur,
                "total_eur": total_eur,
                "fx_provider_id": fx_provider_id,
                "fx_timestamp": fx_timestamp,
                "market_timestamp": market_timestamp,
                "strategy_id": strategy_id,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "created_at": stamp,
                "client_order_id": client_order_id,
                "mode": "paper",
                "idempotent_replay": False,
            }
            if client_order_id:
                db.execute(
                    "UPDATE order_intents SET status='filled',trade_id=?,fill_json=?,rejection_reason=NULL,updated_at=? "
                    "WHERE client_order_id=? AND status='risk_approved'",
                    (cursor.lastrowid, self._canonical_json(result), stamp, client_order_id),
                )
                self._insert_order_intent_event(
                    db, client_order_id, "risk_approved", "filled",
                    {"trade_id": cursor.lastrowid, "fill": result}, stamp,
                )
            return result

    def convert_currency(
        self,
        from_currency: str,
        to_currency: str,
        amount: float,
        rate: float,
        fee_rate: float,
        client_conversion_id: str,
        fx_provider_id: str,
        fx_timestamp: int,
    ) -> dict:
        from_currency = str(from_currency).strip().upper()
        to_currency = str(to_currency).strip().upper()
        if {from_currency, to_currency} != {"EUR", "USD"}:
            raise PortfolioError("Only explicit EUR/USD paper conversions are supported")
        if not isinstance(client_conversion_id, str) or not client_conversion_id.strip():
            raise PortfolioError("A conversion idempotency key is required")
        if not isinstance(fx_provider_id, str) or not fx_provider_id.strip():
            raise PortfolioError("An FX provider identity is required")
        try:
            amount, rate, fee_rate = float(amount), float(rate), float(fee_rate)
            fx_timestamp = int(fx_timestamp)
        except (TypeError, ValueError, OverflowError) as exc:
            raise PortfolioError("Conversion values must be finite numbers") from exc
        if (
            not all(math.isfinite(value) for value in (amount, rate, fee_rate))
            or amount <= 0 or rate <= 0 or not 0 <= fee_rate < 1 or fx_timestamp <= 0
        ):
            raise PortfolioError("Conversion amount, rate, timestamp or fee is invalid")
        gross_target = amount * rate
        fee = gross_target * fee_rate
        fee_eur = fee / rate if to_currency == "USD" else fee
        net_target = gross_target - fee
        if not all(math.isfinite(value) for value in (gross_target, fee, fee_eur, net_target)):
            raise PortfolioError("Calculated conversion amounts must be finite")
        if gross_target <= 0 or net_target <= 0 or fee < 0 or fee_eur < 0:
            raise PortfolioError("Calculated conversion amounts must be positive after fees")
        stamp = datetime.now(timezone.utc).isoformat()
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM currency_conversions WHERE client_conversion_id=?",
                (client_conversion_id.strip(),),
            ).fetchone()
            if existing:
                prior = dict(existing)
                same_intent = (
                    prior["from_currency"] == from_currency
                    and prior["to_currency"] == to_currency
                    and math.isclose(float(prior["from_amount"]), amount, rel_tol=1e-12, abs_tol=1e-12)
                )
                if not same_intent:
                    raise PortfolioError("client_conversion_id was already used for a different conversion")
                prior["idempotent_replay"] = True
                return prior
            db.execute(
                "INSERT OR IGNORE INTO currency_balances(currency,balance,updated_at) VALUES(?,0,?)",
                (from_currency, stamp),
            )
            db.execute(
                "INSERT OR IGNORE INTO currency_balances(currency,balance,updated_at) VALUES(?,0,?)",
                (to_currency, stamp),
            )
            source = db.execute(
                "SELECT balance FROM currency_balances WHERE currency=?",
                (from_currency,),
            ).fetchone()
            if float(source["balance"]) + 1e-12 < amount:
                raise PortfolioError(f"Insufficient {from_currency} cash for conversion")
            db.execute(
                "UPDATE currency_balances SET balance=balance-?,updated_at=? WHERE currency=?",
                (amount, stamp, from_currency),
            )
            db.execute(
                "UPDATE currency_balances SET balance=balance+?,updated_at=? WHERE currency=?",
                (net_target, stamp, to_currency),
            )
            if from_currency == "EUR":
                db.execute("UPDATE account SET cash=cash-? WHERE id=1", (amount,))
            if to_currency == "EUR":
                db.execute("UPDATE account SET cash=cash+? WHERE id=1", (net_target,))
            db.execute("UPDATE account SET realised_pnl=realised_pnl-? WHERE id=1", (fee_eur,))
            cursor = db.execute(
                "INSERT INTO currency_conversions("
                "client_conversion_id,from_currency,to_currency,from_amount,gross_to_amount,"
                "fee,fee_eur,net_to_amount,rate,fx_provider_id,fx_timestamp,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    client_conversion_id.strip(), from_currency, to_currency, amount,
                    gross_target, fee, fee_eur, net_target, rate, fx_provider_id.strip(),
                    fx_timestamp, stamp,
                ),
            )
            return {
                "id": cursor.lastrowid,
                "client_conversion_id": client_conversion_id.strip(),
                "from_currency": from_currency,
                "to_currency": to_currency,
                "from_amount": amount,
                "gross_to_amount": gross_target,
                "fee": fee,
                "fee_eur": fee_eur,
                "net_to_amount": net_target,
                "rate": rate,
                "fx_provider_id": fx_provider_id.strip(),
                "fx_timestamp": fx_timestamp,
                "created_at": stamp,
                "idempotent_replay": False,
            }

    def get_conversion_by_client_conversion_id(self, client_conversion_id: str) -> dict | None:
        if not client_conversion_id:
            return None
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM currency_conversions WHERE client_conversion_id=?",
                (client_conversion_id,),
            ).fetchone()
        return dict(row) if row else None

    def conversion_history(self, limit: int = 50) -> list[dict]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM currency_conversions ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            ]

    def request_withdrawal(self, amount: float) -> dict:
        try:
            amount = float(amount)
        except (TypeError, ValueError) as exc:
            raise PortfolioError("Withdrawal amount must be a positive finite number") from exc
        if not math.isfinite(amount) or amount <= 0:
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
            non_reserved_component = amount - profit_component
            db.execute(
                "UPDATE account SET cash=cash-?, withdrawn_profit=withdrawn_profit+? WHERE id=1",
                (amount, profit_component),
            )
            db.execute(
                "UPDATE currency_balances SET balance=balance-?,updated_at=? WHERE currency='EUR'",
                (amount, stamp),
            )
            db.execute(
                "UPDATE withdrawal_requests SET status='approved', profit_component=?, "
                "non_reserved_component=?, decided_at=? WHERE id=?",
                (profit_component, non_reserved_component, stamp, request_id),
            )
            result = dict(request)
            result.update({
                "status": "approved",
                "profit_component": profit_component,
                "non_reserved_component": non_reserved_component,
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
