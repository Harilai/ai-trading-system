# Binance Spot Demo operational smoke-test runbook

## Purpose and boundary

Use this runbook only with a **Binance Spot Demo Trading account** and virtual funds. It verifies the Demo connector after PR #63 using the actual Binance Demo API. It does not enable production orders, market orders, transfers, withdrawals or wallet signing.

The API process must use Demo credentials created for the Demo environment. Never put production API keys, seed phrases or private keys in this configuration. Do not send credentials or the owner token in chat, GitHub issues, screenshots or logs.

## 1. Configure the server

On the host, edit the untracked server-side `.env` file:

```dotenv
BINANCE_SPOT_DEMO_ENABLED=true
BINANCE_SPOT_DEMO_API_KEY=<BINANCE_DEMO_API_KEY>
BINANCE_SPOT_DEMO_API_SECRET=<BINANCE_DEMO_API_SECRET>
BINANCE_SPOT_DEMO_MAX_ORDER_QUOTE=25
AGENT_CONTROL_TOKEN=<LONG_RANDOM_OWNER_TOKEN>
```

- Keep the Demo key separate from the production read-only key.
- Do not grant transfer or withdrawal permissions. The Demo adapter does not expose those operations.
- Do not commit `.env`; it is already ignored.
- Start/restart the API:

```bash
docker compose up --build -d api
docker compose logs --tail=100 api
```

The connector must initialize against `demo-api.binance.com`. A production REST endpoint is a hard configuration failure. Do not work around that check.

## 2. Authenticate without persisting the token

In a private shell on the server:

```bash
read -rsp "Owner token: " AGENT_CONTROL_TOKEN
export AGENT_CONTROL_TOKEN
echo
API=http://127.0.0.1:8000
```

All calls below use the owner token in the `X-Agent-Token` header. Do not paste the token into shell history or save it in a browser.

## 3. Read-only preflight

First confirm the account capability and environment:

```bash
curl -fsS -H "X-Agent-Token: $AGENT_CONTROL_TOKEN" "$API/api/accounts"
curl -fsS -H "X-Agent-Token: $AGENT_CONTROL_TOKEN" "$API/api/demo/binance/spot/balances"
curl -fsS -H "X-Agent-Token: $AGENT_CONTROL_TOKEN" "$API/api/demo/binance/spot/markets"
```

Acceptance:
- The Demo connector is configured and reports Demo mode.
- The Demo balances are virtual and remain separate from the production read-only account and the local paper portfolio.
- Market discovery returns the expected active Spot symbols and their precision/limits.
- Order submission is enabled only on the Demo connector. The production Binance connector remains read-only.
- If credentials, host verification, balances or market metadata fail, stop here; do not submit an order.

## 4. Prepare a small non-marketable limit order

Choose a symbol from the Demo market catalogue whose quote asset is one of USDT, USDC, FDUSD, TUSD or EUR. Check its current bid/ask and market precision in the Binance Demo interface.

For the first test:
- Use a notional comfortably below the default 25 quote-unit cap.
- Respect minimum amount/cost and amount/price precision.
- Use a non-marketable limit price (for example, a buy limit below the current best bid) so the order should remain open rather than fill immediately.
- Generate a new client order ID of 1–36 letters, digits, underscores or hyphens.
- Use JSON strings for amount and price. Do not send JSON numbers or exponent notation.

Example shape only; replace every placeholder with values verified for the selected Demo market:

```bash
curl -i -X POST "$API/api/demo/binance/spot/orders" \
  -H "Content-Type: application/json" \
  -H "X-Agent-Token: $AGENT_CONTROL_TOKEN" \
  --data '{
    "symbol": "<ACTIVE_SPOT_SYMBOL>",
    "side": "buy",
    "amount": "<EXACT_AMOUNT_DECIMAL_STRING>",
    "price": "<EXACT_LIMIT_PRICE_DECIMAL_STRING>",
    "client_order_id": "demo-smoke-unique-001"
  }'
```

Expected result: HTTP 201 with a persisted intent and an exchange order ID/status, or HTTP 202 if the submission outcome is ambiguous. A 202 is **not** permission to submit again with a new client ID.

## 5. Verify idempotency, status, audit and cancellation

Use the same client ID from the request:

```bash
curl -fsS -H "X-Agent-Token: $AGENT_CONTROL_TOKEN" "$API/api/demo/binance/spot/orders/demo-smoke-unique-001"
curl -fsS -H "X-Agent-Token: $AGENT_CONTROL_TOKEN" "$API/api/demo/binance/spot/orders/demo-smoke-unique-001/events"
```

Repeat the exact same POST once with the same client ID and identical payload. It must return the existing order and must not create a second exchange order. Reusing the client ID with a different symbol, side, amount, price or quote cap must be rejected.

If the order is open, cancel it:

```bash
curl -i -X POST -H "X-Agent-Token: $AGENT_CONTROL_TOKEN" \
  "$API/api/demo/binance/spot/orders/demo-smoke-unique-001/cancel"
```

Then read the order and events again. Confirm the exchange ID is stable and that filled/remaining quantities are consistent with the original amount.

## 6. Verify restart and ambiguous-outcome recovery

For a resting Demo order, restart only the API container (do not delete the data volume):

```bash
docker compose restart api
```

After readiness returns, reconcile the existing client order ID:

```bash
curl -i -X POST -H "X-Agent-Token: $AGENT_CONTROL_TOKEN" \
  "$API/api/demo/binance/spot/orders/demo-smoke-unique-001/reconcile"
```

Acceptance:
- The persisted intent and audit events survive restart.
- Reconciliation queries the existing order by exchange ID or client order ID.
- No restart, retry or repeated POST causes blind resubmission.
- If an acknowledged order cannot be found, or exchange identity/quantity data conflicts, the state becomes `reconciliation_required` and blocks subsequent Demo orders.
- Resolve any remaining open order in the Binance Demo interface and reconcile/cancel it before ending the drill.

## 7. Partial fills and fee evidence

A partial fill is market-dependent and cannot be guaranteed by a deterministic smoke test. If one occurs, verify that:
- Cumulative filled quantity increases monotonically.
- Remaining quantity decreases and filled + remaining equals the original amount.
- The order cannot be marked filled without complete evidence for the original amount.
- A contradictory or regressing exchange snapshot is quarantined and preserves the last known-good quantities.

**Known scope limit:** PR #63 stores cumulative filled/remaining quantity and average price; it does not yet persist each individual exchange trade, commission amount or commission asset as a fill ledger. Do not use this Demo order store as the accounting source of truth. Individual trade/fee reconciliation is a required follow-up before any live execution.

## 8. Close the drill

- Confirm no Demo order remains open or unresolved.
- Record the test date, symbol, client order ID, exchange order ID, status transitions, fills and observed errors in the operational log without recording credentials.
- Unset the owner token in the shell: `unset AGENT_CONTROL_TOKEN`.
- Disable the Demo connector when not in use by setting `BINANCE_SPOT_DEMO_ENABLED=false` and restarting the API.
- Never switch this connector to production by changing its URL or reusing production credentials.

## Stop conditions

Stop immediately if the account reports production mode, the endpoint is not the Demo host, the quote cap/precision is unclear, balances are inconsistent, an order ID/client ID/symbol/side does not match, fill quantities regress, or reconciliation is uncertain. Do not send another order to "see if it works".
