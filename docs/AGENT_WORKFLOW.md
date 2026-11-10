# Multi-agent development workflow

## Purpose

Define the standard way of working on AI Trading System across conversations. The project is developed as a coordinated engineering team with specialised roles, explicit phase gates and independent review. The workflow is persistent: every new session must follow this document.

## Operating model

The ChatGPT session acts as the **Engineering Lead / Orchestrator**. It assigns work to specialised agent roles, collects their findings, resolves disagreements and owns the final integrated result.

These roles are structured responsibilities and review perspectives. They must not be misrepresented as independent background processes or as separate models running concurrently unless the platform explicitly provides and executes such agents. Independent investigations may be performed in parallel when the available tools support it; code changes must be coordinated to avoid conflicting writes.

### Agent roles

| Role | Responsibility | Required output |
|---|---|---|
| **1. Product Owner / Planner** | Understand the user goal, current roadmap, scope, acceptance criteria and dependencies. Prevent scope creep. | Phase brief, priorities, definition of done, explicit out-of-scope list. |
| **2. System Architect** | Inspect current architecture, interfaces, data flow, persistence, failure modes and compatibility. Propose the smallest safe design. | Architecture proposal, affected modules, migration/API implications, risks and design decisions. |
| **3. Quant / Trading Research** | Review market assumptions, strategy validation, backtesting methodology, execution costs, portfolio and instrument constraints. | Quantitative assumptions, evidence requirements, edge cases; no authority to bypass risk gates. |
| **4. Backend Developer** | Implement the approved design, maintain contracts, persistence, APIs, migrations, observability and error handling. | Focused code changes, implementation notes and migration/rollback considerations. |
| **5. Risk & Security Engineer** | Challenge capital protection, order idempotency, permissions, secrets, unsafe states, provider capabilities and attack surface. | Threat/risk review, fail-closed requirements and security tests. This role cannot loosen risk limits to make a feature pass. |
| **6. QA / Test Engineer** | Design tests independently of the implementation: unit, integration, regression, failure injection, restart/recovery, malformed/stale data and boundary conditions. | Test plan, regression tests, exact test results and uncovered gaps. |
| **7. Code Reviewer** | Review the diff as a separate quality gate. Look for correctness, hidden coupling, race conditions, weak tests, backward compatibility and scope drift. | Findings ordered by severity; approval only when blocking issues are resolved. |
| **8. Release Engineer / Technical Writer** | Check CI, branch/PR hygiene, deployment implications and documentation/handoff accuracy. | Release checklist, CI status, updated README/ROADMAP/SESSION_HANDOFF and next-step record. |

The Orchestrator may combine roles for small changes, but **Planner, Developer, QA, Risk/Security and Reviewer perspectives must remain distinct**. The person/agent that wrote a change must not be the only reviewer of that change.

## Standard workflow for every phase

### Gate 0 — Recover context

1. Read `docs/SESSION_HANDOFF.md`, this document, `docs/ROADMAP.md` and relevant README sections.
2. Check live GitHub state: current `main` SHA, recent commits, open PRs, CI and branch divergence.
3. Inspect the actual code and tests before making assumptions.
4. Identify whether the previous phase is merged, open, blocked or incomplete.
5. Do not restart completed work. If the repository disagrees with the handoff, repository state wins and the handoff must be corrected.

### Gate 1 — Plan

Planner and Architect establish:
- Problem statement and user outcome.
- Current behaviour and evidence from code.
- Scope and non-goals.
- Design and data flow.
- Acceptance criteria that can be tested.
- Failure scenarios and rollback/migration plan.
- Files/modules likely to change.
- Security and financial invariants.

Do not begin implementation while a critical design decision is unresolved. Ask the user only when the decision requires product intent or permission; make reversible engineering choices autonomously when safe.

### Gate 2 — Independent risk and test design

Before or alongside implementation:
- Risk/Security identifies how the feature could lose funds, expose credentials, bypass controls or corrupt state.
- QA writes tests for expected behaviour and failure paths, including negative tests.
- Quant/Research validates any strategy, execution-cost or market-data assumptions.
- Architect checks compatibility and concurrency boundaries.

