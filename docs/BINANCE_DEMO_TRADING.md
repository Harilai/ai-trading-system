# Binance Spot Demo Trading connector

## Why Demo Trading, not the legacy Spot Testnet

The application uses CCXT's Binance Demo Trading mode via `enable_demo_trading(True)`. It does not use the legacy `set_sandbox_mode(True)` route for the old Spot Test Network. CCXT documents Demo Trading separately, and the keys are distinct from production credentials.

- CCXT Binance Demo Trading method: https://docs.ccxt.com/docs/exchanges/binance
- Binance Demo Trading API key management: https://demo.binance.com/en/my/settings/api-management
- CCXT announcement on the Demo Trading mode: https://t.me/s/ccxt_announcements/87

The connector enables Demo Trading immediately after constructing the exchange and before any other exchange method call. It then verifies the REST API host is `demo-api.binance.com` and rejects production `api.binance.com` endpoints. If the installed CCXT version cannot enable Demo Trading or endpoint verification fails, the connector refuses to initialize.

## Configuration

The connector is disabled by default. Set:

- `BINANCE_SPOT_DEMO_ENABLED=true`
- `BINANCE_SPOT_DEMO_API_KEY`
- `BINANCE_SPOT_DEMO_API_SECRET`

Generate the keys in Binance Demo Trading account settings. Never reuse production keys. Keep credentials only in the server-side `.env` file, with restrictive permissions; do not paste them into ChatGPT, GitHub or the dashboard.

## Current API surface

All endpoints require `X-Agent-Token`:

- `GET /api/demo/binance/spot/balances`: reads virtual Spot balances from the Demo Trading account.
- `GET /api/demo/binance/spot/markets`: discovers active Spot markets and exposes symbol, base/quote, precision and limits.

Both endpoints are owner-authenticated. They do not place orders. The connector's capability declaration explicitly sets order submission and management to false.

## Safety and remaining work

- Only Demo Trading credentials are accepted by this adapter configuration.
- The REST endpoint is checked at startup; a production endpoint causes a hard failure.
- The connector has no order submission, cancellation, transfer, withdrawal or wallet signing methods.
- Demo balances are virtual and are not mixed with the paper portfolio or real Binance balances.
- No live order path is implemented or enabled.

The next phase must add a separate, durable test order lifecycle: persisted intent before submission, stable client order IDs, timeout/unknown-state reconciliation, partial fills, cancel handling and restart recovery. Only after those tests pass should testnet order submission be exposed.
