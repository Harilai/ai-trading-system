# Order lifecycle and reconciliation design

## Scope

This phase hardens the existing **paper-only** execution path. It does not add a broker adapter, exchange submission, wallet signing, live trading, margin, derivatives or withdrawal permissions.

The current backend uses one SQLite database file for `AutonomousStore` task state and `PaperPortfolio` trades/positions. Order intent, risk decision, paper fill and task completion are nevertheless separate transaction boundaries. This design makes the order/fill boundary explicit and prevents stale workers from completing tasks they no longer own.

## Paper order state machine

Persist each order under its immutable `client_order_id` in an `order_intents` table and append every transition to `order_intent_events`.

| State | Meaning | Terminal |
|---|---|---|
| `risk_approved` | Deterministic Risk Manager approved and the exact normalized paper request and decision evidence are persisted | No |
| `filled` | Paper trade and portfolio mutation committed atomically with this state | Yes |
| `rejected` | Deterministic execution/risk validation rejected the intent | Yes, unless a contradictory trade row exists |
| `expired` | Unfilled approval was abandoned because its market/FX evidence became stale | Yes, unless a contradictory trade row exists |
| `reconciliation_required` | Intent and trade ledger disagree; new buys fail closed | No, operator/recovery action required |

The current implementation creates an intent only after the deterministic Risk Manager approves it; it does not persist a separate `intent_created` state.

Legal transitions:
- `risk_approved -> filled | rejected | expired | reconciliation_required`
- `filled -> reconciliation_required` only when the referenced trade is missing or inconsistent.
- `rejected | expired -> reconciliation_required` only if a contradictory trade row exists for that terminal intent.
- `reconciliation_required -> filled` only when reconciliation later finds the matching trade.

There are deliberately no `submitted`, `accepted`, `partial_fill` or broker-pending states because no broker order exists.

## Persistence and idempotency

- The canonical intent payload binds symbol, provider/instrument identity, side, quantity, quote currency, strategy, stop/take levels, reference price and relevant FX evidence.
- Reusing a `client_order_id` with a different payload is a conflict.
- A paper fill is allowed only from `risk_approved`.
- Reconciliation compares the complete immutable request with the persisted trade row (instrument, side, quantity, fill price, fees, totals, FX evidence, market timestamp and protective levels); a matching client ID alone is insufficient.
- The trade insert, position/cash mutation, intent transition to `filled`, and fill event commit in the same SQLite transaction.
- Replaying a filled intent returns the original trade without a second portfolio mutation.
- A retry resumes the exact persisted request only while its market and, when applicable, FX timestamps remain within configured freshness limits. Retries use backoff and a finite six-attempt recovery budget. If evidence is stale, the owning task is lost, or the retry budget is exhausted, the intent expires and the next scheduled cycle must obtain fresh data and a new task ID.
- A filled intent with no matching trade, or a trade inconsistent with its intent, moves to `reconciliation_required` and blocks new orders.

## Worker lease fencing

Each task claim receives a random `lease_token`. Heartbeats extend the lease. Completion, failure and retry scheduling are conditional on both task ID and the current lease token. A worker whose lease was reclaimed cannot complete or fail the newer worker's task.

Expired leases are reclaimed with the token cleared. A failed decision task with a durable risk-approved intent can be requeued, but the exact request is resumed only if its stored market/FX evidence is still fresh. Stale approvals expire. Manual-owner paper requests are filled atomically with the ledger in the same transaction, so they cannot remain half-submitted across restart.

## Recovery sequence

1. Recover expired task leases.
2. Reconcile order intents against the trade ledger.
3. Mark missing/mismatched fills as `reconciliation_required`.
4. Requeue failed decision tasks with backoff (maximum six attempts); require fresh market/FX evidence before resuming.
5. Expire stale approvals and allow new intents only after a fresh decision.
6. Complete tasks only with the matching lease token.

## Equity protective levels

Equity/ETF stop-loss and take-profit fields are currently stored but not monitored. The follow-on subphase will add a paper-only monitor that uses fresh USD quotes and FX, creates idempotent exit intents, and reconciles their fills. Until that is complete, equity exits remain manual. No autonomous equity entry or exit is enabled.

## Acceptance criteria

- Existing SQLite databases migrate additively without losing trades, positions, tasks or audit history.
- State transitions reject illegal edges and every transition is auditable.
- A duplicate intent cannot create a second paper fill.
- Trade + portfolio mutation + `filled` state are atomic.
- Crash/restart tests cover risk-approved, filled-before-task-completion, task lease expiry and replay boundaries.
- A missing/mismatched trade moves to `reconciliation_required` and blocks new buys.
- Stale/future/malformed market or FX evidence fails closed.
- Expired task leases cannot be completed by an old worker after a new claim.
- Worker heartbeats prevent healthy long-running work from being reclaimed.
- Full tests, Docker build and non-root runtime checks pass before merge.
