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

Balances and market discovery are owner-authenticated. The Demo adapter can now submit **limit orders only** through a separate durable lifecycle:
- `POST /api/demo/binance/spot/orders`: validates and persists an immutable intent before submission.
- `GET /api/demo/binance/spot/orders` and `GET /api/demo/binance/spot/orders/{client_order_id}`: inspect order state.
- `GET /api/demo/binance/spot/orders/{client_order_id}/events`: inspect append-only state history.
- `POST /api/demo/binance/spot/orders/{client_order_id}/reconcile`: query the exchange by client order ID or exchange order ID.
- `POST /api/demo/binance/spot/orders/{client_order_id}/cancel`: cancel an acknowledged open/partially-filled limit order.

Every endpoint requires `X-Agent-Token`. Orders require a unique client ID, exact decimal-string amount and price, an active Spot market, valid exchange precision/limits and an allowed quote asset (USDT, USDC, FDUSD, TUSD or EUR). The default per-order quote notional cap is 25 units; configuration is bounded to a maximum of 1000. Only one unresolved order is allowed at a time.

The lifecycle persists `intent_created` then `submission_unknown` before the network call. A timeout is never interpreted as a failed order: the API returns HTTP 202 and requires explicit reconciliation. Replays never resubmit an existing client order ID. Partial fills, fills, cancellation and rejected/unknown states are persisted with append-only audit events.

## Safety and remaining work

- Only Demo Trading credentials are accepted by this adapter configuration.
- The REST endpoint is checked at startup; a production endpoint causes a hard failure.
- The Demo connector supports limit-order submit/status/reconcile/cancel only; no market orders, transfers, withdrawals or wallet signing.
- Demo balances are virtual and are not mixed with the paper portfolio or real Binance balances.
- No live order path is implemented or enabled. The separate production Binance read-only connector remains read-only.

The Demo order lifecycle is now persisted and tested. The next phase must add restart/reconciliation drills against a real Demo account, verify actual Binance Demo API responses and fee/precision behaviour, and then generalize execution contracts to other venues. Live execution remains a separate future project with independent approvals and gates.
