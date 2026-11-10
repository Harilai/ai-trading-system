# Order lifecycle and reconciliation design

## Scope

This phase hardens the existing **paper-only** execution path. It does not add a broker adapter, exchange submission, wallet signing, live trading, margin, derivatives or withdrawal permissions.

The current backend uses one SQLite database file for `AutonomousStore` task state and `PaperPortfolio` trades/positions. Order intent, risk decision, paper fill and task completion are nevertheless separate transaction boundaries. This design makes the order/fill boundary explicit and prevents stale workers from completing tasks they no longer own.

## Paper order state machine

Persist each risk-approved order under its immutable `client_order_id` in an `order_intents` table and append every transition to `order_intent_events`. The proposal/request and deterministic Risk Manager result are produced before the first persisted state, so the first durable state is `risk_approved`.

| State | Meaning | Terminal |
|---|---|---|
| `risk_approved` | Deterministic Risk Manager approved the exact persisted intent, before fill | No |
| `filled` | Paper trade and portfolio mutation committed atomically with this state | Yes |
| `rejected` | Risk gate rejected the intent | Yes |
| `expired` | Unfilled intent was abandoned after its owning task/request was lost or its data expired | Yes |
| `reconciliation_required` | Intent and trade ledger disagree; new orders must fail closed | No, operator/recovery action required |

Legal transitions:
- `risk_approved -> filled | rejected | expired | reconciliation_required`
- `filled -> reconciliation_required` only when the referenced trade is missing or inconsistent.
- `reconciliation_required -> filled` only after a later ledger comparison proves the exact trade matches the persisted intent.
- `rejected` and `expired` are terminal.

There are deliberately no `submitted`, `accepted`, `partial_fill` or broker-pending states because no broker order exists.

## Persistence and idempotency

- The canonical intent payload binds symbol, provider/instrument identity, side, quantity, quote currency, strategy, stop/take levels, reference price and relevant FX evidence.
- Reusing a `client_order_id` with a different payload is a conflict.
- A paper fill is allowed only from `risk_approved`.
- The trade insert, position/cash mutation, intent transition to `filled`, and fill event commit in the same SQLite transaction.
- All application order paths require a persisted risk-approved intent; the lower-level portfolio compatibility path remains available only for tests/legacy callers.
- Replaying a filled intent returns the original trade without a second portfolio mutation.
- A risk-approved intent with no trade may resume only while its persisted market timestamp and any required FX timestamps remain fresh. Otherwise it expires; the next scheduled cycle must obtain fresh market data and a new task ID.
- A filled intent with no matching trade, or a trade inconsistent with its intent, moves to `reconciliation_required` and blocks new entries. Risk-reducing exits remain subject to position and risk validation.

## Worker lease fencing

Each task claim receives a random `lease_token`. Heartbeats extend the lease. Completion, failure and retry scheduling are conditional on both task ID and the current lease token. A worker whose lease was reclaimed cannot complete or fail the newer worker's task.

Expired leases are reclaimed with the token cleared. Order-intent reconciliation then expires unfilled autonomous intents whose task is no longer actively leased. Manual-owner intents left unfilled across process restart are expired before the API accepts new work.

## Recovery sequence

1. Recover expired task leases.
2. Reconcile order intents against the trade ledger and currently active task leases.
3. Mark missing/mismatched fills as `reconciliation_required`.
4. Expire unfilled intents whose market/FX evidence is stale and which no longer have a live task lease.
5. Allow only new intents whose market/FX data and risk checks are fresh.
6. Complete tasks only with the matching lease token.

## Equity protective levels

Equity/ETF stop-loss and take-profit fields are currently stored but not monitored. The follow-on subphase will add a paper-only monitor that uses fresh USD quotes and FX, creates idempotent exit intents, and reconciles their fills. Until that is complete, equity exits remain manual. No autonomous equity entry or exit is enabled.

## Acceptance criteria

- Existing SQLite databases migrate additively without losing trades, positions, tasks or audit history.
- State transitions reject illegal edges and every transition is auditable in `order_intent_events`.
- A duplicate intent cannot create a second paper fill.
- Trade + portfolio mutation + `paper_filled` state are atomic.
- Crash/restart tests cover intent-created, risk-approved, paper-filled and task-completed boundaries.
- A missing/mismatched trade moves to `reconciliation_required` and blocks new orders.
- Stale/future/malformed market or FX evidence fails closed.
- Expired task leases cannot be completed by an old worker after a new claim.
- Worker heartbeats prevent healthy long-running work from being reclaimed.
- Full tests, Docker build and non-root runtime checks pass before merge.
