# Sub-audit E — FastAPI, PostgreSQL, Alembic and data consistency

Audit mode: read-only application audit  
Audit commit: `01752f1d7b8b606b6900160eebba0888d8def294`  
Application/business/configuration files changed by this sub-audit: **none**  
Allowed audit artifacts created: `audit/subagents/e/**`, `tools/audit/e/**`

## Executive result

Six deduplicated findings are proposed:

| Severity | Count |
|---|---:|
| P0 | 1 |
| P1 | 5 |
| P2 | 0 |
| P3 | 0 |

They separate into:

- **Confirmed runtime/release defects:** `E-001`, `E-002`, `E-004`, and the stale-claim portion of `E-005`.
- **Confirmed repository deployment-topology defect, live production state unverified:** cause-agent portion of `E-005`.
- **Confirmed test-infrastructure/contract drift, not 52 production bugs:** `E-003`.
- **Confirmed anonymous exposure; business-policy severity should be accepted by the product/security owner:** `E-006`.

The strongest systemic causes are:

1. database compatibility has several manually synchronized authorities and historical migrations import mutable runtime code;
2. release immutability rules were added without a replay-compatible writer protocol;
3. a new frozen-content authority was not propagated to shared test data builders;
4. transaction ownership and idempotency are split across HTTP/service/repository layers;
5. background-job production, consumer deployment, lease recovery, and readiness are not governed by one registry.

## Coverage and baseline

- FastAPI routes inventoried: **246**
  - routes with transitive bearer dependency: **225**
  - public or custom-auth routes: **21**
- Alembic revisions: **71**, one base, one linear head `20260728_70`
- Full backend pytest baseline (run by the parent audit): **993 collected; 924 passed; 69 failed; 0 errors**
- Of those 69 failures:
  - **56** are from Git-tracked repository tests;
  - **13** come from `backend/tests/test_release_lifecycle_scripts.py`, which was untracked before the audit and is recorded separately rather than treated as commit-baseline evidence.
- The 56 tracked failures deduplicate exactly to:
  - `E-001`: **2**
  - `E-002`: **2**
  - `E-003`: **52**

The exact node-by-node classification is in `evidence/pytest-failure-clusters.csv`. The 52 `E-003` cases fail in seed/setup, release activation, binding validation, or Xingce task lookup; they do not reach the assertions they were written to test.

No production environment, real user data, real payment, or external object store was accessed. Local write tests used only ephemeral PostgreSQL created and destroyed by the existing pytest fixture.

## Confirmed findings

### E-001 — The release can neither trust one database revision nor replay the audited baseline to head

- **id:** E-001
- **severity:** P0
- **confidence:** high
- **category:** migration / release governance
- **functional domain:** deployment, readiness, PostgreSQL schema compatibility
- **page or business flow:** release activation and application startup/readiness
- **classification:** confirmed runtime/release defect
- **tracked pytest failures grouped here:** 2
- **preconditions:** repository artifact at the audited commit; local PostgreSQL; no production access
- **reproduction steps:**
  1. Run `cd backend && .venv/bin/alembic heads`; observe `20260728_70`.
  2. Compare `backend/app/core/config.py:193` (`_68`), `ops/env.example:176` (`_69`), and `ops/release-db-compatibility.env:3` (`_70`).
  3. Inspect `ops/scripts/activate_release.sh:83-97`; it rejects any configured revision unequal to the artifact head.
  4. Run the audited-baseline migration test recorded in `evidence/failure-release-and-migration-targeted.log`.
- **expected behavior:** all runtime/deployment/test authorities resolve to the artifact head, and the audited baseline upgrades through every revision to that head.
- **actual behavior:** revision declarations disagree, and the real upgrade stops at `20260716_33` because that historical migration imports current `MockExamRepository` code whose method now requires `knowledge_release`.
- **user impact:** release activation can be blocked before cutover; `/ready` can reject a correctly migrated database when runtime metadata is stale; an environment at the documented baseline cannot be upgraded with the current artifact.
- **evidence:**
  - `evidence/revision-drift.txt`
  - `evidence/failure-release-and-migration-targeted.log`
  - `evidence/migrations-import-runtime-code.txt`
  - `migration-audit.md`
  - `backend/migrations/versions/20260716_33_stage6_mock_reporting.py:58-90`
