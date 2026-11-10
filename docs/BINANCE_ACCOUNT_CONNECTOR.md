# Binance Spot read-only account connector

## Purpose and scope

This connector is the first authenticated account integration. It reads the balances of one Binance Spot account through CCXT. It does not create orders, cancel orders, transfer assets, withdraw funds, sign wallet transactions, or access futures/margin accounts.

It is a read-only foundation for later reconciliation between real account state and the internal portfolio ledger. It does not connect the real balance to the paper portfolio and does not authorize any trading.

## Credential setup

1. Create a dedicated Binance API key for this application.
2. Grant read-only permissions only.
3. Ensure Spot trading, margin/futures trading, withdrawals and transfers are disabled. Do not enable IP restrictions bypasses; prefer allowlisting the VPS IP when supported.
4. Store the API key and secret only in the VPS `.env` file using `BINANCE_READONLY_API_KEY` and `BINANCE_READONLY_API_SECRET`.
5. Keep `.env` out of Git and restrict file permissions (for example, `chmod 600 .env`).
6. Never paste the key or secret into ChatGPT, GitHub issues, logs, the dashboard or screenshots. If exposed, revoke and rotate it.

The connector does not independently verify the key's exchange-side permission settings. The API response explicitly reports `account_key_permissions_verified=false`; the owner must verify permissions in Binance. The application code only calls CCXT's `fetch_balance()` method.

## API

Both endpoints require the owner token in `X-Agent-Token`:

- `GET /api/accounts`: reports connector configuration and declared read-only capabilities, never credentials.
- `GET /api/accounts/binance/spot/balances`: fetches non-zero free/used/total Spot balances and a UTC retrieval timestamp without persistence.
- `POST /api/accounts/binance/spot/snapshots`: captures and persists an immutable balance observation.
- `GET /api/accounts/snapshots` and `GET /api/accounts/snapshots/{snapshot_id}`: browse history and compare an observation with its predecessor.

If credentials are absent, balance retrieval returns HTTP 503. Provider failures are sanitized and do not expose raw CCXT exception text.

## Security boundaries

- Keys are held as Pydantic `SecretStr` settings and are not returned by any endpoint.
- The API does not expose a generic exchange client or arbitrary CCXT method invocation.
- The connector has no order, cancel, transfer, withdrawal or signing methods.
- Rate limiting is enabled and request timeout is bounded.
- Live balance responses are owner-authenticated. Explicit snapshots are persisted in separate append-only account tables and never mixed into the paper portfolio.
- Binance account-level permissions are not inferred from a successful balance call; API-key scope remains an operator responsibility.

Snapshot storage and comparison semantics are specified in `docs/ACCOUNT_SNAPSHOT_RECONCILIATION.md`. Changes between snapshots are observational only; absent assets are not assumed to be zero and no transfer or P&L is inferred.

## Not implemented

This is not live trading. No real order entry, order reconciliation, wallet integration, DEX, margin, futures, options or multi-account routing is enabled. Before any live execution, the system needs an independently reviewed order adapter, deterministic risk controls, sandbox/testnet validation, durable order lifecycle and reconciliation against exchange fills, kill switch, per-account limits and explicit owner enablement.
