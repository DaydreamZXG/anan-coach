# Bureaucratic Survival Execution Checkpoint

## TodoCheckpointDraft

- Current todo:
  - Launch preflight: signed first-login smoke test on a fresh Apple account, full 10-card court-report CTA tap-through, one-card/docket-review retry design if product wants second answering, TestFlight archive.
- Completed todos:
  - Architecture plan written.
  - Implementation plan written.
  - Goal created for first slice.
  - Task 1 backend baseline repair completed.
  - Task 2 docket open-court backend contract completed.
  - Task 3 docket persistence completed.
  - Task 4 review session verdict API completed.
  - iPhone open-court home and review cards wired to backend APIs.
  - Server setup script updated with non-destructive docket/verdict tables.
  - R1 progression owner added over rank persistence.
  - Review verdicts now return progression feedback and update XP/fame.
  - iPhone review cards now finish into a shareable court report.
  - Launch checklist added.
  - R2 login-like error root cause isolated: Apple login was not the failing owner; production backend was missing the new `dockets` / `review-sessions` / `progression` routes.
  - Production DB schema/code deployed non-destructively and `gongkao-dazi-api` restarted.
  - Simulator existing Apple登录态 now opens court successfully against production.
  - R3 question HTML / underline recovery completed and deployed.
  - R4 docket page advance / option label recovery completed and deployed.
  - R5 court-report next step completed: report primary CTA now opens the workbench, backend memory emits `docket_review`, iOS workbench renders docket-review details, and generic workbench copy has been replaced with `府衙案牍` wording.
  - R5 docs and evidence updated in `docs/HANDOVER.md` and `90-evidence.md`.
- Active slice:
  - R5 court-report next-step closure verified at build/API level; remaining launch work is fresh-account smoke, full manual CTA tap-through, and TestFlight archive.
- Evidence refs:
  - `backend/.venv/bin/python -m pytest backend/tests/test_companion_external.py::test_companion_ask_is_quota_gated_and_audited -q` -> 1 passed.
  - `backend/.venv/bin/python -m pytest backend/tests/test_mock_exam_calibration.py::test_weekly_mock_exam_full_loop_feeds_workbench_and_calibration backend/tests/test_mock_exam_calibration.py::test_taskstream_schedules_weekly_mock_when_missing_this_week -q` -> 2 passed.
  - `backend/.venv/bin/python -m pytest backend/tests/test_practice_taskstream_routes.py::test_practice_answer_contract backend/tests/test_essay_routes.py::test_essay_review_contract_with_quota backend/tests/test_essay_routes.py::test_essay_review_quota_exceeded_contract -q` -> 3 passed.
  - `backend/.venv/bin/python -m pytest backend/tests -q` -> 67 passed, 1 warning.
  - `backend/.venv/bin/python -m pytest backend/tests/test_docket_open_court.py -q` -> 3 passed.
  - `backend/.venv/bin/python -m pytest backend/tests -q` -> 70 passed, 1 warning.
  - `backend/.venv/bin/python -m pytest backend/tests/test_docket_open_court.py -q` -> 4 passed.
  - `backend/.venv/bin/python -m pytest backend/tests -q` -> 71 passed, 1 warning.
  - `backend/.venv/bin/python -m pytest backend/tests/test_review_session_verdict.py -q` -> 2 passed.
  - `backend/.venv/bin/python -m pytest backend/tests -q` -> 73 passed, 1 warning.
  - XcodeBuildMCP `build_sim` -> succeeded for `Gongkao` on `iPhone 17`.
  - XcodeBuildMCP `build_run_sim` -> succeeded; app installed and launched on simulator.
  - XcodeBuildMCP `stop_app_sim` -> succeeded.
  - `bash -n scripts/server_setup_learning_ai_tables.sh` -> passed.
  - `backend/.venv/bin/python -m pytest backend/tests/test_review_session_verdict.py backend/tests/test_progression_contract.py backend/tests/test_practice_taskstream_routes.py::test_practice_answer_contract backend/tests/test_essay_routes.py::test_essay_review_contract_with_quota -q` -> 5 passed, 1 warning.
  - `backend/.venv/bin/python -m py_compile backend/app/domains/progression/*.py backend/app/domains/docket/*.py backend/app/domains/review_session/*.py backend/app/domains/practice/router.py backend/app/domains/essay/router.py backend/app/domains/mock_exam/service.py backend/app/domains/mock_exam/router.py backend/app/api/router.py` -> passed.
  - `rg -n "RankService\\(|get_rank_service" backend/app/domains/practice backend/app/domains/essay backend/app/domains/mock_exam backend/app/domains/review_session backend/app/domains/docket` -> no matches.
  - `backend/.venv/bin/python -m pytest backend/tests -q` -> 74 passed, 1 warning.
  - XcodeBuildMCP `build_sim` with `CODE_SIGNING_ALLOWED=NO` -> succeeded.
  - XcodeBuildMCP `build_run_sim` with `CODE_SIGNING_ALLOWED=NO` -> succeeded; app installed and launched on simulator.
  - Aegis workspace helper `bundle` / `check` attempted; failed on pre-existing missing sidecar/index coverage, recorded in `90-evidence.md`.
  - `backend/.venv/bin/python -m pytest backend/tests/test_auth_apple_contract.py backend/tests/test_docket_open_court.py backend/tests/test_review_session_verdict.py backend/tests/test_progression_contract.py -q` -> 10 passed, 1 warning.
  - Production schema setup `scripts/server_setup_learning_ai_tables.sh` -> completed; verified `learner.dockets`, `learner.docket_cards`, `learner.verdicts`.
  - Production OpenAPI includes `/api/v1/dockets/open-court`, `/api/v1/progression/profile`, `/api/v1/progression/court-state`, `/api/v1/review-sessions/{docket_id}/cards/{card_id}/verdict`.
  - Production unauthenticated `POST /api/v1/dockets/open-court` -> 401, proving route exists and auth gate owns access.
  - XcodeBuildMCP `launch_app_sim` + `snapshot_ui` after production deploy -> existing Apple session rendered 10-card open-court docket; production journal showed `POST /api/v1/dockets/open-court HTTP/1.1` -> 200.
  - `backend/.venv/bin/python -m pytest backend/tests/test_api.py::test_workbench_preview_uses_real_learner_objects backend/tests/test_review_session_verdict.py backend/tests/test_docket_open_court.py -q` -> 7 passed, 1 warning.
  - `curl -fsS https://kg.xingguoxian.cn/health` -> production ok.
  - XcodeBuildMCP `session_show_defaults` -> confirmed `Gongkao` on `iPhone 17`.
  - XcodeBuildMCP `build_sim` -> succeeded.
  - XcodeBuildMCP `build_run_sim` -> succeeded after final wording cleanup.
  - XcodeBuildMCP `snapshot_ui` -> current authenticated simulator renders open-court docket with visible underline and non-duplicated option labels.
  - XcodeBuildMCP `screenshot` -> `exports/2026-07-07-r5-open-court-snapshot.jpg`.
