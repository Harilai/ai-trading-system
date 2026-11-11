# Coordinated external account snapshot — design

> Status: design decision; implementation not yet present.
> Scope: authenticated, read-only account evidence. This design does not enable execution.

## Why this phase is needed

The repository currently persists balances and positions through separate append-only stores and separate API requests. Their records have account scope and individual observation timestamps, but no common capture identity. Two individually valid snapshots therefore do not prove that they describe the same account state. A consumer must not silently join them as if they were atomic.

The current balance snapshot store is Spot-only and has no explicit coverage field. The position snapshot store supports market-scoped `complete` / `partial` coverage. Existing production Binance and generic CCXT adapters declare no position-reading capability; Binance Spot Demo is an order connector, not a positions provider. Do not fabricate a positions adapter from balances or order fills.

## Decision

Introduce a coordinated capture group that references the existing balance and position snapshot records. Do not create a second copy of balances or positions and do not merge either ledger into the paper portfolio.

A group is scoped by:

- `provider_id`
- `account_alias`
- `environment`
- `market_type`
- stable `capture_id`

The group stores capture start/end timestamps, each provider observation timestamp, the maximum observed skew, source/capability metadata, child snapshot IDs, per-side coverage, a canonical group hash, and a terminal status.

## Capture protocol

1. Resolve one registered account and verify the requested `market_type` is explicitly supported.
2. Require read-only connector semantics: no order submission/cancellation, transfers, withdrawals or wallet signing. Require explicit balance and position reader capabilities.
3. Fetch both sides without mutating the exchange. Normalize both responses fully in memory before writing either ledger.
4. Verify exact provider/account/environment/market identity on both responses. Reject mismatches, stale/future timestamps, duplicate assets/instruments, malformed decimals and unsupported market types.
5. Require explicit coverage for both sides. A position response must be `complete` or `partial`; balance coverage must become equally explicit before this workflow is enabled. Unknown coverage is never promoted to complete.
6. Enforce a configurable, conservative maximum timestamp skew. The initial default should be 5 seconds and must be tested at both boundaries. If the provider does not expose source timestamps, record the retrieval timestamp and label its provenance; do not pretend it is exchange event time.
7. Persist both child records and the group row in one SQLite transaction on the same database connection. Refactor the existing stores to expose transaction-aware insert methods; do not call their current independently committing public methods from the coordinator.
8. If validation or persistence fails, roll back the entire group and both child records. Never leave a group marked complete with only one child.
9. The group status is `complete` only when both child snapshots exist, both coverage states are complete, identity matches, timestamps meet skew/freshness rules and the transaction commits. Otherwise persist an explicitly partial/failed capture only if its semantics are well-defined; never infer missing assets or positions as zero/closed.

## Data and accounting invariants

- Keep exact decimal strings end-to-end; no float conversion.
- Keep child ledgers append-only and retain their existing hashes and per-ledger diffs.
- The group hash covers canonical scope, timestamps, coverage, child IDs and child content hashes.
- Idempotency is keyed by a caller-supplied capture request ID plus account scope. Replaying the same request and same payload returns the existing group; conflicting payload under the same ID is rejected.
- A partial or failed group cannot establish that the account is flat, that an asset was liquidated, or that funds are missing.
- No snapshot from a Demo account may be represented as production, and no external account snapshot may affect paper cash, positions, equity or strategy decisions.
- Group history and details require the existing owner-authenticated API boundary. Responses must not expose credentials or raw provider payloads.

## API shape (proposed)

- `POST /api/accounts/{provider_id}/snapshots/coordinated?account_alias=...&market_type=...`
  - read-only capture; requires owner token and explicit capability checks.
  - accepts an idempotency/capture request ID, not arbitrary caller-supplied balances or positions.
- `GET /api/accounts/snapshots/coordinated?provider_id=...&account_alias=...`
  - bounded group history.
- `GET /api/accounts/snapshots/coordinated/{capture_id}`
  - group metadata plus references/details from the existing child stores.

The endpoint must not accept user-authored balances/positions as provider evidence.

## Required tests before merge

- matching account/environment/market scope; reject each mismatch independently;
- complete/partial/unknown coverage combinations;
- empty but explicitly complete response versus empty partial/unknown response;
- timestamps within, exactly at and outside the skew boundary; stale and future observations;
- exact decimal preservation and rejection of floats, NaN, infinity and excessive precision;
- duplicate asset/instrument rejection;
- failure on second child insert rolls back the first child and group row;
- idempotent replay and conflicting replay;
- concurrent captures for the same account serialize safely and preserve previous-snapshot ordering;
- restart/reopen persistence, append-only triggers and hash verification;
- Demo/production isolation and proof that no execution method is called;
- paper portfolio remains byte-for-byte/logically unchanged.

## Provider gate and next dependency

Before implementation, identify a concrete provider/account that can return both balances and positions for one supported market with documented read-only endpoints and verifiable coverage. Current Spot balance adapters do not satisfy this gate. If no such provider is available, implement and test the generic group storage/contract behind capability gates, but do not claim an operational coordinated capture.

## Explicitly out of scope

Live execution, order placement, cancellation, transfers, withdrawals, wallet signing, margin/futures/options enablement, portfolio valuation changes, and strategy/Chief integration.
