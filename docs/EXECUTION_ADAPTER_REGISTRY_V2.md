# Execution adapter registry v2

## Purpose

Provide one explicit source of truth for execution readiness scoped to provider, venue, account, environment, and market type. Market-data discovery, account snapshots, and execution are separate capabilities and must not imply one another.

## Scope identity

Every registration is keyed by the complete tuple:

`provider_id / venue_id / account_id / environment / market_type`

Environments are distinct values: `mock`, `backtest`, `paper`, `demo`, `testnet`, and `live`. There is no wildcard account or environment and no inheritance from demo/testnet to live. Account aliases are not treated as verified account IDs unless the adapter has verified their identity.

## Capability model

Each feature has an explicit state and provenance:

- `supported`: declared and verified by the adapter contract.
- `unsupported`: explicitly unavailable.
- `unknown`: not evidenced or not currently verifiable.
- `not_configured`: required configuration is absent.

Execution features (order types, idempotency, partial fills, cancellation, order reconciliation) are separate from account read features (balances and positions). Withdrawals and transfers are separate, explicit capabilities and remain unsupported by default. Capability evidence includes source and optional timezone-aware verification timestamp.

A supported status is not inferred from a connected market-data provider, a populated instrument catalogue, or account snapshots.

## Initial adapter disposition

- Binance Spot Demo remains on its dedicated persistent order workflow. It is not registered as a generic `ExecutionProvider` because it has a different request, persistence, and reconciliation contract. A future adapter wrapper must preserve its Decimal strings, client IDs, unknown-outcome recovery, fill ledger, and Demo endpoint pinning.
- Alpaca Paper remains read-only. Its account snapshot capabilities do not grant order submission.
- PAPER remains the internal `PaperPortfolio` engine. It is not represented as an external broker account; a future paper execution adapter must wrap the portfolio transaction boundary and preserve its ledger/idempotency semantics.
- No LIVE registration is created. A DEMO registration never authorizes LIVE.

## Readiness policy

A new-entry readiness check requires exact scope identity, supported asset class/order type, client-order idempotency, partial-fill handling, cancellation, and order reconciliation. Balances and positions are evidence inputs for account risk and preflight; a caller requiring either must additionally require its corresponding capability. Unknown or missing evidence fails closed.

## Safety

This registry is descriptive and read-only. It does not create credentials, initialize exchange clients, submit/cancel orders, or change the existing LIVE disable switch. Registrations are opt-in by code, scoped to one account/environment/market type, and unique. No secret material is stored in registration data or audit output.
