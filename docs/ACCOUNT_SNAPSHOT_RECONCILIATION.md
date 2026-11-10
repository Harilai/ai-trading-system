# External account snapshots and read-only reconciliation

## Purpose

Persist an immutable history of authenticated account observations so the system can compare one real account snapshot with the next. This is not a paper portfolio update, accounting ledger, valuation engine or trading authorization.

## Data model

- `account_snapshots`: one immutable observation per provider/account/market and retrieval time.
- `account_snapshot_balances`: immutable per-asset free, used and total amounts stored as decimal strings.
- Each snapshot records its previous snapshot ID, retrieval timestamp, recording timestamp, content hash, balance count, reconciliation status and change count.
- SQLite triggers reject UPDATE and DELETE on snapshot rows and balance rows.
- All snapshot writes and balance inserts commit in one transaction.
- Histories are partitioned by provider ID, account alias and market type. A second account never inherits another account's previous snapshot.

## Reconciliation semantics

- `initial`: first observation for an account.
- `unchanged`: every asset present in both snapshots has identical free/used/total values.
- `changed`: at least one balance changed, a new asset appeared, or an asset present previously is absent from the latest response.
- Missing assets are labelled `absent_from_latest_snapshot`; the system does not silently interpret absence as a zero balance or claim that funds were transferred.
- Delta totals are calculated only when the asset exists in both snapshots and both totals are known.
- Out-of-order observations are rejected.
- No FX conversion, fiat valuation, cost basis, P&L, liabilities, margin, collateral or paper-ledger reconciliation is inferred.

## API

All endpoints require the owner token in `X-Agent-Token`:

- `POST /api/accounts/binance/spot/snapshots`: fetches a fresh read-only Binance Spot balance response and persists an immutable snapshot.
- `GET /api/accounts/snapshots`: list snapshots with optional provider/account filters.
- `GET /api/accounts/snapshots/{snapshot_id}`: retrieve balances and comparison with the immediately previous snapshot.

The existing `GET /api/accounts/binance/spot/balances` remains a live, non-persisting read. All endpoints are owner-authenticated.

## Safety boundaries

- Only the configured Binance Spot read-only connector is currently wired to snapshot capture.
- Values must be decimal strings; binary floating-point inputs are rejected.
- Malformed, non-finite, negative, duplicate-asset, timezone-naive and future-dated observations are rejected.
- Snapshot data is kept separate from the paper portfolio database tables and never changes simulated cash or positions.
- Snapshot history is not proof of ownership, solvency, withdrawable funds or exchange-side API-key permission scope.
- No live orders, transfers, withdrawals, wallet signing or derivatives are implemented.
