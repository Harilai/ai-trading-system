# Persistent instrument catalogue design

## Purpose

Persist provider discovery as a searchable snapshot rather than forcing every consumer to rediscover markets. The catalogue records what each configured data provider reported, when it was last seen and which constraints/capabilities were actually supplied.

## Identity model

- Keep the existing `instrument_id = provider_id:market_type:symbol` unchanged because portfolio positions, trades and strategy history already persist it.
- Add a separate deterministic `catalogue_id` derived from provider ID, venue ID, asset class, market type, native symbol, base and quote.
- This distinguishes the same ticker listed on separate MICs/venues without rewriting historical portfolio keys.
- A later explicit migration can move order/position references to the canonical catalogue ID after collision analysis and compatibility planning.

## Persistence

- `instrument_catalog` stores normalized contract JSON plus queryable identity/classification/status columns.
- `first_seen_at` is immutable; `last_seen_at` advances only when that instrument is rediscovered.
- Instruments absent from a later discovery run are not deleted or automatically marked inactive: discovery may be partial or a provider may be unavailable.
- `instrument_discovery_runs` stores timestamps, counts, status and sanitized provider failures.
- Invalid/non-JSON metadata is rejected from the catalogue and recorded as a discovery failure.
- The catalogue shares the existing SQLite database file but owns its tables and indexes.

## API

- `GET /api/instruments`: persisted catalogue with filters for provider, asset class, market type and active status; supports bounded pagination.
- `GET /api/instruments/{catalogue_id}`: exact instrument snapshot and first/last-seen timestamps.
- `GET /api/instrument-discovery/runs`: recent discovery run summaries and sanitized failures.
- `POST /api/instruments/refresh`: owner-token-protected synchronous discovery refresh. It only reads provider market metadata; it never submits orders.

The legacy `GET /api/markets` remains live discovery for compatibility and also persists its snapshot.

## Safety

- Discovery is not account eligibility, authenticated trading access or execution permission.
- Missing precision, lot size, settlement or contract data stays null.
- Failed or partial provider discovery never deletes previous records.
- Provider errors are truncated and credential-like strings are redacted before persistence.
- No order, wallet, broker, DEX, withdrawal or live-trading capability is added by the catalogue.
