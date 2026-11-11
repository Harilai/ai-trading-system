from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import RLock
from typing import Callable, Iterable


class HistoricalDataError(ValueError):
    pass


class HistoricalDataConflict(HistoricalDataError):
    pass


def _decimal(value, field: str, *, positive: bool = False, non_negative: bool = False) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HistoricalDataError(f"Candle {field} must be a finite decimal") from exc
    if not number.is_finite():
        raise HistoricalDataError(f"Candle {field} must be a finite decimal")
    if positive and number <= 0:
        raise HistoricalDataError(f"Candle {field} must be positive")
    if non_negative and number < 0:
        raise HistoricalDataError(f"Candle {field} cannot be negative")
    return format(number.normalize(), "f")


def normalize_candle(row: Iterable, *, timestamp_unit: str = "ms") -> dict:
    if isinstance(row, dict):
        values = [row.get(key) for key in ("timestamp", "open", "high", "low", "close", "volume")]
    else:
        values = list(row)
        if len(values) < 6:
            raise HistoricalDataError("OHLCV row must contain six fields")
        values = values[:6]
    try:
        raw_timestamp = Decimal(str(values[0]))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HistoricalDataError("Candle timestamp must be an integer") from exc
    if isinstance(values[0], bool) or not raw_timestamp.is_finite() or raw_timestamp != raw_timestamp.to_integral_value():
        raise HistoricalDataError("Candle timestamp must be an integer")
    timestamp = int(raw_timestamp)
    if timestamp <= 0:
        raise HistoricalDataError("Candle timestamp must be positive")
    if timestamp_unit == "seconds":
        timestamp *= 1000
    elif timestamp_unit != "ms":
        raise HistoricalDataError("Unsupported candle timestamp unit")
    candle = {
        "timestamp": timestamp,
        "open": _decimal(values[1], "open", positive=True),
        "high": _decimal(values[2], "high", positive=True),
        "low": _decimal(values[3], "low", positive=True),
        "close": _decimal(values[4], "close", positive=True),
        "volume": _decimal(values[5], "volume", non_negative=True),
    }
    o, h, l, c = (Decimal(candle[key]) for key in ("open", "high", "low", "close"))
    if h < max(o, c, l) or l > min(o, c, h):
        raise HistoricalDataError("Invalid candle high/low range")
    return candle


