from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
from uuid import uuid4


class AccountSnapshotError(RuntimeError):
    pass


class AccountSnapshotStore:
    """Append-only, multi-venue external account snapshots, separate from paper portfolio."""

    SUPPORTED_MARKET_TYPES = frozenset({
        "spot", "cash_equity", "margin", "futures", "options", "forex", "onchain",
    })

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
                CREATE TABLE IF NOT EXISTS account_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    account_alias TEXT NOT NULL,
                    market_type TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    previous_snapshot_id TEXT,
                    reconciliation_status TEXT NOT NULL,
                    change_count INTEGER NOT NULL,
                    balance_count INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    environment TEXT NOT NULL DEFAULT 'unspecified',
                    venue_type TEXT NOT NULL DEFAULT 'unknown',
                    read_only INTEGER NOT NULL DEFAULT 1,
                    execution_enabled INTEGER NOT NULL DEFAULT 0,
                    withdrawals_enabled INTEGER NOT NULL DEFAULT 0,
                    transfers_enabled INTEGER NOT NULL DEFAULT 0,
                    wallet_signing_enabled INTEGER NOT NULL DEFAULT 0,
                    permissions_verified INTEGER NOT NULL DEFAULT 0,
                    position_count INTEGER NOT NULL DEFAULT 0,
                    position_change_count INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(previous_snapshot_id) REFERENCES account_snapshots(snapshot_id)
                );
                CREATE INDEX IF NOT EXISTS idx_account_snapshots_account
                    ON account_snapshots(provider_id,account_alias,market_type,fetched_at DESC);
                CREATE TABLE IF NOT EXISTS account_snapshot_balances (
                    snapshot_id TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    free TEXT,
                    used TEXT,
                    total TEXT,
                    PRIMARY KEY(snapshot_id,asset),
                    FOREIGN KEY(snapshot_id) REFERENCES account_snapshots(snapshot_id) ON DELETE RESTRICT
                );
                CREATE TABLE IF NOT EXISTS account_snapshot_positions (
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
                    PRIMARY KEY(snapshot_id,instrument_id,side),
                    FOREIGN KEY(snapshot_id) REFERENCES account_snapshots(snapshot_id) ON DELETE RESTRICT
                );
                CREATE INDEX IF NOT EXISTS idx_account_snapshot_positions_instrument
                    ON account_snapshot_positions(instrument_id,snapshot_id);
                CREATE TRIGGER IF NOT EXISTS account_snapshot_positions_no_update
                    BEFORE UPDATE ON account_snapshot_positions
                    BEGIN SELECT RAISE(ABORT,'account snapshot positions are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS account_snapshot_positions_no_delete
                    BEFORE DELETE ON account_snapshot_positions
                    BEGIN SELECT RAISE(ABORT,'account snapshot positions are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS account_snapshots_no_update
                    BEFORE UPDATE ON account_snapshots
                    BEGIN SELECT RAISE(ABORT,'account snapshots are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS account_snapshots_no_delete
                    BEFORE DELETE ON account_snapshots
                    BEGIN SELECT RAISE(ABORT,'account snapshots are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS account_snapshot_balances_no_update
                    BEFORE UPDATE ON account_snapshot_balances
                    BEGIN SELECT RAISE(ABORT,'account snapshot balances are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS account_snapshot_balances_no_delete
                    BEFORE DELETE ON account_snapshot_balances
                    BEGIN SELECT RAISE(ABORT,'account snapshot balances are append-only'); END;
                """
            )
            existing_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(account_snapshots)").fetchall()
            }
            migrations = {
                "environment": "TEXT NOT NULL DEFAULT 'unspecified'",
                "venue_type": "TEXT NOT NULL DEFAULT 'unknown'",
                "read_only": "INTEGER NOT NULL DEFAULT 1",
                "execution_enabled": "INTEGER NOT NULL DEFAULT 0",
                "withdrawals_enabled": "INTEGER NOT NULL DEFAULT 0",
                "transfers_enabled": "INTEGER NOT NULL DEFAULT 0",
                "wallet_signing_enabled": "INTEGER NOT NULL DEFAULT 0",
                "permissions_verified": "INTEGER NOT NULL DEFAULT 0",
                "position_count": "INTEGER NOT NULL DEFAULT 0",
                "position_change_count": "INTEGER NOT NULL DEFAULT 0",
            }
            for column, definition in migrations.items():
                if column not in existing_columns:
                    db.execute(f"ALTER TABLE account_snapshots ADD COLUMN {column} {definition}")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_account_snapshots_account_environment "
                "ON account_snapshots(provider_id,account_alias,market_type,environment,fetched_at DESC)"
            )

    @staticmethod
    def _format_decimal(amount: Decimal) -> str:
        text = format(amount, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return "0" if text in ("", "-0") else text

    @classmethod
    def _decimal_text(cls, value: Any, *, asset: str, field: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise AccountSnapshotError(f"{field} balance for {asset} must be a decimal string")
        try:
            amount = Decimal(value)
        except (InvalidOperation, ValueError):
            raise AccountSnapshotError(f"{field} balance for {asset} is malformed") from None
        if not amount.is_finite() or amount < 0:
            raise AccountSnapshotError(f"{field} balance for {asset} is invalid")
        if len(amount.as_tuple().digits) > 80 or abs(amount.as_tuple().exponent) > 80:
            raise AccountSnapshotError(f"{field} balance for {asset} exceeds supported precision")
        return cls._format_decimal(amount)

    @staticmethod
    def _parse_time(value: Any, field: str) -> datetime:
        if not isinstance(value, str):
            raise AccountSnapshotError(f"{field} timestamp is required")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise AccountSnapshotError(f"{field} timestamp is invalid") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise AccountSnapshotError(f"{field} timestamp must include a timezone")
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _signed_decimal_text(cls, value: Any, *, field: str, allow_zero: bool = True) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise AccountSnapshotError(f"Position {field} must be a decimal string")
        try:
            amount = Decimal(value)
        except (InvalidOperation, ValueError):
            raise AccountSnapshotError(f"Position {field} is malformed") from None
        if not amount.is_finite() or (not allow_zero and amount == 0):
            raise AccountSnapshotError(f"Position {field} is invalid")
        if len(amount.as_tuple().digits) > 80 or abs(amount.as_tuple().exponent) > 80:
            raise AccountSnapshotError(f"Position {field} exceeds supported precision")
        return cls._format_decimal(amount)

    @classmethod
    def _normalize_position(cls, item: Any, *, snapshot_market_type: str) -> dict:
        if not isinstance(item, dict):
            raise AccountSnapshotError("Account snapshot contains a malformed position")
        instrument_id = item.get("instrument_id")
        symbol = item.get("symbol")
        asset_class = item.get("asset_class")
        market_type = item.get("market_type")
        side = item.get("side")
        quote_currency = item.get("quote_currency")
        if not isinstance(instrument_id, str) or not instrument_id.strip() or len(instrument_id) > 256:
            raise AccountSnapshotError("Position instrument_id is invalid")
        if not isinstance(symbol, str) or not symbol.strip() or len(symbol) > 128:
            raise AccountSnapshotError("Position symbol is invalid")
        if asset_class not in {"crypto", "forex", "equity", "etf", "index", "commodity", "future", "option", "defi", "tokenized"}:
            raise AccountSnapshotError("Position asset_class is unsupported")
        if market_type != snapshot_market_type:
            raise AccountSnapshotError("Position market_type must match its account snapshot")
        if side not in ("long", "short"):
            raise AccountSnapshotError("Position side must be long or short")
        if not isinstance(quote_currency, str) or not re.fullmatch(r"[A-Z0-9._-]{1,24}", quote_currency):
            raise AccountSnapshotError("Position quote_currency is invalid")
        quantity = cls._decimal_text(item.get("quantity"), asset=symbol, field="quantity")
        if quantity is None or Decimal(quantity) <= 0:
            raise AccountSnapshotError("Position quantity must be positive")
        average_entry_price = cls._decimal_text(
            item.get("average_entry_price"), asset=symbol, field="average_entry_price",
        )
        mark_price = cls._decimal_text(item.get("mark_price"), asset=symbol, field="mark_price")
        market_value = cls._decimal_text(item.get("market_value"), asset=symbol, field="market_value")
        unrealized_pnl = cls._signed_decimal_text(
            item.get("unrealized_pnl"), field="unrealized_pnl",
        )
        for field, value in (("average_entry_price", average_entry_price), ("mark_price", mark_price)):
            if value is not None and Decimal(value) <= 0:
                raise AccountSnapshotError(f"Position {field} must be positive")
        return {
            "instrument_id": instrument_id.strip(),
            "symbol": symbol.strip(),
            "asset_class": asset_class,
            "market_type": market_type,
            "quantity": quantity,
            "side": side,
            "quote_currency": quote_currency,
            "average_entry_price": average_entry_price,
            "mark_price": mark_price,
            "market_value": market_value,
            "unrealized_pnl": unrealized_pnl,
        }

    @classmethod
    def _normalize(cls, snapshot: dict) -> tuple[dict, list[dict], list[dict]]:
        if not isinstance(snapshot, dict):
            raise AccountSnapshotError("Account snapshot must be an object")
        provider_id = snapshot.get("provider_id")
        account_alias = snapshot.get("account_alias")
        market_type = snapshot.get("market_type")
        if not isinstance(provider_id, str) or not provider_id.strip() or len(provider_id) > 128:
            raise AccountSnapshotError("Account snapshot provider_id is invalid")
        if not isinstance(account_alias, str) or not account_alias.strip() or len(account_alias) > 128:
            raise AccountSnapshotError("Account snapshot account_alias is invalid")
        if market_type not in cls.SUPPORTED_MARKET_TYPES:
            raise AccountSnapshotError("Account snapshot market_type is unsupported")
        if snapshot.get("read_only") is not True:
            raise AccountSnapshotError("Account snapshot must originate from a read-only connector")

        permission_flags = {
            "execution_enabled": snapshot.get("execution_enabled"),
            "withdrawals_enabled": snapshot.get("withdrawals_enabled"),
            "transfers_enabled": snapshot.get("transfers_enabled", False),
            "wallet_signing_enabled": snapshot.get("wallet_signing_enabled", False),
            "permissions_verified": snapshot.get("permissions_verified", False),
        }
        if any(not isinstance(value, bool) for value in permission_flags.values()):
            raise AccountSnapshotError("Account snapshot permission flags must be booleans")
        if any(permission_flags[key] for key in (
            "execution_enabled", "withdrawals_enabled", "transfers_enabled", "wallet_signing_enabled",
        )):
            raise AccountSnapshotError("Account snapshot cannot include write, transfer, withdrawal or signing permissions")

        environment = snapshot.get("environment", "unspecified")
        venue_type = snapshot.get("venue_type", "unknown")
        if not isinstance(environment, str) or not environment.strip() or len(environment) > 32:
            raise AccountSnapshotError("Account snapshot environment is invalid")
        if not isinstance(venue_type, str) or not venue_type.strip() or len(venue_type) > 32:
            raise AccountSnapshotError("Account snapshot venue_type is invalid")

        fetched_at = cls._parse_time(snapshot.get("fetched_at"), "fetched_at")
        now = datetime.now(timezone.utc)
        age_seconds = (now - fetched_at).total_seconds()
        if age_seconds < -60:
            raise AccountSnapshotError("Account snapshot timestamp is in the future")
        if age_seconds > 300:
            raise AccountSnapshotError("Account snapshot timestamp is stale")

        raw_balances = snapshot.get("balances", [])
        if not isinstance(raw_balances, list):
            raise AccountSnapshotError("Account snapshot balances must be a list")
        balances = []
        seen_assets = set()
        for item in raw_balances:
            if not isinstance(item, dict):
                raise AccountSnapshotError("Account snapshot contains a malformed balance")
            asset = item.get("asset")
            if not isinstance(asset, str) or not re.fullmatch(r"[A-Z0-9._-]{1,24}", asset):
                raise AccountSnapshotError("Account snapshot contains an invalid asset code")
            if asset in seen_assets:
                raise AccountSnapshotError(f"Duplicate asset balance in snapshot: {asset}")
            seen_assets.add(asset)
            free = cls._decimal_text(item.get("free"), asset=asset, field="free")
            used = cls._decimal_text(item.get("used"), asset=asset, field="used")
            total = cls._decimal_text(item.get("total"), asset=asset, field="total")
            if total is None and free is not None and used is not None:
                with localcontext() as context:
                    context.prec = 100
                    total = cls._format_decimal(Decimal(free) + Decimal(used))
            if free is None and used is None and total is None:
                raise AccountSnapshotError(f"Balance for {asset} has no amount fields")
            balances.append({"asset": asset, "free": free, "used": used, "total": total})
        balances.sort(key=lambda item: item["asset"])

        raw_positions = snapshot.get("positions", [])
        if not isinstance(raw_positions, list):
            raise AccountSnapshotError("Account snapshot positions must be a list")
        positions = []
        seen_positions = set()
        for raw_position in raw_positions:
            position = cls._normalize_position(raw_position, snapshot_market_type=market_type)
            key = (position["instrument_id"], position["side"])
            if key in seen_positions:
                raise AccountSnapshotError("Duplicate instrument/side position in snapshot")
            seen_positions.add(key)
            positions.append(position)
        positions.sort(key=lambda item: (item["instrument_id"], item["side"]))

        normalized = {
            "provider_id": provider_id.strip(),
            "account_alias": account_alias.strip(),
            "market_type": market_type,
            "environment": environment.strip().lower(),
            "venue_type": venue_type.strip().lower(),
            "read_only": True,
            **permission_flags,
            "fetched_at": fetched_at.isoformat(),
            "balances": balances,
            "positions": positions,
        }
        return normalized, balances, positions

    @staticmethod
    def _diff(previous: dict[str, dict], current: dict[str, dict]) -> list[dict]:
        changes = []
        for asset in sorted(set(previous) | set(current)):
            before = previous.get(asset)
            after = current.get(asset)
            if before is None:
                changes.append({
                    "asset": asset,
                    "state": "added",
                    "previous_present": False,
                    "current_present": True,
                    "previous_total": None,
                    "current_total": after["total"],
                    "delta_total": None,
                })
                continue
            if after is None:
                changes.append({
                    "asset": asset,
                    "state": "absent_from_latest_snapshot",
                    "previous_present": True,
                    "current_present": False,
                    "previous_total": before["total"],
                    "current_total": None,
                    "delta_total": None,
                })
                continue

            changed = any(before.get(field) != after.get(field) for field in ("free", "used", "total"))
            previous_total = before.get("total")
            current_total = after.get("total")
            delta = None
            if previous_total is not None and current_total is not None:
                with localcontext() as context:
                    context.prec = 100
                    delta = AccountSnapshotStore._format_decimal(
                        Decimal(current_total) - Decimal(previous_total)
                    )
            changes.append({
                "asset": asset,
                "state": "changed" if changed else "unchanged",
                "previous_present": True,
                "current_present": True,
                "previous_total": previous_total,
                "current_total": current_total,
                "delta_total": delta,
            })
        return changes

    @staticmethod
    def _diff_positions(previous: dict[tuple[str, str], dict], current: dict[tuple[str, str], dict]) -> list[dict]:
        changes = []
        fields = (
            "symbol", "asset_class", "market_type", "quantity", "quote_currency",
            "average_entry_price", "mark_price", "market_value", "unrealized_pnl",
        )
        for key in sorted(set(previous) | set(current)):
            before = previous.get(key)
            after = current.get(key)
            instrument_id, side = key
            if before is None:
                changes.append({
                    "instrument_id": instrument_id, "side": side, "state": "added",
                    "previous_present": False, "current_present": True,
                    "previous": None, "current": after,
                })
            elif after is None:
                changes.append({
                    "instrument_id": instrument_id, "side": side, "state": "absent_from_latest_snapshot",
                    "previous_present": True, "current_present": False,
                    "previous": before, "current": None,
                })
            else:
                changed = any(before.get(field) != after.get(field) for field in fields)
                changes.append({
                    "instrument_id": instrument_id, "side": side,
                    "state": "changed" if changed else "unchanged",
                    "previous_present": True, "current_present": True,
                    "previous": before, "current": after,
                })
        return changes

    def record_snapshot(self, snapshot: dict) -> dict:
        normalized, balances, positions = self._normalize(snapshot)
        recorded_at = datetime.now(timezone.utc).isoformat()
        snapshot_id = str(uuid4())
        canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        provider_id = normalized["provider_id"]
        account_alias = normalized["account_alias"]
        market_type = normalized["market_type"]
        environment = normalized["environment"]
        venue_type = normalized["venue_type"]

        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous_row = db.execute(
                """
                SELECT snapshot_id,fetched_at FROM account_snapshots
                WHERE provider_id=? AND account_alias=? AND market_type=? AND environment=?
                ORDER BY fetched_at DESC, recorded_at DESC, snapshot_id DESC LIMIT 1
                """,
                (provider_id, account_alias, market_type, environment),
            ).fetchone()
            previous_id = previous_row["snapshot_id"] if previous_row else None
            if previous_row and normalized["fetched_at"] < previous_row["fetched_at"]:
                raise AccountSnapshotError(
                    "Account snapshot is older than the latest stored observation"
                )

            previous_balances = {}
            previous_positions = {}
            if previous_id:
                rows = db.execute(
                    "SELECT asset,free,used,total FROM account_snapshot_balances WHERE snapshot_id=?",
                    (previous_id,),
                ).fetchall()
                previous_balances = {row["asset"]: dict(row) for row in rows}
                position_rows = db.execute(
                    "SELECT instrument_id,symbol,asset_class,market_type,quantity,side,quote_currency,"
                    "average_entry_price,mark_price,market_value,unrealized_pnl "
                    "FROM account_snapshot_positions WHERE snapshot_id=?",
                    (previous_id,),
                ).fetchall()
                previous_positions = {
                    (row["instrument_id"], row["side"]): dict(row) for row in position_rows
                }

            current_balances = {item["asset"]: item for item in balances}
            current_positions = {
                (item["instrument_id"], item["side"]): item for item in positions
            }
            balance_changes = self._diff(previous_balances, current_balances) if previous_id else []
            position_changes = self._diff_positions(previous_positions, current_positions) if previous_id else []
            balance_change_count = sum(item["state"] != "unchanged" for item in balance_changes)
            position_change_count = sum(item["state"] != "unchanged" for item in position_changes)
            change_count = balance_change_count + position_change_count
            status = "initial" if previous_id is None else ("changed" if change_count else "unchanged")

            db.execute(
                """
                INSERT INTO account_snapshots(
                    snapshot_id,provider_id,account_alias,market_type,fetched_at,recorded_at,
                    previous_snapshot_id,reconciliation_status,change_count,balance_count,content_hash,
                    environment,venue_type,read_only,execution_enabled,withdrawals_enabled,
                    transfers_enabled,wallet_signing_enabled,permissions_verified,
                    position_count,position_change_count
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    snapshot_id, provider_id, account_alias, market_type,
                    normalized["fetched_at"], recorded_at, previous_id,
                    status, change_count, len(balances), content_hash,
                    environment, venue_type, 1, 0, 0, 0, 0,
                    int(normalized["permissions_verified"]), len(positions), position_change_count,
                ),
            )
            db.executemany(
                """
                INSERT INTO account_snapshot_balances(snapshot_id,asset,free,used,total)
                VALUES(?,?,?,?,?)
                """,
                [
                    (snapshot_id, item["asset"], item["free"], item["used"], item["total"])
                    for item in balances
                ],
            )
            db.executemany(
                """
                INSERT INTO account_snapshot_positions(
                    snapshot_id,instrument_id,symbol,asset_class,market_type,quantity,side,
                    quote_currency,average_entry_price,mark_price,market_value,unrealized_pnl
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

        return self.get_snapshot(snapshot_id)

    def list_snapshots(
        self,
        *,
        provider_id: str | None = None,
        account_alias: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        if not isinstance(limit, int) or not 1 <= limit <= 200:
            raise AccountSnapshotError("Account snapshot limit must be between 1 and 200")
        clauses = []
        params = []
        if provider_id:
            clauses.append("provider_id=?")
            params.append(provider_id)
        if account_alias:
            clauses.append("account_alias=?")
            params.append(account_alias)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self.connect() as db:
            rows = db.execute(
                f"SELECT snapshot_id,provider_id,account_alias,market_type,fetched_at,recorded_at,"
                f"previous_snapshot_id,reconciliation_status,change_count,balance_count,content_hash "
                f"FROM account_snapshots {where} "
                f"ORDER BY fetched_at DESC,recorded_at DESC,snapshot_id DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_snapshot(self, snapshot_id: str) -> dict | None:
        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            return None
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM account_snapshots WHERE snapshot_id=?",
                (snapshot_id.strip(),),
            ).fetchone()
            if row is None:
                return None
            current_rows = db.execute(
                "SELECT asset,free,used,total FROM account_snapshot_balances WHERE snapshot_id=? ORDER BY asset",
                (snapshot_id.strip(),),
            ).fetchall()
            current = {item["asset"]: dict(item) for item in current_rows}
            previous = {}
            previous_id = row["previous_snapshot_id"]
            if previous_id:
                previous_rows = db.execute(
                    "SELECT asset,free,used,total FROM account_snapshot_balances WHERE snapshot_id=? ORDER BY asset",
                    (previous_id,),
                ).fetchall()
                previous = {item["asset"]: dict(item) for item in previous_rows}

        result = dict(row)
        result["balances"] = list(current.values())
        result["changes"] = self._diff(previous, current) if previous_id else []
        return result
