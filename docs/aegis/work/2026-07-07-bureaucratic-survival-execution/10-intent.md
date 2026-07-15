# Bureaucratic Survival Execution Intent

Date: 2026-07-07

## Requested Outcome

Execute the approved bureaucratic survival implementation plan inline, one slice at a time.

## Active Goal

First slice only: stabilize the backend test baseline so full `backend/tests` passes before adding the new `docket` owner.

## Scope

- Repair existing backend contract/test failures.
- Preserve current product assets and existing user/project changes.
- Do not create `docket` feature code until the baseline is green.

## Non-Goals

- No production deployment.
- No database destructive operation.
- No old-path deletion in this slice.
- No iOS feature work in this slice.
- No git commit unless explicitly requested.

## BaselineUsageDraft

- Required baseline refs: `docs/aegis/plans/2026-07-07-bureaucratic-survival-implementation-plan.md`, `docs/aegis/specs/2026-07-07-bureaucratic-survival-architecture-plan.md`, failing backend tests, relevant backend routers/services/schema.
- Acknowledged before plan refs: current backend test failures, current dirty worktree, no separate worktree because much of current project state is untracked.
- Cited in execution refs: Task 1 from the implementation plan.
- Missing refs: none blocking.
- Decision: continue.

## ImpactStatementDraft

- Affected layers: backend test schema, mock exam submission, route tests for rank side effects.
- Compatibility: no production API contract removal; test expectations may update for explicit `moduleName`.
- Risk: fixing tests can reveal deeper rank/progression ownership issues.
