# Auditoría de ramas remotas

Fecha: 2026-11-10. Repositorio: `Harilai/ai-trading-system`.

Se compararon las puntas de las 86 referencias remotas con `main` (`35b894800ebf24324aebb2f09c363bdda8d1fca8`) y con las ramas fuente de PRs fusionadas. Una rama se considera candidata segura si su punta ya es ancestro de `main` o coincide con el head de una PR fusionada. Esto demuestra que su código ya está integrado; no borra la rama.

## Eliminadas tras aprobación del propietario (66)

Las 66 ramas de esta lista se eliminaron de GitHub el 2026-11-10, después de verificar que cada punta estaba integrada en `main` o correspondía exactamente al head de una PR fusionada. La autorización se recibió tras presentar la lista completa. Los commits siguen accesibles desde `main`/el historial de PRs.

```text
docs/binance-demo-smoke-runbook
docs/multi-agent-development-workflow
docs/multi-asset-real-capital-target-v2
docs/post-account-snapshot-handoff
docs/post-binance-account-handoff
docs/post-binance-demo-fill-ledger-handoff
docs/post-binance-demo-handoff
docs/post-dashboard-handoff
docs/post-demo-handoff
docs/post-demo-periodic-reconciliation
docs/post-instrument-catalogue-handoff
docs/post-instrument-contract-handoff
docs/post-multicurrency-handoff
docs/project-continuity-handoff
docs/roadmap-external-backups-complete
docs/session-handoff-and-next-phase
docs/update-handoff-after-pr63
feat/worker-readiness-healthcheck
feature/autonomous-cycle-scheduler
feature/backtest-cost-sensitivity
feature/chief-risk-gated-decisions
feature/cost-stress-promotion-gate
feature/dashboard-operations-v2
feature/dedicated-sniper-agent
feature/equity-etf-research-pipeline
feature/indicator-reference-validation
feature/instrument-contract-v2
feature/integrated-autonomous-system
feature/local-paper-demo
feature/market-regime-diagnostics
feature/operations-dashboard
feature/paper-execution-idempotency
feature/persist-strategy-research-history
feature/persistent-instrument-catalog
feature/rotating-equity-universe-sampling
feature/scheduled-sqlite-backups
feature/sprint-1-paper-trading
feature/sqlite-backup-restore-utility
feature/strategy-walk-forward-promotion-gates
feature/twelve-data-credit-aware-quota
feature/twelve-data-quota-cache
feature/twelve-data-us-equities-etf-data
feature/universal-provider-layer
fix/manual-withdrawals-from-available-cash
fix/paper-execution-finite-input-validation
fix/secure-paper-order-risk-gate
fix/sniper-round-trip-risk-sizing
fix/walk-forward-history-default
fix/worker-loop-resilience
hardening/docker-runtime-security
integration/provider-and-equity-research
phase/account-connector-contracts-v1
phase/account-provider-contracts
phase/account-snapshot-reconciliation
phase/binance-demo-fill-ledger
phase/binance-demo-fill-ledger-v2
phase/binance-demo-order-lifecycle
phase/binance-demo-periodic-reconciliation
phase/binance-readonly-account
phase/binance-spot-testnet-adapter
phase/ccxt-readonly-account-adapter
phase/normalized-account-positions
phase/order-lifecycle-reconciliation-finalize
phase/vps-operational-readiness
security/twelve-data-header-auth
test/provider-discovery-failure-isolation
```

## Conservar o revisar antes de borrar (16)

Estas puntas no están contenidas en `main` ni coinciden con un head fusionado. Aunque no aparezca una PR abierta para ellas, primero hay que comprobar manualmente si contienen trabajo no integrado o si siguen siendo necesarias.

```text
docs/multi-asset-real-capital-target
feat/automated-sqlite-backups
feature/autonomous-analysis-pipeline
feature/order-lifecycle-reconciliation-validation
feature/persistent-agent-main
feature/persistent-autonomous-agent
feature/portfolio-valuation-withdrawals
feature/provider-scoped-portfolio
fix/agent-task-lease-recovery
fix/market-discovery-malformed-tickers
phase/account-position-snapshots-v2
phase/binance-demo-periodic-reconciliation-v2
phase/dashboard-research-withdrawals
phase/external-backups
phase/multicurrency-paper-portfolio
phase/order-lifecycle-reconciliation
```

## Mantener

- `main`: rama principal.
- `demo`: apunta a `35b894800ebf24324aebb2f09c363bdda8d1fca8`, versión local de paper demo.
- `phase/account-position-snapshots`: rama de la PR #74, abierta y lista para revisión.

Se mantuvieron `main`, `demo` y `phase/account-position-snapshots`; las 16 ramas divergentes no se eliminaron.