- Blocked-on:
  - None.
- Next step:
  - Run fresh Apple-account login smoke, manually complete a 10-card docket and tap the R5 workbench CTA end-to-end, then archive for TestFlight.

## Slice Card

- Goal: make backend pytest green before adding the new docket owner.
- Parent plan/spec: `docs/aegis/plans/2026-07-07-bureaucratic-survival-implementation-plan.md`, Task 1.
- Files: `backend/tests/sql/minimal_schema.sql`, `backend/app/domains/mock_exam/service.py`, `backend/app/domains/rank_system/deps.py`, `backend/app/domains/rank_system/router.py`, `backend/app/domains/rank_system/service.py`, `backend/app/domains/practice/router.py`, `backend/app/domains/essay/router.py`, route and position tests.
- Boundary: no production data deletion, no old-path deletion, no docket feature code yet.
- Verification: backend full pytest.
- Stop: reached; backend full suite is green.

## Slice Card

- Goal: add `/api/v1/dockets/open-court` as the new backend homepage owner.
- Parent plan/spec: `docs/aegis/plans/2026-07-07-bureaucratic-survival-implementation-plan.md`, Task 2.
- Files: `backend/app/domains/docket/*`, `backend/app/api/router.py`, `backend/tests/test_docket_open_court.py`.
- Boundary: no persistence yet, no iOS work, no old route deletion.
- Verification: docket contract tests and backend full pytest.
- Stop: reached; docket contract tests and backend full suite are green.

## Slice Card

- Goal: persist daily dockets and cards so open-court is a real session.
- Parent plan/spec: `docs/aegis/plans/2026-07-07-bureaucratic-survival-implementation-plan.md`, Task 3.
- Files: `backend/app/domains/docket/repository.py`, `backend/app/domains/docket/service.py`, `backend/app/domains/docket/router.py`, `backend/tests/sql/minimal_schema.sql`, `backend/tests/test_docket_open_court.py`.
- Boundary: no review verdict API yet, no iOS work, no destructive migration.
- Verification: docket persistence test and backend full pytest.
- Stop: reached; same-day docket reuse is verified.

## Slice Card

- Goal: add review session verdict API for docket cards.
- Parent plan/spec: `docs/aegis/plans/2026-07-07-bureaucratic-survival-implementation-plan.md`, Task 4.
- Files: `backend/app/domains/review_session/*`, `backend/app/api/router.py`, `backend/tests/sql/minimal_schema.sql`, `backend/tests/test_review_session_verdict.py`.
- Boundary: no progression event wiring yet, no iOS work in this slice.
- Verification: review-session tests and backend full pytest.
- Stop: reached; verdict recording, stamp text, ownership check, and card completion are verified.

## Slice Card