class HistoricalCandleStore:
    """SQLite OHLCV store keyed by provider, instrument, timeframe and candle open time.

    Finalized candles are immutable. Provisional candles may be refreshed until
    finalized; conflicting finalized data is surfaced rather than silently rewritten.
    """

    def __init__(self, database_path: str):
        self.path = database_path
        self.lock = RLock()
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS historical_candles ("
                "provider_id TEXT NOT NULL, instrument_id TEXT NOT NULL, symbol TEXT NOT NULL, "
                "timeframe TEXT NOT NULL, timestamp_ms INTEGER NOT NULL, "
                "open TEXT NOT NULL, high TEXT NOT NULL, low TEXT NOT NULL, close TEXT NOT NULL, "
                "volume TEXT NOT NULL, is_final INTEGER NOT NULL CHECK(is_final IN (0,1)), "
                "source TEXT NOT NULL, retrieved_at TEXT NOT NULL, "
                "PRIMARY KEY(provider_id,instrument_id,timeframe,timestamp_ms))"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_historical_candles_range "
                "ON historical_candles(provider_id,instrument_id,timeframe,timestamp_ms)"
            )

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _identity(provider_id: str, instrument_id: str, symbol: str, timeframe: str) -> None:
        for name, value in (("provider_id", provider_id), ("instrument_id", instrument_id),
                            ("symbol", symbol), ("timeframe", timeframe)):
            if not isinstance(value, str) or not value.strip():
                raise HistoricalDataError(f"{name} is required")

    def upsert(
        self, provider_id: str, instrument_id: str, symbol: str, timeframe: str,
        candles: Iterable, *, finalized_before_ms: int, source: str = "provider",
    ) -> dict:
        self._identity(provider_id, instrument_id, symbol, timeframe)
        if isinstance(finalized_before_ms, bool) or not isinstance(finalized_before_ms, int) or finalized_before_ms <= 0:
            raise HistoricalDataError("finalized_before_ms must be a positive integer")
        if not isinstance(source, str) or not source.strip():
            raise HistoricalDataError("source is required")
        normalized = [normalize_candle(row) for row in candles]
        if any(b["timestamp"] <= a["timestamp"] for a, b in zip(normalized, normalized[1:])):
            raise HistoricalDataError("Candle timestamps must be strictly increasing")
        now = datetime.now(timezone.utc).isoformat()
        inserted = updated = unchanged = 0
        with self.lock, self.connect() as db:
            for candle in normalized:
                is_final = candle["timestamp"] < finalized_before_ms
                key = (provider_id, instrument_id, timeframe, candle["timestamp"])
                existing = db.execute(
                    "SELECT open,high,low,close,volume,is_final FROM historical_candles "
                    "WHERE provider_id=? AND instrument_id=? AND timeframe=? AND timestamp_ms=?", key
                ).fetchone()
                values = tuple(candle[k] for k in ("open", "high", "low", "close", "volume"))
                if existing is None:
                    db.execute(
                        "INSERT INTO historical_candles "
                        "(provider_id,instrument_id,symbol,timeframe,timestamp_ms,open,high,low,close,volume,is_final,source,retrieved_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (provider_id, instrument_id, symbol, timeframe, candle["timestamp"], *values,
                         int(is_final), source, now),
                    )
                    inserted += 1
                    continue
                old_values = tuple(existing[k] for k in ("open", "high", "low", "close", "volume"))
                if existing["is_final"]:
                    if old_values != values:
                        raise HistoricalDataConflict(
                            f"Conflicting finalized candle at {candle['timestamp']}"
                        )
                    # A stale or backfilled response must never demote a final candle.
                    unchanged += 1
                    continue
                if old_values == values and not is_final:
                    unchanged += 1
                    continue
                db.execute(
                    "UPDATE historical_candles SET symbol=?,open=?,high=?,low=?,close=?,volume=?,"
                    "is_final=?,source=?,retrieved_at=? WHERE provider_id=? AND instrument_id=? "
                    "AND timeframe=? AND timestamp_ms=?",
                    (symbol, *values, int(is_final), source, now, *key),
                )
                updated += 1
        return {"inserted": inserted, "updated": updated, "unchanged": unchanged, "received": len(normalized)}

    def get(
        self, provider_id: str, instrument_id: str, timeframe: str, *,
        since_ms: int | None = None, until_ms: int | None = None, limit: int = 5000,
    ) -> list[dict]:
        if limit < 1 or limit > 100_000:
            raise HistoricalDataError("limit must be between 1 and 100000")
        clauses = ["provider_id=?", "instrument_id=?", "timeframe=?"]
        params: list = [provider_id, instrument_id, timeframe]
        if since_ms is not None:
            clauses.append("timestamp_ms>=?")
            params.append(since_ms)
        if until_ms is not None:
            clauses.append("timestamp_ms<?")
            params.append(until_ms)
        params.append(limit)
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM historical_candles WHERE " + " AND ".join(clauses)
                + " ORDER BY timestamp_ms DESC LIMIT ?", params,
            ).fetchall()
        rows = list(reversed(rows))
        return [
            {**{key: row[key] for key in ("provider_id","instrument_id","symbol","timeframe",
              "timestamp_ms","open","high","low","close","volume","source","retrieved_at")},
             "is_final": bool(row["is_final"])}
            for row in rows
        ]

    def storage_stats(self) -> dict:
        with self.connect() as db:
            row = db.execute(
                "SELECT COUNT(*) AS bars, "
                "SUM(CASE WHEN is_final=0 THEN 1 ELSE 0 END) AS provisional_bars "
                "FROM historical_candles"
            ).fetchone()
            targets = db.execute(
                "SELECT COUNT(DISTINCT provider_id || '|' || instrument_id || '|' || timeframe) "
                "AS series FROM historical_candles"
            ).fetchone()["series"]
        size_bytes = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                size_bytes += Path(self.path + suffix).stat().st_size
            except OSError:
                continue
        return {
            "bars": row["bars"] or 0,
            "provisional_bars": row["provisional_bars"] or 0,
            "series": targets or 0,
            "database_size_bytes": size_bytes,
            "retention_policy": "retain_indefinitely",
            "automatic_pruning": False,
        }

    def coverage(self, provider_id: str, instrument_id: str, timeframe: str) -> dict:
        with self.connect() as db:
            row = db.execute(
                "SELECT COUNT(*) AS bars,MIN(timestamp_ms) AS first_ms,MAX(timestamp_ms) AS last_ms,"
                "SUM(CASE WHEN is_final=0 THEN 1 ELSE 0 END) AS provisional_bars "
                "FROM historical_candles WHERE provider_id=? AND instrument_id=? AND timeframe=?",
                (provider_id, instrument_id, timeframe),
            ).fetchone()
        return {
            "bars": row["bars"], "first_timestamp_ms": row["first_ms"],
            "last_timestamp_ms": row["last_ms"], "provisional_bars": row["provisional_bars"] or 0,
            "status": "available" if row["bars"] else "empty",
        }