- **inferred root cause:** revision strings are manually copied, historical migrations depend on mutable application interfaces, and the general test database is a shadow schema rather than a real head upgrade.
- **affected pages/modules:** all API and worker deployment; `/ready`; rollback/activation scripts; migration CI; any feature relying on revision `_70` data.
- **recommended repair direction:** Wave 0. Generate one artifact compatibility authority; consume it everywhere; make historical transforms migration-local or permanently versioned; require audited-baseline → head PostgreSQL replay in CI and release preflight.
- **recommended regression tests:** baseline → head; exact equality across settings/artifact/ops/readiness; application smoke at head; schema fingerprint comparison between ordinary fixture and real upgrade.
- **status:** open; no fix attempted.

### E-002 — Released method-card content rejects an exact idempotent replay

- **id:** E-002
- **severity:** P1
- **confidence:** high
- **category:** release lifecycle / data consistency
- **functional domain:** method cards, knowledge-map publishing, Companion knowledge
- **page or business flow:** content import, repeat publication, deployment retry
- **classification:** confirmed runtime defect
- **tracked pytest failures grouped here:** 2
- **preconditions:** a method card is referenced by an active or retired knowledge-map release.
- **reproduction steps:**
  1. Insert a method card, create a release/node referencing it, and activate the release.
  2. Call `MethodCardRepository.upsert_cards` with the byte-for-byte same logical card.
  3. Or run the targeted test in `evidence/failure-release-and-migration-targeted.log`.
- **expected behavior:** exact replay succeeds without changing `updated_at`; changed content under the same ID is rejected.
- **actual behavior:** the `BEFORE INSERT` trigger raises `method cards referenced by published knowledge releases are immutable` before PostgreSQL can resolve `ON CONFLICT` and its equality predicate. Even `ON CONFLICT DO NOTHING` is blocked.
- **user impact:** safe retries and repeat imports fail after publication; automated release/refresh recovery becomes non-idempotent and can strand content rollout.
- **evidence:**
  - `evidence/failure-release-and-migration-targeted.log`
  - `audit/baseline/backend-pytest.log`
  - `backend/migrations/versions/20260727_66_knowledge_release_immutability.py:266-303`
  - `backend/app/domains/method_cards/repository.py:67-169`
- **inferred root cause:** reader immutability was enforced at the table's `INSERT` trigger boundary while the writer still represents replay as a new insert attempt.
- **affected pages/modules:** `scripts/import_method_cards.py`, `MethodCardRepository`, knowledge-map publishers, tests/helpers that safely replay shared cards.
- **recommended repair direction:** Wave 1. Define a versioned command/replay protocol. Resolve the existing row before immutable checks or use a stored procedure that returns success only for an exact canonical fingerprint and rejects drift.
- **recommended regression tests:** exact replay preserves timestamps; changed payload fails; concurrent exact replays converge; retired and active releases behave identically; publisher retry after a committed prior run succeeds.
- **status:** open; no fix attempted.

### E-003 — Frozen-content contract drift prevents 52 core tests from reaching their assertions

- **id:** E-003
- **severity:** P1
- **confidence:** high
- **category:** test infrastructure / contract governance
- **functional domain:** planning, calibration, Xingce attempts, cause analysis, media, settlement, briefing, dispute and work-package acceptance
- **page or business flow:** backend regression gate for core Xingce workflows
- **classification:** confirmed test-infrastructure defect; **not evidence of 52 runtime bugs**
- **tracked pytest failures grouped here:** 52
- **preconditions:** run the tracked backend suite against its standard ephemeral PostgreSQL fixture.
- **reproduction steps:**
  1. Run the three tests in `evidence/failure-shared-planning-release-gate.log`.
  2. Observe a 200 plan response containing only the essay task; shared helpers raise `StopIteration` looking for Xingce.
  3. Run the media/timing tests in `evidence/failure-media-and-timing-targeted.log`; they fail at the same helper.
  4. Inspect `_seed_shared_objective_question_bank` in `backend/tests/test_planning_routes.py:1748`; it seeds only `product.questions` and options.
  5. Inspect `XingceAttemptRepository.select_frozen_questions` and `_select_eligible_questions`; selection now requires an active released vocabulary, release-scoped bindings, exactly one content snapshot, and delivery eligibility.
