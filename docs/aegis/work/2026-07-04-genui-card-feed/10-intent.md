# GenUI card feed backend phase 1 - Intent

## TaskIntentDraft

- Requested outcome: Implement /api/v1/feed/today with closed card schemas and rule orchestration backed by existing taskstream data.
- Goal: Add backend feed endpoint and tests without changing planner scheduling.
- Success evidence:
- Feed route returns rules_v1 cards; 14 payload schemas validate; tests cover review priority, budget, honest no-data downgrade, and taskstream task consistency.
- Stop condition: done when targeted backend tests pass; blocked if existing taskstream contract lacks required data; scope-exceeded if planner or iOS changes become necessary.
- Non-goals:
- No iOS renderer changes; no LLM coach text generation; no new planner schedule logic.
- Scope: backend feed domain, API router registration, backend tests, README/Aegis evidence
- Change kinds:
- api-contract
- Risk hints:
- Hot-path endpoint must not call LLM and must not invent unsupported cards.

## BaselineReadSetHint

- docs/aegis/specs/2026-07-04-genui-card-feed-spec.md
- backend/app/domains/taskstream/service.py
- backend/app/domains/taskstream/schemas.py
- backend/app/domains/taskstream/router.py

## BaselineUsageDraft

- Required baseline refs:
- docs/aegis/specs/2026-07-04-genui-card-feed-spec.md
- backend/app/domains/taskstream/service.py
- backend/app/domains/taskstream/schemas.py
- backend/app/domains/taskstream/router.py
- Acknowledged before plan:
- none
- Cited in plan:
- none
- Missing refs:
- docs/aegis/specs/2026-07-04-genui-card-feed-spec.md
- backend/app/domains/taskstream/service.py
- backend/app/domains/taskstream/schemas.py
- backend/app/domains/taskstream/router.py
- Advisory decision: needs-baseline-readback

## ImpactStatementDraft

- Compatibility boundary: Old taskstream today endpoint remains unchanged for one client version.
- Affected layers:
- backend-api
- Owners:
- backend/app/domains/feed
- Invariants:
- Feed task cards match old /api/v1/taskstream/today tasks for the same user.
- Non-goals:
- No iOS renderer changes; no LLM coach text generation; no new planner schedule logic.

These records are Method Pack drafts / hints, not authoritative runtime decisions.

## BaselineUsageDraft

- Required baseline refs:
- docs/aegis/specs/2026-07-04-genui-card-feed-spec.md
- backend/app/domains/taskstream/router.py
- backend/app/domains/taskstream/service.py
- backend/app/domains/taskstream/schemas.py
- Delivered context refs:
- none
- Acknowledged before plan:
- docs/aegis/specs/2026-07-04-genui-card-feed-spec.md
- backend/app/domains/taskstream/router.py
- backend/app/domains/taskstream/service.py
- backend/app/domains/taskstream/schemas.py
- Cited in plan:
- docs/aegis/specs/2026-07-04-genui-card-feed-spec.md
- Missing refs:
- none
- Advisory decision: continue