class PaginatedCandleCollector:
    """Fetch historical OHLCV using provider-supported since/limit pagination."""

    def __init__(self, store: HistoricalCandleStore, *, page_limit: int = 500, max_pages: int = 100):
        if not 1 <= page_limit <= 1000 or not 1 <= max_pages <= 1000:
            raise ValueError("Invalid pagination bounds")
        self.store = store
        self.page_limit = page_limit
        self.max_pages = max_pages

    def collect(
        self, provider_id: str, instrument_id: str, symbol: str, timeframe: str,
        since_ms: int, until_ms: int, fetch_page: Callable,
        *, candle_interval_ms: int, finalized_before_ms: int, source: str = "provider",
    ) -> dict:
        for value, name in ((since_ms, "since_ms"), (until_ms, "until_ms"),
                            (candle_interval_ms, "candle_interval_ms")):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise HistoricalDataError(f"{name} must be a positive integer")
        if until_ms <= since_ms:
            raise HistoricalDataError("until_ms must be later than since_ms")
        cursor = since_ms
        total_received = 0
        pages = 0
        persisted = {"inserted": 0, "updated": 0, "unchanged": 0, "received": 0}
        while cursor < until_ms and pages < self.max_pages:
            rows = fetch_page(symbol, timeframe=timeframe, since=cursor, limit=self.page_limit)
            pages += 1
            if not rows:
                break
            provider_page = [normalize_candle(row) for row in rows]
            if any(b["timestamp"] <= a["timestamp"] for a, b in zip(provider_page, provider_page[1:])):
                raise HistoricalDataError("Provider returned duplicate or non-increasing candle timestamps")
            page = [row for row in provider_page if cursor <= row["timestamp"] < until_ms]
            if not page:
                if any(row["timestamp"] >= until_ms for row in provider_page):
                    cursor = until_ms
                break
            result = self.store.upsert(
                provider_id, instrument_id, symbol, timeframe, page,
                finalized_before_ms=finalized_before_ms, source=source,
            )
            for key in ("inserted", "updated", "unchanged", "received"):
                persisted[key] += result[key]
            total_received += len(page)
            next_cursor = page[-1]["timestamp"] + candle_interval_ms
            if next_cursor <= cursor:
                raise HistoricalDataError("Pagination cursor did not advance")
            cursor = next_cursor
            if len(rows) < self.page_limit:
                break
        return {
            "provider_id": provider_id, "instrument_id": instrument_id, "symbol": symbol,
            "timeframe": timeframe, "pages": pages, "received": total_received,
            **{key: persisted[key] for key in ("inserted","updated","unchanged")},
            "next_since_ms": cursor if cursor < until_ms else None,
            "complete": cursor >= until_ms,
            "coverage": self.store.coverage(provider_id, instrument_id, timeframe),
        }