- **expected behavior:** shared builders construct data satisfying the current release authority so each test reaches and verifies its own behavior.
- **actual behavior:** builders model the old mutable question-table contract. Planning correctly fails closed and omits Xingce, or newer binding triggers reject outdated seed operations. Fifty-two tests stop before their intended assertions.
- **user impact:** CI cannot distinguish new core regressions from fixture noise; attempt submission, result semantics, cause-agent billing, media atomicity, dispute isolation, and acceptance flows currently lack effective regression execution. This directly explains why manual testing can continue finding defects despite a large test count.
- **evidence:**
  - `evidence/pytest-failure-clusters.csv`
  - `evidence/failure-shared-planning-release-gate.log`
  - `evidence/failure-media-and-timing-targeted.log`
  - `evidence/failure-calibration-targeted.log`
  - `backend/app/domains/xingce_learning/real_attempt/repository.py:166-199`
  - `backend/app/domains/xingce_learning/real_attempt/repository.py:304-420`
- **inferred root cause:** the production content authority changed atomically in code, but reusable test factories and acceptance fixtures were not versioned or migrated with it.
- **affected pages/modules:** 52 test node IDs listed in the evidence CSV; no claim is made that all corresponding production modules are broken.
- **recommended repair direction:** Wave 0. Create one canonical frozen-release test factory that publishes vocabulary, content versions, nodes, bindings, method cards/assets, and activation facts. Make older builders delegate to it. Preserve explicit negative factories for stale/invalid releases.
- **recommended regression tests:** first require all 52 classified cases to reach their intended assertions; add a factory self-test for release fingerprint, binding authority, content snapshot, and delivery eligibility; fail if selection silently falls back to legacy `product.questions`.
- **status:** open; no test or application fix attempted.

### E-004 — Answer/verdict replay duplicates progression and commits one logical command in several transactions

- **id:** E-004
- **severity:** P1
- **confidence:** high
- **category:** idempotency / transaction boundary / data consistency
- **functional domain:** legacy review session, practice answers, official rank/progression
- **page or business flow:** submit answer → persist result → award XP/fame → rank event/promotion
- **classification:** confirmed runtime defect
- **tracked baseline failures grouped here:** 0; found by audit probe
- **preconditions:** authenticated user and answerable review card.
- **reproduction steps:**
  1. POST the same body twice to `/api/v1/review-sessions/{docket}/cards/{card}/verdict`.
  2. Query `learner.verdicts`, `learner.official_ranks`, and `learner.rank_events`.
  3. The executable audit probe is `test_review_verdict_replay_awards_xp_twice` in `tools/audit/e/test_confirmed_backend_risks.py`.
- **expected behavior:** retry returns the original command result and performs no second business side effect; the answer and progression facts commit atomically or through an atomic fact plus idempotent outbox.
- **actual behavior:** the verdict upsert returns the same `verdict_id`, but two calls produce one verdict row, XP `20`, and two `xp_gain` events. Verdict, docket-card, XP, active date, rank event, and promotion repositories commit independently. The practice-answer endpoint likewise inserts and commits before separately awarding progression and exposes no idempotency key.
- **user impact:** network retries can inflate rank/XP; an exception after the first commit can show an answer as stored while progression failed, or progression can be replayed without a new logical answer.
- **evidence:**
  - `evidence/confirmed-risk-probes.log`
  - `evidence/domain-files-with-explicit-commits.txt`
  - `backend/app/domains/review_session/service.py:53-60`
  - `backend/app/domains/review_session/repository.py:79-151`
  - `backend/app/domains/practice/router.py:75-112`
  - `backend/app/domains/rank_system/repository.py:72-198`
- **inferred root cause:** command identity is absent, and lower-layer repositories own commits rather than one use-case transaction.
- **affected pages/modules:** generated v1 practice/review APIs; `learner.verdicts`, `learner.practice_answers`, `learner.official_ranks`, `learner.rank_events`, docket cards.
- **recommended repair direction:** Wave 1. Require an idempotency key and immutable command record; move commits to a single unit-of-work; publish progression through an idempotent event/outbox keyed by the answer command.
- **recommended regression tests:** exact retry, same-key/different-payload conflict, timeout after durable answer, injected rank-event failure, concurrent duplicate submit, and invariant counts after every failure point.
- **status:** open; no fix attempted.

### E-005 — Background jobs lack one enforceable consumer/lease/recovery contract

