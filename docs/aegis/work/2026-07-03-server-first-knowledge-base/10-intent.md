# Server-first knowledge base execution - Intent

## TaskIntentDraft

- Requested outcome: Execute the first server-first knowledge-base milestone for 考公搭子
- Goal: Create a stable baseline and prepare the server database/schema/import staging path for canonical qbank import
- Success evidence:
- Aegis check passes, repository baseline is committed, server PostgreSQL/schema is active, and qbank import inputs are staged on the server
- Stop condition: Stop when Slice 0-2 are verified, or if server package install/import transfer is blocked
- Non-goals:
- Implementing 行测/申论 full learning loop
- Serving image assets from the API server
- Scope: Repository baseline, server PostgreSQL setup, schema boundaries, qbank import input transfer; no product API implementation in this slice
- Change kinds:
- ops+persistence
- Risk hints:
- Do not commit large data, secrets, local runtimes, or old project code; do not expose PostgreSQL publicly

## BaselineReadSetHint

- docs/aegis/plans/2026-07-03-server-first-knowledge-base-implementation-plan.md
- docs/ops/server-inventory.md
- data/question-bank-export/MANIFEST.md

## BaselineUsageDraft

- Required baseline refs:
- docs/aegis/plans/2026-07-03-server-first-knowledge-base-implementation-plan.md
- docs/ops/server-inventory.md
- data/question-bank-export/MANIFEST.md
- Acknowledged before plan:
- none
- Cited in plan:
- none
- Missing refs:
- docs/aegis/plans/2026-07-03-server-first-knowledge-base-implementation-plan.md
- docs/ops/server-inventory.md
- data/question-bank-export/MANIFEST.md
- Advisory decision: needs-baseline-readback

## ImpactStatementDraft

- Compatibility boundary: No hidden fallback auth/model/user path; no image serving through 2M API host; no staging schema as final runtime owner
- Affected layers:
- ops
- database
- backend
- Owners:
- server-first knowledge base execution
- Invariants:
- Runtime API must read product-owned database tables, not local files or old project runtime state
- Non-goals:
- Implementing 行测/申论 full learning loop
- Serving image assets from the API server

These records are Method Pack drafts / hints, not authoritative runtime decisions.
