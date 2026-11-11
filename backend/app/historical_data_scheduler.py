from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import sqlite3
import sys
import time
from pathlib import Path

from .config import settings
from .historical_data import HistoricalCandleStore, PaginatedCandleCollector
from .providers.ccxt_spot import CcxtSpotProvider


logger = logging.getLogger("historical-data-sync")
DAY_MS = 86_400_000


@dataclass(frozen=True)
class HistoricalSyncTarget:
    provider_id: str
    symbol: str
    timeframe: str


def parse_sync_targets(value: str) -> list[HistoricalSyncTarget]:
    targets = []
    seen = set()
    for raw in (value or "").split(";"):
        raw = raw.strip()
        if not raw:
            continue
        parts = [part.strip() for part in raw.split(",")]
        if len(parts) != 3 or not all(parts):
            raise ValueError(
                "Each history sync target must be provider_id,symbol,timeframe"
            )
        provider_id, symbol, timeframe = parts
        if not provider_id.startswith("ccxt:") or not provider_id[5:]:
            raise ValueError("Historical sync currently supports explicit ccxt:<exchange> targets only")
        key = (provider_id.lower(), symbol.upper(), timeframe.lower())
        if key in seen:
            raise ValueError("Duplicate historical sync target")
        seen.add(key)
        targets.append(HistoricalSyncTarget(provider_id, symbol, timeframe))
    return targets