- **id:** E-005
- **severity:** P1
- **confidence:** high for repository behavior; production incident state unverified
- **category:** asynchronous state machine / deployment topology / privacy consistency
- **functional domain:** data export/account deletion, iLink media deletion, Xingce cause analysis
- **page or business flow:** enqueue → claim → external/durable work → completion/retry
- **classification:** confirmed repository and standard-deployment defect
- **tracked baseline failures grouped here:** 0; found by audit probe and topology inventory
- **preconditions:** worker process exits after committing a claim, or repository release is deployed through `ops/scripts/activate_release.sh`.
- **reproduction steps:**
  1. Enqueue and claim a data-rights job, commit the `running` claim, advance audit time seven days, and call `DataRightsWorker.process_next`.
  2. Observe the worker returns `None`; the job remains `running`, with its claim token and attempt count unchanged. See the second audit probe.
  3. Inspect iLink deletion: it commits `state='deleting'` before object deletion, but future claims select only `pending`/`failed`.
  4. Inspect cause analysis: `CauseAgentWorkflow` enqueues `xingce_cause_agent_v1`; a Python entrypoint exists; there is no corresponding systemd unit or activation `runtime_units` entry. The entrypoint also never calls the worker's existing `recover_stale_claims`.
- **expected behavior:** every produced job type has a deployed consumer, lease timeout, automatic recovery on every poll/start, readiness/metrics ownership, and an invariant-preserving retry contract.
- **actual behavior:** those capabilities are optional and domain-specific. Data-rights and iLink committed claims can become permanently invisible. The repository-defined release does not start the cause-agent consumer.
- **user impact:** export/deletion requests or object cleanup can remain pending indefinitely after a crash; cause-analysis/reflection can remain queued and never return a result.
- **evidence:**
  - `evidence/confirmed-risk-probes.log`
  - `backend/app/platform/jobs.py:37-72`
  - `backend/app/domains/data_rights/worker.py:37-48`
  - `backend/app/domains/ilink_channel/repository.py:826-871`
  - `backend/app/workers/ilink_channel.py:127-149`
  - `backend/app/domains/xingce_learning/cause_agent/service.py:29`
  - `backend/app/workers/xingce_cause_agent.py:11-25`
  - `ops/scripts/activate_release.sh:133-175`
  - absence of a cause-agent unit in `ops/systemd/`
- **inferred root cause:** there is no build-time registry joining job producer, consumer unit, lease duration, stale recovery, readiness and alert policy.
- **affected pages/modules:** privacy exports/deletions, iLink photos/query media, Xingce cause-candidate workflow, background-job metrics.
- **recommended repair direction:** Wave 1. Introduce a job-type registry and deployment assertion; use leased claims with `lease_expires_at`; recover before every claim; add the cause-agent service unit and call recovery; expose per-type queue age/running age in readiness/metrics.
- **recommended regression tests:** kill after claim/after external call/before completion; restart and converge once; deployment manifest asserts every produced type has one active consumer; stale `deleting` iLink object is reclaimed; cause job progresses under the exact release topology.
- **status:** open; live production worker state is an audit blocker, not assumed.

### E-006 — Anonymous knowledge routes return answer keys and explanations before task submission

- **id:** E-006
- **severity:** P1
- **confidence:** high for exposure; product/security owner should confirm intended public-content policy
- **category:** authorization / content integrity
- **functional domain:** question corpus and practice integrity
- **page or business flow:** anonymous question search/detail versus authenticated answer submission
- **classification:** confirmed exposure
- **tracked baseline failures grouped here:** 0; existing route tests currently assert the exposure
- **preconditions:** network access to the API; no account or bearer token.
- **reproduction steps:**
  1. GET `/api/v1/knowledge/questions/search?domain=objective&limit=100` without `Authorization`.
  2. Observe each summary contract contains `questionId`, stem and `answer`.
  3. GET `/api/v1/knowledge/questions/{questionId}` without `Authorization`.
  4. Observe answer, options, preferred analysis, occurrences, tags/chunks/assets.
  5. The existing no-auth contract tests are recorded in `evidence/anonymous-content-contract-tests.log`.
- **expected behavior:** learner delivery is authenticated and stage-aware; pre-submit projections omit answer keys and explanations. Bulk corpus/admin reads require a separate authorization scope.
- **actual behavior:** both routes have only database/service dependencies. Search accepts up to 100 rows and directly selects `q.answer`; detail has no status or authentication filter and serializes the full answer/explanation contract.
- **user impact:** an anonymous caller can extract governed learning content and bypass practice answer timing, compromising question-bank value and assessment integrity.
- **evidence:**
  - `evidence/fastapi-route-auth-inventory.csv`
  - `evidence/anonymous-content-contract-tests.log`
  - `backend/app/domains/knowledge/router.py:22-56`
  - `backend/app/domains/knowledge/schemas.py:65-119`
  - `backend/app/domains/knowledge/repository.py:235-326`
