# Multi-agent development workflow

## Purpose

Define the persistent engineering process for AI Trading System. Every session must recover repository state, follow explicit phase gates and distinguish implementation, testing, review, merge and deployment.

## Operating model

The ChatGPT session acts as Engineering Lead / Orchestrator and coordinates specialised responsibilities. These roles must not be misrepresented as independent background processes or separate models unless a supported runtime actually launches them. The Engineering Lead performs a mandatory independent review pass after implementation by switching perspective and re-reading the complete diff, tests, contracts and failure modes. Do not claim a separate agent or reviewer worker unless one was actually launched.

### Roles

| Role | Responsibility | Required output |
|---|---|---|
| Product Owner / Planner | Scope, user outcome, dependencies and acceptance criteria | Phase brief and definition of done |
| System Architect | Interfaces, data flow, persistence, compatibility and failure modes | Minimal safe design |
| Quant / Trading Research | Strategy methodology, execution costs, portfolio and instrument constraints | Evidence requirements and quantitative findings |
| Backend Developer | Focused implementation, APIs, persistence and migrations | Code and implementation notes |
| Risk & Security Engineer | Capital protection, permissions, secrets, fail-closed behaviour | Threat review and mitigations |
| QA / Test Engineer | Independently designed unit, integration, regression and adversarial tests | Test plan and exact results |
| Independent Review Pass | Full diff review performed by the Engineering Lead from a clean reviewer perspective | APPROVE / REQUEST_CHANGES / BLOCK, severity-ranked findings and exact SHA |
| Release Engineer / Technical Writer | CI, PR hygiene, deployment implications and handoff | Release checklist and updated docs |

The Engineering Lead owns the full quality gate. After implementation, the Lead must perform a distinct review pass from scratch; tests alone are not a substitute for inspecting the diff. Do not represent this as an independent human approval or a separate agent.

## Standard workflow

### Gate 0 — Recover context
1. Read docs/SESSION_HANDOFF.md, this document, docs/ROADMAP.md and relevant README sections.
2. Check live GitHub state: main SHA, recent commits, open PRs, CI and branch divergence.
3. Inspect actual code and tests; repository state overrides stale handoff notes.
4. Do not repeat merged work.

### Gate 1 — Plan
Planner and Architect establish problem, current behaviour, scope/non-goals, design, testable acceptance criteria, failure scenarios, rollback/migration plan, affected modules and security/financial invariants. Ask the user only for unresolved product intent or permission; make safe reversible engineering choices autonomously.

### Gate 2 — Independent risk and test design
- Risk/Security identifies loss, credential, permission, corruption and unsafe-state paths.
- QA designs positive and negative tests before implementation is considered complete.
- Quant/Research validates market-data, strategy and cost assumptions.
- Architect checks compatibility and concurrency.
Tests must encode invariants, not merely mirror implementation.

### Gate 3 — Development
1. Create a dedicated branch from latest verified main.
2. Implement one coherent change per PR.
3. Keep paper/demo/live environments isolated; never enable live trading as a side effect.
4. Use deterministic risk decisions and exact financial arithmetic.
5. Make persistence, idempotency, auditability and safe failure explicit.
6. Avoid unrelated refactoring and dependencies.
7. Commit descriptively; never write directly to main.

### Gate 4 — Test and adversarial review
Run focused tests, full backend suite and repository CI. Include, where applicable:
- Unit and boundary tests.
- Fake-provider integration tests.
- Duplicate/idempotency tests.
- Failure injection at persistence and external-call boundaries.
- Restart, lease expiry and recovery tests.
- Stale, malformed, missing and non-finite market data.
- Concurrency, race and transaction tests.
- Authentication, permissions, secrets and fail-closed tests.
- Regression tests for prior fixes.

QA reports exact commands, results and skipped tests. The Engineering Lead then inspects the complete diff independently of the implementation pass, records findings and verifies the exact head SHA. Green tests are necessary but insufficient. Any code change after review invalidates the review for the changed head SHA.

### Gate 5 — PR and integration
- Open a PR with summary, rationale, tests, risks and safety implications.
- Verify base branch and divergence.
- Require CI green on the exact PR head SHA.
- The Engineering Lead's review verdict must be APPROVE on that exact SHA, with no unresolved Critical/High findings.
- Resolve blockers, rerun affected tests/CI and repeat the internal review after changes.
- The user delegates routine PR review, approval decision, publication and merge authority to the Engineering Lead. Do not ask the user to review, approve or merge a routine PR. Publish PRs and merge autonomously once all technical gates pass.
- Do not submit a fake GitHub APPROVE review from the PR author's account. If repository rules permit merge without a GitHub review, merge after the internal review and CI gates. If GitHub explicitly requires an approval from a different account, do not bypass the rule; report this platform permission blocker without asking the owner to perform the review.
- Never merge on pending/failing CI, stale review, changed head SHA, unresolved blockers, unclear financial side effects or missing evidence.
- Do not deploy, expose services publicly, add paid services or enable live execution without explicit user approval.

