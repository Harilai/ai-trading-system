from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Iterable
from uuid import uuid4

from .providers.base import AssetClass, MarketInstrument, MarketType


class InstrumentCatalogError(RuntimeError):
    pass


class InstrumentCatalog:
    """Persistent discovery catalogue with a venue-aware key and legacy ID preservation."""

    def __init__(self, database_path: str):
        self.database_path = str(Path(database_path).expanduser().resolve())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
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
                CREATE TABLE IF NOT EXISTS instrument_catalog (
                    catalogue_id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    venue_id TEXT NOT NULL DEFAULT '',
                    asset_class TEXT NOT NULL,
                    market_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    native_symbol TEXT NOT NULL,
                    base TEXT NOT NULL,
                    quote TEXT NOT NULL,
                    legacy_instrument_id TEXT NOT NULL,
                    active INTEGER NOT NULL,
                    trading_status TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(provider_id, venue_id, asset_class, market_type, native_symbol, base, quote)
                );
                CREATE INDEX IF NOT EXISTS idx_instrument_catalog_provider
                    ON instrument_catalog(provider_id, venue_id);
                CREATE INDEX IF NOT EXISTS idx_instrument_catalog_class
                    ON instrument_catalog(asset_class, market_type, active);
                CREATE INDEX IF NOT EXISTS idx_instrument_catalog_seen
                    ON instrument_catalog(last_seen_at DESC);

                CREATE TABLE IF NOT EXISTS instrument_discovery_runs (
                    run_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    discovered_count INTEGER NOT NULL,
                    inserted_count INTEGER NOT NULL,
                    updated_count INTEGER NOT NULL,
                    failure_count INTEGER NOT NULL,
                    failures_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_instrument_discovery_runs_started
                    ON instrument_discovery_runs(started_at DESC);
                """
            )

    @staticmethod
    def catalogue_id(instrument: MarketInstrument) -> str:
        identity = [
            instrument.provider_id,
            instrument.venue_id or "",
            instrument.asset_class.value,
            instrument.market_type.value,
            instrument.native_symbol or instrument.symbol,
            instrument.base,
            instrument.quote,
        ]
        material = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
        return "inst_" + hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _sanitize_metadata(value):
        sensitive_markers = (
            "apikey", "secret", "privatekey", "authorization", "password",
            "seedphrase", "accesstoken", "refreshtoken",
        )
        if isinstance(value, dict):
            sanitized = {}
            for key, item in value.items():
                normalized_key = str(key).strip().lower().replace("-", "_").replace(" ", "").replace("_", "")
                if any(marker in normalized_key for marker in sensitive_markers):
                    sanitized[str(key)] = "[REDACTED]"
                else:
                    sanitized[str(key)] = InstrumentCatalog._sanitize_metadata(item)
            return sanitized
        if isinstance(value, (list, tuple)):
            return [InstrumentCatalog._sanitize_metadata(item) for item in value]
        return value

    @staticmethod
    def _serialize(instrument: MarketInstrument) -> dict:
        if not isinstance(instrument.metadata, dict):
            raise ValueError("Instrument metadata must be a mapping")
        return {
            "instrument_id": instrument.instrument_id,
            "provider_id": instrument.provider_id,
            "venue_id": instrument.venue_id,
            "symbol": instrument.symbol,
            "native_symbol": instrument.native_symbol,
            "asset_class": instrument.asset_class.value,
            "market_type": instrument.market_type.value,
            "base": instrument.base,
            "quote": instrument.quote,
            "active": instrument.active,
            "trading_status": instrument.trading_status,
            "minimum_amount": instrument.min_amount,
            "maximum_amount": instrument.max_amount,
            "minimum_cost": instrument.min_cost,
            "maximum_cost": instrument.max_cost,
            "maker_fee": instrument.maker_fee,
            "taker_fee": instrument.taker_fee,
            "precision_mode": instrument.precision_mode.value,
            "price_precision": instrument.price_precision,
            "amount_precision": instrument.amount_precision,
            "price_tick": instrument.price_tick,
            "amount_step": instrument.amount_step,
            "contract_type": instrument.contract_type,
            "contract_size": instrument.contract_size,
            "settlement_currency": instrument.settlement_currency,
            "expiry": instrument.expiry,
            "strike": instrument.strike,
            "option_type": instrument.option_type,
            "underlying": instrument.underlying,
            "session_timezone": instrument.session_timezone,
            "metadata": InstrumentCatalog._sanitize_metadata(instrument.metadata),
        }

    @staticmethod
    def _safe_failure(failure: dict) -> dict:
        if not isinstance(failure, dict):
            return {"provider_id": "unknown", "reason": "Malformed provider failure record"}
        provider_id = str(failure.get("provider_id") or "unknown")[:128]
        reason = str(failure.get("reason") or "provider discovery failed")
        reason = re.sub(
            r"(?i)authorization\s*[:=]\s*[^\s&,;]+(?:\s+[^\s&,;]+)?",
            "Authorization=[REDACTED]",
            reason,
        )
        reason = re.sub(
            r"(?i)(api[_-]?key|secret|token|private[_-]?key|password|seed[_-]?phrase)\s*[:=]\s*[^\s&,;]+",
            r"\1=[REDACTED]",
            reason,
        )
        return {"provider_id": provider_id, "reason": reason.replace("\n", " ")[:500]}

    def record_discovery(
        self,
        instruments: Iterable[MarketInstrument],
        failures: Iterable[dict] = (),
        *,
        discovered_at: datetime | None = None,
        started_at: datetime | None = None,
    ) -> dict:
        completed_dt = discovered_at or datetime.now(timezone.utc)
        started_dt = started_at or completed_dt
        if (
            completed_dt.tzinfo is None or completed_dt.utcoffset() is None
            or started_dt.tzinfo is None or started_dt.utcoffset() is None
        ):
            raise InstrumentCatalogError("Discovery timestamps must be timezone-aware")
        if started_dt > completed_dt:
            raise InstrumentCatalogError("Discovery start time cannot be after completion time")
        stamp = completed_dt.astimezone(timezone.utc).isoformat()
        started = started_dt.astimezone(timezone.utc).isoformat()
        run_id = str(uuid4())
        safe_failures = [self._safe_failure(item) for item in failures]
        prepared = []
        for instrument in instruments:
            if not isinstance(instrument, MarketInstrument):
                safe_failures.append({
                    "provider_id": "unknown",
                    "reason": "Discovery result was not a MarketInstrument record",
                })
                continue
            try:
                payload = self._serialize(instrument)
                payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                prepared.append((self.catalogue_id(instrument), instrument, payload_json))
            except (TypeError, ValueError, OverflowError) as exc:
                safe_failures.append({
                    "provider_id": instrument.provider_id,
                    "reason": f"Instrument metadata could not be serialized ({type(exc).__name__})",
                })

        inserted = 0
        updated = 0
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for catalogue_id, instrument, payload_json in prepared:
                existing = db.execute(
                    "SELECT first_seen_at FROM instrument_catalog WHERE catalogue_id=?",
                    (catalogue_id,),
                ).fetchone()
                first_seen = existing["first_seen_at"] if existing else stamp
                db.execute(
                    """
                    INSERT INTO instrument_catalog(
                        catalogue_id,provider_id,venue_id,asset_class,market_type,
                        symbol,native_symbol,base,quote,legacy_instrument_id,
                        active,trading_status,first_seen_at,last_seen_at,payload_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(catalogue_id) DO UPDATE SET
                        symbol=excluded.symbol,
                        native_symbol=excluded.native_symbol,
                        base=excluded.base,
                        quote=excluded.quote,
                        legacy_instrument_id=excluded.legacy_instrument_id,
                        active=excluded.active,
                        trading_status=excluded.trading_status,
                        last_seen_at=excluded.last_seen_at,
                        payload_json=excluded.payload_json
                    """,
                    (
                        catalogue_id,
                        instrument.provider_id,
                        instrument.venue_id or "",
                        instrument.asset_class.value,
                        instrument.market_type.value,
                        instrument.symbol,
                        instrument.native_symbol or instrument.symbol,
                        instrument.base,
                        instrument.quote,
                        instrument.instrument_id,
                        int(instrument.active),
                        instrument.trading_status or "unknown",
                        first_seen,
                        stamp,
                        payload_json,
                    ),
                )
                if existing:
                    updated += 1
                else:
                    inserted += 1

            status = "failed" if safe_failures and not prepared else ("partial" if safe_failures else "success")
            db.execute(
                """
                INSERT INTO instrument_discovery_runs(
                    run_id,started_at,completed_at,status,discovered_count,
                    inserted_count,updated_count,failure_count,failures_json
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id, started, stamp, status, len(prepared), inserted, updated,
                    len(safe_failures),
                    json.dumps(safe_failures, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                ),
            )

        return {
            "run_id": run_id,
            "status": status,
            "discovered_count": len(prepared),
            "inserted_count": inserted,
            "updated_count": updated,
            "failure_count": len(safe_failures),
            "failures": safe_failures,
        }

    def list_instruments(
        self,
        *,
        provider_id: str | None = None,
        asset_class: str | None = None,
        market_type: str | None = None,
        venue_id: str | None = None,
        active: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        if not isinstance(limit, int) or not 1 <= limit <= 500:
            raise InstrumentCatalogError("Instrument catalogue limit must be between 1 and 500")
        if not isinstance(offset, int) or offset < 0:
            raise InstrumentCatalogError("Instrument catalogue offset must be non-negative")
        clauses = []
        params: list = []
        if provider_id:
            clauses.append("provider_id=?")
            params.append(provider_id)
        if asset_class:
            try:
                normalized = AssetClass(asset_class).value
            except ValueError as exc:
                raise InstrumentCatalogError("Unknown asset class filter") from exc
            clauses.append("asset_class=?")
            params.append(normalized)
        if market_type:
            try:
                normalized = MarketType(market_type).value
            except ValueError as exc:
                raise InstrumentCatalogError("Unknown market type filter") from exc
            clauses.append("market_type=?")
            params.append(normalized)
        if venue_id:
            clauses.append("venue_id=?")
            params.append(venue_id)
        if active is not None:
            clauses.append("active=?")
            params.append(int(active))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend((limit, offset))
        with self.connect() as db:
            rows = db.execute(
                f"SELECT catalogue_id,first_seen_at,last_seen_at,payload_json "
                f"FROM instrument_catalog {where} ORDER BY last_seen_at DESC,catalogue_id LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        result = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            payload.update({
                "catalogue_id": row["catalogue_id"],
                "first_seen_at": row["first_seen_at"],
                "last_seen_at": row["last_seen_at"],
            })
            result.append(payload)
        return result

    def get_instrument(self, catalogue_id: str) -> dict | None:
        if not isinstance(catalogue_id, str) or not catalogue_id.strip():
            return None
        with self.connect() as db:
            row = db.execute(
                "SELECT catalogue_id,first_seen_at,last_seen_at,payload_json "
                "FROM instrument_catalog WHERE catalogue_id=?",
                (catalogue_id.strip(),),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        payload.update({
            "catalogue_id": row["catalogue_id"],
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
        })
        return payload

    def discovery_runs(self, limit: int = 50) -> list[dict]:
        if not isinstance(limit, int) or not 1 <= limit <= 200:
            raise InstrumentCatalogError("Discovery history limit must be between 1 and 200")
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM instrument_discovery_runs ORDER BY started_at DESC,run_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["failures"] = json.loads(item.pop("failures_json"))
            result.append(item)
        return result