- **inferred root cause:** an internal/content-browser schema is mounted in the public API without a delivery-stage projection or auth policy.
- **affected pages/modules:** two v1 knowledge routes, generated OpenAPI clients, all active objective content returned through them.
- **recommended repair direction:** Wave 1. Decide and encode route policy; split public safe projection from privileged corpus detail; require learner/admin auth as appropriate; never expose answer/explanation until an authorized attempt reaches the allowed state.
- **recommended regression tests:** anonymous 401/403 or safe projection; authenticated pre-submit response has no answer; post-submit scoped response has answer; bulk enumeration rate/permission tests; inactive/draft IDs cannot be fetched.
- **status:** open; gateway-level production blocking was not verified.

## High-probability issues and hypotheses

Only `E-006` has a remaining policy question; the technical exposure is confirmed. Performance/rate-limit/timing possibilities and the lack of live production inspection are kept out of formal findings in `hypotheses.md`.

## Audit blockers and uncovered scope

- No production PostgreSQL connection, schema diff, row counts, worker queue state, systemd state, CDN/WAF policy, real payment, or real object storage.
- No full declarative ORM comparison is possible because Alembic has `target_metadata = None` and repositories use raw SQL.
- The legacy baseline is intentionally provisioned outside Alembic; blank-database upgrade is not a supported validity test.
- Full destructive downgrade was not attempted; dedicated downgrade guards/tests were reviewed instead.
- The 13 failures from the pre-existing untracked lifecycle test file are not claimed as repository-baseline failures.
- Pre-existing modified files in `ops/` were not changed by this audit. Where used as supporting evidence, they are labeled working-tree evidence; `E-001` is independently confirmed by tracked settings/env/artifact values.

## Positive controls observed

- Alembic topology has one base and one head.
- Core v2 learner routes generally carry transitive bearer dependencies.
- Internal runtime endpoints use release-pinned tokens and `hmac.compare_digest`; support and payment callbacks have custom authorization/signature mechanisms rather than bearer auth.
- Several workers (external learning, learning media, coach notifications, essay review) explicitly recover stale claims, proving the shared job repository supports the required behavior.
- Modern v2 Xingce commands generally require idempotency headers and frozen content; the confirmed duplicate progression path is concentrated in retained v1 endpoints.
- Full-suite server-setup schema contract tests passed; the defect is that this does not substitute for the failing end-to-end migration replay.

## Evidence index

- `evidence/alembic-history.txt` — 71-revision linear graph
- `evidence/revision-drift.txt` — current revision authorities
- `evidence/migration-inventory.csv` — revision lineage, fixture references, runtime imports
- `evidence/migrations-import-runtime-code.txt` — historical migration imports from `app.*`
- `evidence/failure-release-and-migration-targeted.log` — method-card replay and baseline upgrade failures
- `evidence/pytest-failure-clusters.csv` — all 69 failures, Git tracking state and shared-root classification
- `evidence/failure-shared-planning-release-gate.log` — three representative `E-003` failures
- `evidence/failure-media-and-timing-targeted.log` — three more `E-003` failures
- `evidence/failure-calibration-targeted.log` — calibration representative
- `evidence/confirmed-risk-probes.log` — duplicate XP and seven-day stale data-rights claim
- `evidence/fastapi-route-auth-inventory.csv` — 246-route dependency inventory
- `evidence/anonymous-content-contract-tests.log` — no-auth knowledge/legacy route contract tests
- `evidence/domain-files-with-explicit-commits.txt` — transaction-boundary review inventory

## Recommended ordering

- **Wave 0:** `E-001`, `E-003`. Restore an executable migration/release authority and make the core suite meaningful.
- **Wave 1:** `E-002`, `E-004`, `E-005`, `E-006`. Repair systemic replay, transaction, worker, and authorization boundaries.
- **Wave 2+:** no backend/database-specific item from this sub-audit should be scheduled ahead of Wave 0/1 based solely on this report.

Stop condition reached: evidence and repair direction recorded; no application fix was made.