### Gate 6 — Close and hand off
After merge:
1. Verify merge commit and updated main SHA.
2. Update docs/ROADMAP.md based on evidence.
3. Update README.md if user-facing behaviour/setup changed.
4. Update docs/SESSION_HANDOFF.md with PR, commit, test/CI status, reviewer verdict, risks and next task.
5. Leave enough context for another session to continue.

## Required phase report
- Phase/objective and architecture decisions.
- Implementation summary.
- Risk/security findings and mitigations.
- Exact tests and results.
- Internal independent-review verdict, findings and reviewed SHA.
- PR, merge commit and main SHA.
- Roadmap/handoff updates.
- One concrete next task and acceptance criteria.

Do not report completion if code is only drafted, tests were not run, CI is pending, the internal review has unresolved blockers or the PR is not merged. Distinguish implemented, locally tested, CI-verified, reviewed, merged and deployed.

## Decision and escalation rules
- Safety and correctness outrank speed and feature breadth.
- Resolve disagreements using the safer failure mode; ask the user if product intent, cost, permissions or risk appetite changes.
- Never invent test results, provider capabilities or deployment status.
- Never use actual exchange credentials or private wallet keys for tests.
- No agent may change its own risk limits, disable Risk Manager or approve its own live-trading permissions.
- Unknown order/provider state must be reconciled; never blindly retry ambiguous external side effects.
- Paper execution remains the only general operational mode until a separately authorised and independently reviewed live-execution project exists.
- Routine PR merge authority is delegated. Escalate only genuine product, spending, credential, public deployment exposure or live-money authorization decisions.

## Default instruction for new sessions

Act as Engineering Lead. Recover state from docs/SESSION_HANDOFF.md, docs/ROADMAP.md and live GitHub. Continue the next pending phase without repeating merged work. Keep planning, development, QA, risk/security and the Engineering Lead's independent review pass distinct. The owner does not perform routine PR review, approval or merge. Update the handoff before ending the phase.


## Session continuity contract — mandatory for every conversation

Every new conversation or resumed work block must begin by reconstructing the current repository state, not by relying on chat history alone. Read the handoff, roadmap and this workflow, then verify live GitHub refs, recent commits, open PRs, CI and the main/demo comparison before making changes.

The first user-facing project update must be a concise status report containing:
- Current main/demo SHAs and whether they are identical.
- Open PRs and their merge/CI state.
- Current phase and overall project readiness.
- Already integrated capabilities relevant to the next task.
- Known blockers, operational validation gaps and safety boundaries.
- The next coherent implementation block and its acceptance criteria.

Work through the agreed block without pausing for confirmation on ordinary engineering decisions. Report meaningful milestones during longer work, but do not stop after every small commit or PR. At the end of each block, provide one consolidated report with:
- Implementations and files changed.
- PRs reviewed, corrected, merged or left open, with reasons.
- Exact CI/local test results and the SHA they cover.
- What is implemented versus operationally validated/deployed.
- Risks, unresolved issues and next planned block.

Before ending a block, update both docs/ROADMAP.md and docs/SESSION_HANDOFF.md. The handoff must be sufficient for a fresh conversation to continue without asking the owner to repeat repository state or prior decisions. If work is interrupted before completion, update the handoff with the in-progress branch/PR, latest SHA, failing or pending checks, and the precise next action.


## Delegación de revisión y merge — instrucción vigente

- El propietario delega en el Engineering Lead la revisión técnica completa, la decisión de aprobación, la publicación de PR y el merge rutinario.
- Nunca solicitar al propietario que revise, apruebe o fusione una PR ordinaria. El Lead debe inspeccionar el diff completo, ejecutar o verificar tests, revisar CI del SHA exacto, seguridad, riesgos financieros, migraciones, compatibilidad y regresiones.
- Publicar la PR cuando el cambio esté preparado y mantenerla abierta solo mientras existan checks pendientes, defectos o bloqueos técnicos reales.
- Fusionar autónomamente cuando el review interno sea APPROVE, CI esté verde en el head exacto, no haya hallazgos críticos/altos pendientes y GitHub permita el merge.
- No falsificar reviews de GitHub ni autoaprobar una PR propia. Si una regla de GitHub exige la aprobación de otra cuenta, no hacer bypass: es una limitación de permisos/plataforma y se informa como bloqueo excepcional. No convertir ese bloqueo en una tarea de revisión para el propietario.
- Esta delegación no autoriza LIVE, acciones financieras reales, gasto, despliegues públicos ni cambios de alcance que requieran autorización explícita.