- Goal: wire iPhone R0 open-court and review cards against backend APIs.
- Parent plan/spec: `docs/aegis/plans/2026-07-07-bureaucratic-survival-implementation-plan.md`, Tasks 6-8, narrowed to launchable R0 path.
- Files: `DocketModels.swift`, `DocketStore.swift`, `DocketView.swift`, `ReviewCardsModels.swift`, `ReviewCardsStore.swift`, `ReviewCardsView.swift`, `AppShellView.swift`, `AppModels.swift`, project file.
- Boundary: no App Store/TestFlight upload, no production deployment, no destructive migration, no full progression refactor yet.
- Verification: iOS compile and simulator launch, backend full pytest.
- Stop: reached; app builds/runs and iPhone today route renders DocketView.

## DriftCheckDraft

- Original task intent: rewrite project direction toward bureaucratic survival game.
- Current slice serves intent: yes, by making the main loop emotionally complete enough for inner launch.
- Compatibility boundary: no production data deletion; no deployment; no old-path deletion from iPad/internal surfaces.
- New owner/fallback introduced: `progression` introduced as business owner over rank persistence; no old main-path fallback added.
- Retirement track explicit: yes, iPhone main path is docket/review/progression; taskstream/feed remain internal/legacy.
- Decision: R1 release candidate boundary reached; production release remains an external operation.

## Slice Card

- Goal: add progression feedback and share-report completion to make the iPhone loop launchable for inner testing.
- Parent plan/spec: `docs/aegis/plans/2026-07-07-bureaucratic-survival-implementation-plan.md`, Tasks 5 and 9 narrowed to R1.
- Files: `backend/app/domains/progression/*`, `review_session/*`, `docket/*`, `practice/router.py`, `essay/router.py`, `mock_exam/*`, `ReviewCards*`, `DocketView.swift`, `ShareReportView.swift`, project file, handover docs.
- Boundary: no destructive DB change, no production deploy, no TestFlight upload, no full御史台/commerce implementation.
- Verification: targeted backend tests, backend full pytest, iOS build/run, launch checklist.
- Stop: reached; R1 is a verified local release candidate, not yet production-published.

## Slice Card

- Goal: recover the login/open-court production path after the user saw a login-adjacent error.
- Parent plan/spec: `docs/aegis/work/2026-07-07-bureaucratic-survival-execution/95-launch-checklist.md`, production backend release.
- Files: production `/srv/gongkao-dazi/backend/app/domains/{docket,progression,review_session,rank_system}`, route owner `backend/app/api/router.py`, related execution routers/services, DB setup script.
- Boundary: no destructive DB migration, no TestFlight upload, no client-side 404 fallback.
- Verification: local auth/docket/review/progression tests, server import/OpenAPI checks, production 401 route existence, simulator existing-login open-court 200.
- Stop: reached for backend recovery; fresh first-login and verdict-card smoke remain before TestFlight.

## DriftCheckDraft

- Original task intent: make the bureaucratic survival app usable enough for near-term inner launch.
- Current slice serves intent: yes, it removes the production 404 that broke the first iPhone main path after login.
- Compatibility boundary: no production data deletion; old taskstream/feed routes remain; no client fallback added.
- New owner/fallback introduced: none; production was aligned to the existing `docket` / `review_session` / `progression` owners.
- Retirement track explicit: yes, iPhone home stays on docket/review/progression; legacy routes remain internal until later retirement.
- Decision: continue to TestFlight/fresh-account smoke.

## Slice Card

- Goal: close the product gap after the court report so a finished docket has a real learning next step.
- Parent plan/spec: current launch-readiness goal and user report "好像没有下一步".
- Files: `ShareReportView.swift`, `DocketView.swift`, `ReviewCardsView.swift`, `AppShellView.swift`, `AppModels.swift`, `WorkbenchDetailView.swift`, `backend/app/domains/memory/repository.py`, `backend/tests/test_api.py`, handover/evidence docs.
- Boundary: no fake retry flow, no client-side fallback, no old-path downgrade, no destructive production data change.
- Verification: targeted backend tests, production health, XcodeBuildMCP build/launch/snapshot, fixed screenshot artifact.
- Stop: reached for build/API/documented R5 closure; full tap-through remains a launch preflight item because UI tap tooling was not exposed in this run.

## DriftCheckDraft

- Original task intent: make the bureaucratic survival app usable enough for near-term inner launch.
- Current slice serves intent: yes, it makes the 10-card docket loop continue into workbench review instead of stopping at sharing.
- Compatibility boundary: no production data deletion; no duplicate review owner; no legacy practice retry misuse.
- New owner/fallback introduced: no fallback; `memory` workbench now owns the `docket_review` object list and iOS workbench owns its detail rendering.
- Retirement track explicit: yes, docket-review is read-only until a dedicated retry endpoint exists.
- Decision: continue to fresh-account smoke and TestFlight archive.
