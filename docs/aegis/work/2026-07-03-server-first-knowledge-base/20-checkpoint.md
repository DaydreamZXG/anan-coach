# Server-first knowledge base execution - Checkpoint

- Task ID: 2026-07-03-server-first-knowledge-base
- Current todo: Slice 0: establish baseline and server posture
- Active slice: Slice 0
- Blocked on: none
- Next step: Create repository baseline commit and verify server readiness

## Checkpoint Update

- Current todo: Slice 1: install PostgreSQL and create hard server boundaries
- Active slice: Slice 1
- Completed todos:
- Slice 0: repository baseline and full verification
- Evidence refs:
- scripts/full_verify.sh passed
- git commit fe1ba0b
- Blocked on: none
- Next step: Install PostgreSQL 16, create database roles/schemas, verify local-only posture

## DriftCheckDraft

- Scope status: Slice 0 stayed inside baseline/server-posture scope
- Compatibility status: No secrets or large data committed; ignored dump/files/local runtimes remain out of git
- Retirement status: Old project remains non-runtime; preview ownership not touched in this slice
- New risk signals:
- none
- Advisory decision: continue

## Checkpoint Update

- Current todo: Slice 2: transfer qbank import inputs to server staging
- Active slice: Slice 2
- Completed todos:
- Slice 0: repository baseline and full verification
- Slice 1: PostgreSQL/pgvector/schema/server boundary setup
- Evidence refs:
- server PostgreSQL verification: active/vector/schemas/local-only
- Blocked on: none
- Next step: Create import-staging/qbank and rsync dump, SQL, reports, and manifest

## DriftCheckDraft

- Scope status: Slice 1 stayed within server database setup
- Compatibility status: PostgreSQL local-only; credentials stored in root-only server env file, not repo
- Retirement status: Staging/runtime schema split established; no old runtime owner introduced
- New risk signals:
- none
- Advisory decision: continue

## Checkpoint Update

- Current todo: Next planned slice: restore source dump and build canonical_qbank audit schema on server
- Active slice: handoff after first milestone
- Completed todos:
- Slice 0: repository baseline and full verification
- Slice 1: PostgreSQL/pgvector/schema/server boundary setup
- Slice 2: qbank import inputs staged on server
- Evidence refs:
- dump checksum 7d07c3296f047ffa038d56dcefc406fbc6ad78ac3a9bc57144e4c24ef9e572f5 verified on server
- server staging size 309M and root disk 53G available
- Blocked on: none
- Next step: Run pg_restore into staging/source schema, then run canonical-qbank-v1.sql and verify import_summary counts

## DriftCheckDraft

- Scope status: Slice 2 stayed inside import-input staging scope
- Compatibility status: Only dump/SQL/reports/manifest transferred; image/file assets were not placed behind API server
- Retirement status: Local Mac is no longer required for heavy qbank import; staging remains non-runtime
- New risk signals:
- none
- Advisory decision: continue

## Checkpoint Update

- Current todo: Next planned slice: create product knowledge tables and importer from canonical_qbank
- Active slice: handoff after canonical qbank milestone
- Completed todos:
- Slice 0: repository baseline and full verification
- Slice 1: PostgreSQL/pgvector/schema/server boundary setup
- Slice 2: qbank import inputs staged on server
- Slice 3: source dump restored and canonical_qbank v1 rebuilt/verified on server
- Evidence refs:
- scripts/server_restore_canonical_qbank.sh matched 12 expected counts
- remote smoke: public old source tables 0, postgres local-only, disk 50G available
- Blocked on: none
- Next step: Implement product schema/importer/API slice from canonical_qbank to product tables

## DriftCheckDraft

- Scope status: Slice 3 stayed inside canonical qbank audit rebuild scope
- Compatibility status: Runtime API still does not read staging schemas; PostgreSQL remains local-only; no image assets served through API host
- Retirement status: Locale-dependent candidate recomputation retired from canonical v1 path; approved CSV artifacts are now the merge authority
- New risk signals:
- Product runtime import and API are still pending Slice 4/5
- Advisory decision: continue

## Checkpoint Update

- Current todo: Next planned slice: build learner/taskstream/practice APIs and connect iOS to real backend
- Active slice: handoff after product knowledge API milestone
- Completed todos:
- Slice 4: product runtime knowledge import
- Slice 5: source-grounded knowledge question API deployed and smoke-tested
- Evidence refs:
- slice4-product-knowledge-import
- slice5-knowledge-api-tests-and-smoke
- Blocked on: none
- Next step: Implement backend taskstream/practice contract using product knowledge search/detail, then replace iOS preview question data with real API calls.

## DriftCheckDraft

- Scope status: Slice 4/5 stayed inside product knowledge import and API scope.
- Compatibility status: Runtime API reads product.* tables through backend knowledge repository; PostgreSQL remains local-only; asset response suppresses local file paths and does not serve images through the 2M API host.
- Retirement status: Preview routes still exist only as non-authoritative shell contracts; runtime question detail/search no longer depend on staging schemas.
- New risk signals:
- Taskstream/practice/essay routes and iOS real backend integration remain pending before initial app goal is complete.
- Advisory decision: continue

## Checkpoint Update

- Current todo: Initial app candidate reached; remaining work is cleanup/commit and explicit residual risk handoff.
- Active slice: completion candidate for initial iOS/iPad app loop
- Completed todos:
- Slice 6: backend taskstream and 行测 practice answer loop
- Slice 7: backend 申论 AI review/rewrite loop with quota and audit
- Slice 8: iOS/iPad AppState reads real backend taskstream, practice, and essay contracts
- Evidence refs:
- slice6-practice-essay-ios-loop
- Blocked on: none
- Next step: Commit current backend+iOS loop work, then run final git status and report residual gaps: Apple auth and StoreKit products are not wired yet.

## DriftCheckDraft

- Scope status: Work still serves initial app goal: real taskstream, real qbank-backed 行测 answer loop, and first-class 申论 review/rewrite loop.
- Compatibility status: No runtime route reads source_qbank/canonical_qbank. AI calls are feature-scoped, quota-gated, and audited. iOS uses backend contracts and does not silently fall back to PreviewData for 今日 execution.
- Retirement status: PreviewData remains only for static workbench/preview surfaces; 今日 execution path has moved to real backend AppState.
- New risk signals:
- Apple Sign In, StoreKit transaction verification, durable authenticated learner identity, and richer iPad interaction polish remain after this initial app candidate.
- Advisory decision: continue
