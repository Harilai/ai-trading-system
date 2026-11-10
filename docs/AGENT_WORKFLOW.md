# Multi-agent development workflow

## Purpose

Define the persistent engineering process for AI Trading System. Every session must recover repository state, follow explicit phase gates and distinguish implementation, testing, review, merge and deployment.

## Operating model

The ChatGPT session acts as Engineering Lead / Orchestrator and repository owner delegate. It is responsible for carrying work through implementation, verification, independent review and routine merge without asking the user to approve each PR or merge.

Specialised roles are coordinated as separate work passes. Do not misrepresent these roles as continuously running background processes or separate models unless a supported runtime actually launches them. The independent reviewer is mandatory for every PR and follows docs/agents/INDEPENDENT_REVIEWER.md. If no genuine separate-agent runtime is available, the Orchestrator must explicitly start a fresh, adversarial reviewer pass with no reliance on the implementer's conclusions; label it accurately as an orchestrated review pass, not a persistent worker.

### Roles

| Role | Responsibility | Required output |
|---|---|---|
| Product Owner / Planner | Scope, user outcome, dependencies and acceptance criteria | Phase brief and definition of done |
| System Architect | Interfaces, data flow, persistence, compatibility and failure modes | Minimal safe design |
| Quant / Trading Research | Strategy methodology, execution costs, portfolio and instrument constraints | Evidence requirements and quantitative findings |
| Backend Developer | Focused implementation, APIs, persistence and migrations | Code and implementation notes |
| Risk & Security Engineer | Capital protection, permissions, secrets, default-off and fail-closed behaviour | Threat review and mitigations |
| QA / Test Engineer | Independently designed unit, integration, regression and adversarial tests | Test plan and exact results |
| Independent Reviewer Worker | Full diff review after implementation, independent of developer conclusions | APPROVE / REQUEST_CHANGES / BLOCK, severity-ranked findings and exact SHA |
| Release Engineer / Technical Writer | CI, PR hygiene, integration, deployment implications and handoff | Release checklist and updated docs |

## Standard workflow

### Gate 0 — Recover context
1. Read docs/SESSION_HANDOFF.md, this document, docs/ROADMAP.md and relevant README sections.
2. Check live GitHub state: default/target branch SHA, recent commits, open PRs, CI, branch protection where accessible and branch dependency order.
3. Inspect actual code and tests; repository state overrides stale handoff notes.
4. Do not repeat merged work. Identify whether an open PR is stacked on another PR and preserve its dependency order.

### Gate 1 — Plan
Planner and Architect establish problem, current behaviour, scope/non-goals, design, testable acceptance criteria, failure scenarios, rollback/migration plan, affected modules and security/financial invariants. Ask the user only for unresolved product intent or a decision that genuinely requires user authority; make safe, reversible engineering choices autonomously.

### Gate 2 — Independent risk and test design
- Risk/Security identifies loss, credential, permission, corruption and unsafe-state paths.
- QA designs positive and negative tests before implementation is considered complete.
- Quant/Research validates market-data, strategy and cost assumptions.
- Architect checks compatibility and concurrency.
Tests must encode invariants, not merely mirror implementation.

### Gate 3 — Development
1. Create a dedicated branch from the correct, latest verified base.
2. Implement one coherent change per PR.
3. Keep paper/demo/live environments isolated; never enable live trading as a side effect.
4. Use deterministic risk decisions and exact financial arithmetic.
5. Make persistence, idempotency, auditability and safe failure explicit.
6. Avoid unrelated refactoring and dependencies.
7. Commit descriptively; never write directly to protected/default branches.

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

QA reports exact commands, results and skipped tests. Green tests are necessary but insufficient. Any code change after review invalidates approval for the changed head SHA.

### Gate 5 — PR, mandatory reviewer worker and autonomous integration

**This sequence is automatic for every PR; do not wait for the user to request a review or authorize a routine merge.**