class HistoricalSyncStateStore:
    """Durable per-target cursor and scheduler health state, sharing the trading DB."""

    def __init__(self, database_path: str):
        self.path = database_path
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS historical_sync_state ("
                "provider_id TEXT NOT NULL, instrument_id TEXT NOT NULL, symbol TEXT NOT NULL, "
                "timeframe TEXT NOT NULL, initial_since_ms INTEGER NOT NULL, next_since_ms INTEGER, "
                "last_started_at TEXT, last_finished_at TEXT, last_success_at TEXT, "
                "last_status TEXT NOT NULL DEFAULT 'pending', last_error TEXT, "
                "last_pages INTEGER NOT NULL DEFAULT 0, last_received INTEGER NOT NULL DEFAULT 0, "
                "last_inserted INTEGER NOT NULL DEFAULT 0, last_updated INTEGER NOT NULL DEFAULT 0, "
                "updated_at TEXT NOT NULL, "
                "PRIMARY KEY(provider_id,instrument_id,timeframe))"
            )

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def ensure_target(
        self, provider_id: str, instrument_id: str, symbol: str,
        timeframe: str, initial_since_ms: int,
    ) -> tuple[dict, bool]:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO historical_sync_state "
                "(provider_id,instrument_id,symbol,timeframe,initial_since_ms,next_since_ms,"
                "last_status,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (provider_id, instrument_id, symbol, timeframe, initial_since_ms,
                 initial_since_ms, "pending", now),
            )
            created = cursor.rowcount == 1
            row = db.execute(
                "SELECT * FROM historical_sync_state WHERE provider_id=? AND instrument_id=? AND timeframe=?",
                (provider_id, instrument_id, timeframe),
            ).fetchone()
        return dict(row), created

    def record_start(self, provider_id: str, instrument_id: str, timeframe: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute(
                "UPDATE historical_sync_state SET last_started_at=?,updated_at=? "
                "WHERE provider_id=? AND instrument_id=? AND timeframe=?",
                (now, now, provider_id, instrument_id, timeframe),
            )

    def record_result(
        self, provider_id: str, instrument_id: str, timeframe: str,
        result: dict, *, status: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        next_since = result.get("next_since_ms")
        with self.connect() as db:
            db.execute(
                "UPDATE historical_sync_state SET next_since_ms=?,last_finished_at=?,"
                "last_success_at=?,last_status=?,last_error=NULL,last_pages=?,last_received=?,"
                "last_inserted=?,last_updated=?,updated_at=? "
                "WHERE provider_id=? AND instrument_id=? AND timeframe=?",
                (next_since, now, now, status, int(result.get("pages", 0)),
                 int(result.get("received", 0)), int(result.get("inserted", 0)),
                 int(result.get("updated", 0)), now, provider_id, instrument_id, timeframe),
            )

    def record_error(
        self, provider_id: str, instrument_id: str, timeframe: str, error: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute(
                "UPDATE historical_sync_state SET last_finished_at=?,last_status='error',"
                "last_error=?,updated_at=? WHERE provider_id=? AND instrument_id=? AND timeframe=?",
                (now, error[:500], now, provider_id, instrument_id, timeframe),
            )

    def list_targets(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM historical_sync_state ORDER BY provider_id,instrument_id,timeframe"
            ).fetchall()
        return [dict(row) for row in rows]


def run_sync_cycle(
    targets: list[HistoricalSyncTarget],
    providers: dict,
    store: HistoricalCandleStore,
    state_store: HistoricalSyncStateStore,
    *,
    now_ms: int,
    lookback_days: int,
    page_limit: int = 500,
    max_pages_per_target: int = 10,
) -> list[dict]:
    if now_ms <= 0 or lookback_days < 1:
        raise ValueError("Invalid historical sync clock or lookback")
    collector = PaginatedCandleCollector(
        store, page_limit=page_limit, max_pages=max_pages_per_target,
    )
    outcomes = []
    for target in targets:
        instrument = None
        provider = providers.get(target.provider_id)
        if provider is None:
            outcomes.append({
                "provider_id": target.provider_id, "symbol": target.symbol,
                "timeframe": target.timeframe, "status": "provider_unavailable",
            })
            continue
        try:
            exchange = getattr(provider, "exchange", None)
            if exchange is None or not callable(getattr(exchange, "fetch_ohlcv", None)):
                raise ValueError("Provider does not expose CCXT OHLCV pagination")
            instrument = next(
                (item for item in provider.discover_markets()
                 if item.symbol == target.symbol and item.active),
                None,
            )
            if instrument is None:
                raise ValueError("Target is not an active instrument in provider catalogue")
            interval_seconds = exchange.parse_timeframe(target.timeframe)
            if not isinstance(interval_seconds, (int, float)) or interval_seconds <= 0:
                raise ValueError("Unsupported OHLCV timeframe")
            interval_ms = int(interval_seconds * 1000)
            if interval_ms <= 0:
                raise ValueError("OHLCV timeframe is below millisecond precision")
            initial_since_ms = max(1, now_ms - lookback_days * DAY_MS)
            initial_since_ms = (initial_since_ms // interval_ms) * interval_ms
            initial_since_ms = max(1, initial_since_ms)
            state, created = state_store.ensure_target(
                target.provider_id, instrument.instrument_id, target.symbol,
                target.timeframe, initial_since_ms,
            )
            latest = store.get(
                target.provider_id, instrument.instrument_id, target.timeframe, limit=1,
            )
            if created:
                cursor = initial_since_ms
            elif (
                latest and not latest[-1]["is_final"]
                and (
                    state["next_since_ms"] is None
                    or int(state["next_since_ms"]) > int(latest[-1]["timestamp_ms"])
                )
            ):
                # Re-fetch the last provisional candle only after the backfill
                # cursor has reached/passed it; do not skip older missing history.
                cursor = int(latest[-1]["timestamp_ms"])
            elif state["next_since_ms"] is not None:
                cursor = int(state["next_since_ms"])
            elif latest:
                cursor = int(latest[-1]["timestamp_ms"]) + interval_ms
            else:
                cursor = int(state["initial_since_ms"])
            if cursor >= now_ms:
                outcomes.append({
                    "provider_id": target.provider_id, "symbol": target.symbol,
                    "timeframe": target.timeframe, "status": "waiting_for_data",
                    "pages": 0, "received": 0,
                })
                continue
            state_store.record_start(
                target.provider_id, instrument.instrument_id, target.timeframe,
            )
            result = collector.collect(
                target.provider_id, instrument.instrument_id, target.symbol,
                target.timeframe, cursor, now_ms, exchange.fetch_ohlcv,
                candle_interval_ms=interval_ms,
                finalized_before_ms=max(1, now_ms - interval_ms),
                source="scheduled_ccxt_public_market_data",
            )
            if result["complete"]:
                status = "caught_up"
            elif result["received"] == 0:
                status = "waiting_for_data"
            else:
                status = "backfill_in_progress"
            state_store.record_result(
                target.provider_id, instrument.instrument_id, target.timeframe,
                result, status=status,
            )
            outcomes.append({**result, "status": status})
        except Exception as exc:
            # Persist a bounded diagnostic, not provider request/credential details.
            safe_error = f"{type(exc).__name__}: historical sync target failed"
            try:
                if instrument is not None:
                    state_store.record_error(
                        target.provider_id, instrument.instrument_id,
                        target.timeframe, safe_error,
                    )
            except Exception:
                logger.exception("Unable to persist history sync error state")
            logger.warning(
                "Historical sync failed for provider=%s symbol=%s timeframe=%s (%s)",
                target.provider_id, target.symbol, target.timeframe, type(exc).__name__,
            )
            outcomes.append({
                "provider_id": target.provider_id, "symbol": target.symbol,
                "timeframe": target.timeframe, "status": "error",
                "error": safe_error,
            })
    return outcomes


def run_forever() -> None:
    logging_targets = settings.historical_data_sync_targets
    if not settings.historical_data_sync_enabled:
        logger.warning("Historical data scheduler is disabled; no provider requests will be made")
        while True:
            time.sleep(60)
    try:
        targets = parse_sync_targets(logging_targets)
        if not targets:
            raise ValueError("No explicit historical sync targets are configured")
        providers = {}
        for target in targets:
            exchange_id = target.provider_id.removeprefix("ccxt:")
            providers[target.provider_id] = CcxtSpotProvider(exchange_id)
    except Exception as exc:
        logger.error("Historical data scheduler configuration is invalid (%s)", type(exc).__name__)
        while True:
            time.sleep(60)

    store = HistoricalCandleStore(settings.database_path)
    state_store = HistoricalSyncStateStore(settings.database_path)
    logger.info(
        "Starting historical sync: targets=%s interval=%ss lookback=%sd page_limit=%s max_pages=%s retention=indefinite",
        len(targets), settings.historical_data_sync_interval_seconds,
        settings.historical_data_sync_lookback_days, settings.historical_data_sync_page_limit,
        settings.historical_data_sync_max_pages_per_target,
    )
    while True:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        outcomes = run_sync_cycle(
            targets, providers, store, state_store,
            now_ms=now_ms,
            lookback_days=settings.historical_data_sync_lookback_days,
            page_limit=settings.historical_data_sync_page_limit,
            max_pages_per_target=settings.historical_data_sync_max_pages_per_target,
        )
        for outcome in outcomes:
            logger.info(
                "Historical sync target status=%s provider=%s symbol=%s timeframe=%s pages=%s received=%s",
                outcome.get("status"), outcome.get("provider_id"), outcome.get("symbol"),
                outcome.get("timeframe"), outcome.get("pages", 0), outcome.get("received", 0),
            )
        time.sleep(settings.historical_data_sync_interval_seconds)


def main() -> int:
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if "--healthcheck" in sys.argv:
        store = HistoricalSyncStateStore(settings.database_path)
        return 0 if store.list_targets() is not None else 1
    run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