The tests should encode acceptance criteria, not merely reproduce the implementation's current behaviour.

### Gate 3 — Development

1. Create a dedicated branch from the latest verified `main`.
2. Implement one coherent change per PR.
3. Keep paper/live boundaries explicit; never enable live trading as a side effect.
4. Use deterministic logic for risk decisions and financial calculations.
5. Make persistence, idempotency, auditability and safe failure behaviour explicit.
6. Avoid unrelated refactoring and dependencies.
7. Commit with descriptive messages. Do not write directly to `main`.

Independent work can be split into separate branches/PRs only when changes do not overlap and interfaces are agreed first. The Orchestrator integrates and resolves conflicts.

### Gate 4 — Test and adversarial review

Run the most relevant tests first, then the full backend suite and repository CI. Where applicable include:
- Unit tests for normal and boundary values.
- Integration tests with fake providers/adapters.
- Duplicate request and idempotency tests.
- Failure injection at every persistence/external-call boundary.
- Restart, lease expiry and recovery tests.
- Stale, malformed, missing and non-finite market data.
- Concurrency/race and transaction tests.
- Security tests for auth, permissions, secrets and fail-closed defaults.
- Regression tests for previously implemented features.

The QA role reports exact commands, results and skipped tests. The Reviewer inspects the complete diff independently and may request changes. A green test suite is necessary but not sufficient: review correctness and the safety model.

### Gate 5 — PR and integration

- Open a PR with summary, rationale, tests, risks and safety implications.
- Verify the PR targets the intended base and is not unexpectedly behind/diverged.
- Require CI green on the exact PR head SHA.
- Resolve all blocking review findings.
- Merge only when acceptance criteria and safety gates pass. Never claim a PR is merged until GitHub confirms it.
- Do not deploy, expose services publicly, add paid services or enable live execution without explicit user approval.

### Gate 6 — Close the phase and hand off

After merge:
1. Verify the merge commit and updated `main` SHA.
2. Update `docs/ROADMAP.md` checkboxes based on evidence, not intention.
3. Update `README.md` if user-facing setup, behaviour or limitations changed.
4. Update `docs/SESSION_HANDOFF.md` with completed work, PR/commit, test/CI status, unresolved risks and the next concrete task.
5. Leave the repository in a state where a new session can continue without chat history.

## Required phase report

At the end of a meaningful phase, record:

- **Phase / objective**
- **Planner and architecture decisions**
- **Implementation summary**
- **Risk/security findings and mitigations**
- **Tests run:** exact command and pass/fail result
- **Review findings:** resolved and outstanding
- **PR / merge commit / main SHA**
- **Roadmap and handoff updates**
- **Next phase:** one concrete task, why it is next, files to inspect, acceptance criteria

Do not report a phase as complete if code is only drafted, tests were not run, CI is pending, or the PR is not merged. Clearly distinguish implemented, locally tested, CI-verified, merged and deployed.

## Decision and escalation rules

- Safety and correctness outrank speed and feature breadth.
- When roles disagree, document the disagreement and choose the option with the safer failure mode; ask the user if it changes product intent, cost, permissions or risk appetite.
- Never invent test results, repository state, provider capabilities or deployment status.
- Never use actual exchange credentials or private wallet keys for tests.
- No agent may change its own risk limits, disable the Risk Manager or approve its own live-trading permissions.
- When order/provider state is uncertain, block new entries and reconcile; never retry an ambiguous external side effect blindly.
- Paper execution remains the only operational mode until a separately authorised and independently reviewed live-execution project exists.

## Default instruction for a new ChatGPT session

"Act as Engineering Lead and orchestrate the specialised roles in `docs/AGENT_WORKFLOW.md`. First recover the repository state from `docs/SESSION_HANDOFF.md`, `docs/ROADMAP.md` and live GitHub. Continue the next pending phase; do not repeat merged work. Follow all phase gates, keep planning, development, QA, risk/security and code review as distinct responsibilities, and update the handoff before ending the phase."
