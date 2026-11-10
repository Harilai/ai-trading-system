# Normalized account-provider contracts

## Purpose

Provide a venue-independent account boundary without merging accounts, environments or permission scopes. A configured adapter is registered under the stable pair `provider_id + account_alias`; the same venue may expose several separately configured accounts.

## Contract layers

- `AccountCapabilities`: venue, environment, supported market types, balances, positions, order management/submission/cancellation, reconciliation, read-only connector mode, transfers, withdrawals, wallet signing and whether key permissions have actually been verified.
- `AccountBalance` / `AccountBalanceSnapshot`: canonical asset codes and decimal-string free/used/total amounts, account identity, market type, environment, timestamp and explicit permission flags.
- `AccountPosition`: normalized instrument identity, quantity/side, quote currency, entry/mark price, market value and unrealized P&L.
- `AccountOrder`: normalized order observation keyed by provider/account/environment/market, with exchange/client IDs, instrument, side/type/status, requested/filled/remaining quantities, average fill, quote currency and observation timestamp.
- `AccountOrderFill`: immutable per-trade execution with stable trade/order IDs, exact decimal amount/price/cost, quote currency, timestamp and explicit fee amount/currency.
- `AccountFillHistory`: timestamped provider response containing the explicit `history_complete` flag and fills scoped to one account/order. An empty or partial list is never treated as complete unless the provider says so.
- `AccountOrderLedger`: SQLite evidence store for normalized order observations and individual fills. It is idempotent, scope-isolated and append-only for event/fill history; it does not submit or cancel orders.
- `AccountOrderReconciliationService`: refreshes one known order through a read-only provider, verifies identity and stores normalized evidence. A missing order or mismatched identity never changes the stored order status; there is no scheduler or execution path.
- `AccountBalanceProvider`, `PositionProvider` and `OrderReconciliationProvider`: capability-specific protocols. Order reconciliation returns typed `AccountOrder` and `AccountFillHistory` values; a provider must not imply support for methods it does not implement.

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
- `OrderReconciliationProvider` is currently a contract only; no account adapter is registered through it. Binance Spot Demo continues to use its separate, provider-specific order store and reconciler.
- No generic broker, margin, futures, options, DEX or wallet account adapter is added by this phase.

## API

- `GET /api/accounts`: existing account summary plus `normalized_accounts`.
- `GET /api/accounts/{provider_id}/balances?account_alias=...`: normalized balances; owner token required.
- `POST /api/accounts/{provider_id}/snapshots?account_alias=...`: immutable balance snapshot for explicitly read-only Spot accounts only; owner token required.
- Existing Binance-specific endpoints remain available for compatibility.

The snapshot store remains Spot-balance-only. Position and order contracts are foundations for later adapters; they do not imply that positions or orders are available from every current account provider.