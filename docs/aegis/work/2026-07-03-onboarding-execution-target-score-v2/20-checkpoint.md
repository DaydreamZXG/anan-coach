# Onboarding target-score v2 cohort - Checkpoint

- Task ID: 2026-07-03-onboarding-execution-target-score-v2
- Current todo: Define first execution slice.
- Active slice: initial
- Blocked on: none
- Next step: Read baseline refs and start the next safe slice.

## Checkpoint Update

- Current todo: Continue spec execution after target-score v2 slice
- Active slice: target-score-v2
- Completed todos:
- Implemented positions cohort target-score v2 response and onboarding consumption
- Evidence refs:
- target-score-v2-tests
- Blocked on: none
- Next step: Next safe backend slice: decide whether to implement near-5-year tag weights or mock exam APIs; avoid starting section 9-11 without a narrower plan.

## DriftCheckDraft

- Scope status: Stayed inside positions target-score API and onboarding target score derivation.
- Compatibility status: Added response fields and endpoint; existing search/profile contracts retained with targetScore now governed by v2 basis.
- Retirement status: Retired old single-position average+3 computation from PositionService target score path.
- New risk signals:
- L2 says adjacent counties, but current product.positions schema has no adjacency/county graph; implementation uses same city/system/similar hard conditions.
- Advisory decision: continue
