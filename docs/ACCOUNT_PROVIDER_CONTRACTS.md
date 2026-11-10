# Normalized account-provider contracts

## Purpose

Provide a venue-independent account boundary without merging accounts, environments or permission scopes. A configured adapter is registered under the stable pair `provider_id + account_alias`; the same venue may expose several separately configured accounts.

## Contract layers

- `AccountCapabilities`: venue, environment, supported market types, balances, positions, order management/submission/cancellation, reconciliation, read-only connector mode, transfers, withdrawals, wallet signing and whether key permissions have actually been verified.
- `AccountBalance` / `AccountBalanceSnapshot`: canonical asset codes and decimal-string free/used/total amounts, account identity, market type, environment, timestamp and explicit permission flags.
- `AccountPosition`: normalized instrument identity, quantity/side, quote currency, entry/mark price, market value and unrealized P&L.
- `AccountOrder`: normalized client/exchange IDs, instrument, side/type/status, requested/filled/remaining quantities, average fill and quote currency.
- `AccountBalanceProvider`, `PositionProvider` and `OrderReconciliationProvider`: capability-specific protocols. A position provider returns a timestamped envelope containing provider ID, account alias, environment, market type and normalized positions. A provider must not imply support for methods it does not implement.

## Registry invariants

1. Only configured providers with an explicit balance capability can be registered.
2. Accounts are keyed by provider ID and account alias; ambiguous IDs require the alias.
3. A balance response must match the registered identity, market type and environment.
4. Timestamps must be timezone-aware, no more than five minutes old and no more than one minute in the future.
5. Amounts must be plain decimal strings; floats, scientific notation, negative balances, duplicate assets and malformed values are rejected.
6. Runtime read-only/execution/withdrawal flags must match the registered capabilities.
7. Read-only account snapshots are allowed only when the connector is explicitly read-only and has no order submission, transfer, withdrawal or wallet-signing capability. Demo/trading-enabled accounts cannot be persisted through this endpoint.
8. `permissions_verified=false` means the software has not independently verified the exchange API-key scopes. A read-only adapter only promises that its own code path calls read-only endpoints; operators must still create keys with the minimum required permissions.

## Current adapters

- `ccxt:binance` / `binance-spot-readonly`: production Spot balance observation only; no order submission, transfers, withdrawals or wallet signing. The account key's actual permissions are not verified by the adapter.
- Optional generic `ccxt:{exchange_id}` read-only adapter (Kraken by default): configured with `CCXT_READONLY_EXCHANGE_ID`, `CCXT_READONLY_API_KEY` and `CCXT_READONLY_API_SECRET`; it calls only CCXT `fetch_balance()` for Spot. It does not implement order, transfer or withdrawal methods.
- `ccxt:binance-demo` / `binance-spot-demo`: virtual Demo account; limit-order submission/management and reconciliation are separately declared. It cannot be stored as a read-only snapshot.
- No generic broker, margin, futures, options, DEX or wallet account adapter is added by this phase.

## API

- `GET /api/accounts`: existing account summary plus `normalized_accounts`.
- `GET /api/accounts/{provider_id}/balances?account_alias=...`: normalized balances; owner token required.
- `GET /api/accounts/{provider_id}/positions?account_alias=...&market_type=...`: normalized position reads for providers declaring position support; owner token required.
- `POST /api/accounts/{provider_id}/snapshots?account_alias=...&market_type=...`: immutable balance/position snapshot for explicitly read-only accounts; owner token required.
- Existing Binance-specific endpoints remain available for compatibility.

The append-only snapshot store now supports balances and positions across Spot, cash equity, margin, futures, options, FX and on-chain market types, with environment-scoped history, exact decimal quantities and per-instrument change detection. Position source timestamps are validated for freshness and combined balance/position observations must be within 60 seconds of each other. No current production read-only adapter yet declares positions, so position endpoints return an unsupported-capability response until such an adapter is connected. Snapshots remain separate from the paper portfolio.