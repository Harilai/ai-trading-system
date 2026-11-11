# Historical market-data synchronization

## Scope and safety

The historical-data worker downloads **public OHLCV market data only**. It has no exchange API keys, private account connector, order client or execution-router dependency. It cannot place, cancel or reconcile orders.

The worker is opt-in in two independent ways:

1. The Compose service is in the `history-sync` profile and is not started by the default `docker compose up`.
2. `HISTORICAL_DATA_SYNC_ENABLED` defaults to `false`; an explicit target allowlist is also required.

The initial adapter supports only configured `ccxt:<exchange>` providers and active EUR-quoted Spot instruments returned by that provider's catalogue. Other provider families and asset classes remain unsupported until they expose a reviewed pagination contract.

## Configuration

Add to the server-side `.env` (never commit credentials):

```dotenv
HISTORICAL_DATA_SYNC_ENABLED=true
HISTORICAL_DATA_SYNC_TARGETS=ccxt:kraken,BTC/EUR,1h;ccxt:kraken,ETH/EUR,1h
HISTORICAL_DATA_SYNC_INTERVAL_SECONDS=3600
HISTORICAL_DATA_SYNC_RETRY_SECONDS=60
HISTORICAL_DATA_SYNC_LOOKBACK_DAYS=365
HISTORICAL_DATA_SYNC_PAGE_LIMIT=500
HISTORICAL_DATA_SYNC_MAX_PAGES_PER_TARGET=10
```

Each target is `provider_id,symbol,timeframe`; separate targets with semicolons. Only explicitly listed targets are synchronized. CCXT validates the timeframe and its exchange-specific rate limiter remains enabled. Requests run sequentially, with a bounded page count per target per cycle. A backfill in progress retries after the configured retry interval (60 seconds by default); once caught up, the normal sync interval applies. The durable cursor means a long initial backfill continues in later cycles instead of starting over. Calendar-month timeframes such as `1M` are rejected because their variable duration is incompatible with fixed-interval cursor arithmetic.

Start the isolated worker:

```bash
docker compose --profile history-sync up -d history-sync
docker compose logs -f history-sync
```

Stop it without affecting the API or execution worker:

```bash
docker compose stop history-sync
```

The worker shares the existing persistent SQLite volume but receives no `.env` file or broker credentials. Its container is non-root, read-only except for the mounted data volume, drops Linux capabilities and has no-new-privileges enabled.

## Cursor and candle lifecycle

- First run starts at the configured lookback horizon, aligned to the target candle interval.
- Cursor and per-target status are persisted in `historical_sync_state` in the trading database.
- A bounded cycle stores its next cursor. Restarting the container resumes from that cursor.
- The latest provisional candle is re-fetched until it becomes final. Finalized candles remain immutable; conflicting provider data is surfaced as an error.
- A provider returning no new candles is recorded as `waiting_for_data`, not treated as a successful backfill completion.
- Provider errors are recorded without exposing raw request details or credentials.

## Provider pagination and history limits

The scheduler requires an explicit provider capability contract before making OHLCV requests:

- Historical OHLCV support must be declared.
- The exact supported timeframe list must include the configured target.
- Pagination must declare the currently supported `since` cursor mode.
- Optional per-request and total-history limits are validated and applied before collection.
- Missing, mismatched or malformed capabilities fail closed; the scheduler does not infer pagination semantics from the presence of a CCXT method.

| Provider | Pagination | Maximum request | Maximum available history | Notes |
|---|---|---:|---:|---|
| Kraken Spot | `since` | 720 candles | 720 candles per interval | The public OHLC endpoint only exposes the latest 720 data points. Deeper history requires a separate Trades-to-OHLCV aggregation path, which is not implemented by this worker. |
| Other CCXT exchanges | Enabled only when explicitly declared | Provider-declared or unknown | Provider-declared or unknown | Do not infer an unlimited history window from a missing limit. |

When the requested lookback exceeds a declared provider history cap, the worker clamps the effective start timestamp and persists both requested and effective ranges, the applied limit and its status in `historical_sync_state`. This prevents repeated attempts to fetch data the provider cannot return and makes the reduced research coverage visible.

Kraken's 720-point limit is documented by [Kraken Support's Advanced API FAQ](https://support.kraken.com/en/articles/advanced-api-faq) and its [Spot OHLC endpoint reference](https://docs.kraken.com/api/docs/rest-api/get-ohlc-data). Do not apply this limit to other venues.

## Retention and storage

Raw OHLCV is retained **indefinitely** in this phase. Automatic candle pruning is deliberately disabled because Strategy Research and future audit/reproducibility workflows depend on historical samples. Storage diagnostics report bar count, provisional count, series count and SQLite database/WAL/SHM size. A future retention change must first introduce an archive/export and recovery policy; it must not silently delete research history.

## Research integration

Scout continues using the provider's recent sample for chart analysis. Strategy Research separately prefers the longer persisted series when it contains more finalized candles than the live sample. Provisional candles are excluded from backtests. If the store is empty, unavailable or not deeper than the live sample, research falls back to the provider's recent candles and records the source in its result.

## Status

The authenticated endpoint `GET /api/market-data/history/scheduler` reports whether the feature is enabled, configured target state, per-target cursors/results and storage usage. The endpoint uses the existing `X-Agent-Token` owner-control header.

The scheduler is a data-ingestion service, not a trading service. LIVE remains disabled.
