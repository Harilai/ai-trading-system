# Engineering roadmap

This roadmap reflects the repository state inspected on 2026-11-09. Items are not considered complete merely because they are planned or have tests written.

## Current integration queue

| Work | Branch / PR | Status |
|---|---|---|
| Paper-trading backend foundation | `feature/sprint-1-paper-trading` / [PR #1](https://github.com/Harilai/ai-trading-system/pull/1) | Open draft; tests not executed |
| Persistent agent foundation | `feature/persistent-autonomous-agent` / [PR #2](https://github.com/Harilai/ai-trading-system/pull/2) | Open draft; based on PR #1; tests not executed |
| Runtime expired-task recovery | `fix/agent-task-lease-recovery` / [PR #3](https://github.com/Harilai/ai-trading-system/pull/3) | Open draft; based on PR #2; tests not executed |

Keep the dependency order: review and integrate PR #1, then PR #2, then PR #3. Do not merge around failing financial-safety or persistence checks.

## Priority 0 — validate the existing foundation

- [x] Add a GitHub Actions workflow to run the backend test suite on covered pushes and pull requests.
- [ ] Obtain a successful run from GitHub Actions and fix any failures.
- [x] Correct paper-trade cost basis so entry fees are included and realised P/L is net of entry and exit fees.
- [ ] Review transaction boundaries, floating-point precision and duplicate submission behaviour.
- [ ] Add portfolio valuation using fresh market prices; distinguish cost basis, market value, realised P/L and unrealised P/L.
- [ ] Validate indicator calculations against known reference series and edge cases.
- [ ] Add CI for tests, formatting, type checks and dependency/security scanning.
- [ ] Review agent API authentication and ensure it is not exposed publicly before TLS, access controls, rate limits and secret management are in place.

## Priority 1 — make the persistent worker reliable

- [ ] Add durable task idempotency keys and enforce legal task-state transitions.
- [ ] Add lease renewal for long-running tasks and a bounded recovery policy.
- [ ] Expose worker heartbeat, queue depth, last error and liveness in health/status endpoints.
- [ ] Add graceful shutdown and restart/recovery integration tests.
- [ ] Add task scheduling and cancellation with explicit audit events.
- [ ] Keep the worker paused by default and preserve the emergency-stop invariant.

## Priority 2 — deterministic market intelligence

- [ ] Define typed contracts for market observations, agent reports, trade proposals and risk decisions.
- [ ] Implement Scout market discovery over explicitly supported EUR-quoted crypto markets.
- [ ] Implement Charts analysis and validated multi-timeframe indicator output.
- [ ] Implement a deterministic Risk Manager that rejects invalid or oversized proposals.
- [ ] Add News and Sentiment only after source attribution, freshness and data-quality policies are defined.
- [ ] Implement Chief orchestration and persist objectives, tasks, decisions and outcomes.

## Priority 3 — research and simulated execution

- [ ] Build a reproducible backtesting engine with fees, slippage and out-of-sample evaluation.
- [ ] Add strategy registry, regime classification and strategy-performance reporting.
- [ ] Implement Sniper trade proposals with entry, exit, stop-loss and position sizing.
- [ ] Add paper execution integration and position monitoring; no live broker orders.
- [ ] Keep learning/strategy changes gated by backtests and paper-trading evidence.

## Priority 4 — product and operations

- [ ] Build a responsive dashboard for portfolio, positions, agent activity, tasks, audit and controls.
- [ ] Add independent stock/ETF market-data provider and validated FX accounting before enabling those assets.
- [ ] Add PostgreSQL production configuration, migrations and tested backup/restore.
- [ ] Harden Docker Compose deployment for Ubuntu VPS: non-root containers, secrets, TLS/reverse proxy, monitoring and recovery.
- [ ] Document installation, operations, incident response and release procedures.

## Non-negotiable safety constraints

- Paper trading only until a separately reviewed live-execution phase is explicitly authorised.
- Live execution remains disabled by default and must not be enabled by an agent or an LLM.
- Risk limits cannot be changed by autonomous agents.
- Never commit exchange/broker credentials; use deployment secret management and no withdrawal permissions.
- No profitability claims without reproducible, appropriately validated evidence.

## Capability boundary

The repository's worker runs only when the application is deployed and running. A ChatGPT conversation does not keep the project process alive. The current worker foundation executes deterministic health-check tasks only; it is not yet an autonomous trading system.