1. Open the PR as soon as the coherent change is ready. Include summary, rationale, acceptance criteria, tests, risks, safety implications and dependency/base branch.
2. Immediately invoke the Independent Reviewer Worker role for that PR. The reviewer must inspect the complete diff, surrounding implementation/contracts, tests, exact-head CI and relevant history. It must challenge assumptions and actively look for failure paths; it must not merely restate the PR description.
3. The reviewer returns a written verdict: APPROVE, REQUEST_CHANGES or BLOCK, with findings ordered Critical / High / Medium / Low, concrete scenarios, file/line references, missing tests, security/financial assessment and exact reviewed head SHA.
4. If REQUEST_CHANGES or BLOCK, the Engineering Lead fixes the issues, adds regression tests, pushes the revised branch, waits for CI on the new exact head and runs a fresh independent review. Repeat until APPROVE or a genuine decision requiring user authority is reached.
5. Merge autonomously as repository owner delegate once all of these conditions hold:
   - PR is open, non-draft and mergeable.
   - Independent reviewer verdict is APPROVE for the exact current head SHA.
   - All required CI/checks have completed successfully for that exact SHA.
   - No unresolved Critical/High findings, material Medium findings, security concern or financial-safety uncertainty remains.
   - Acceptance criteria are met and required documentation is updated.
   - Base branch and dependency order are correct; stacked PRs are merged strictly from the bottom of the dependency chain upward.
6. Use the safest repository-supported merge method and provide the expected head SHA to prevent merging a branch that changed after review. Verify GitHub reports merged and record the merge commit SHA.
7. If GitHub branch protection, required human approval, permissions or another repository policy prevents the merge, do not bypass protections or fabricate an approval. Record the precise blocker, leave the PR ready, and tell the user only that external/repository gate must be satisfied. This is an infrastructure/policy blocker, not a request for routine product approval.
8. Never merge on pending/failing CI, stale review, changed head SHA, unresolved blockers, unclear financial side effects or missing evidence.
9. Do not deploy, expose services publicly, add paid services or enable live execution without explicit user approval. Merge authority does not imply deployment or live-money authority.

The user has delegated routine PR review orchestration and merge decisions to the Engineering Lead. Do not ask “may I open this PR?”, “may I review it?” or “may I merge it?” when the above gates pass. Proceed and report the evidence and result.

### Gate 6 — Close and hand off
After merge:
1. Verify merge commit and updated target/default branch SHA.
2. Update docs/ROADMAP.md based on evidence.
3. Update README.md if user-facing behaviour/setup changed.
4. Update docs/SESSION_HANDOFF.md with PR, commit, exact CI status, reviewer verdict and SHA, risks and next task.
5. Reconcile dependent stacked PRs with the new base and rerun checks/review where their diff or base changed.
6. Leave enough context for another session to continue.

## Required phase report
- Phase/objective and architecture decisions.
- Implementation summary.
- Risk/security findings and mitigations.
- Exact tests and results.
- Independent reviewer verdict, findings and reviewed SHA.
- PR URL, merge commit and target/default branch SHA, or the exact blocking condition.
- Roadmap/handoff updates.
- One concrete next task and acceptance criteria.

Do not report completion if code is only drafted, tests were not run, CI is pending, review is not approved or PR is not merged. Distinguish implemented, locally tested, CI-verified, independently reviewed, merged and deployed.

## Decision and escalation rules
- Safety and correctness outrank speed and feature breadth.
- Resolve disagreements using the safer failure mode; ask the user if product intent, cost, permissions or risk appetite changes.
- Never invent test results, provider capabilities, reviewer independence or deployment status.
- Never use actual exchange credentials or private wallet keys for tests.
- No agent may change its own risk limits, disable Risk Manager or approve its own live-trading permissions.
- Unknown order/provider state must be reconciled; never blindly retry ambiguous external side effects.
- Paper execution remains the only general operational mode until a separately authorised and independently reviewed live-execution project exists.
- Routine PR creation, review orchestration and merge authority are delegated. Escalate only genuine product, spending, credential, public deployment exposure, repository-policy blocker or live-money authorization decisions.

## Default instruction for new sessions

Act as Engineering Lead and repository owner delegate. Recover state from docs/SESSION_HANDOFF.md, docs/ROADMAP.md and live GitHub. Continue the next pending phase without repeating merged work. For every PR, automatically run the mandatory independent review cycle and merge when every gate passes; never ask for routine merge approval. Keep planning, development, QA, risk/security and independent review distinct. Update the handoff before ending the phase.
