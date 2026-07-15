# Bureaucratic Survival Execution Evidence

## Task 1: Backend Test Baseline Repair

Date: 2026-07-07

Commands:

- `backend/.venv/bin/python -m pytest backend/tests/test_companion_external.py::test_companion_ask_is_quota_gated_and_audited backend/tests/test_mock_exam_calibration.py::test_weekly_mock_exam_full_loop_feeds_workbench_and_calibration backend/tests/test_practice_taskstream_routes.py::test_practice_answer_contract -q`
  - Before repair: 3 failed.
  - Failure classes: missing `learner.official_ranks`, `MockExamSessionRecord` passed as DB session, route test hitting unconfigured `DATABASE_URL`.
- `backend/.venv/bin/python -m pytest backend/tests/test_companion_external.py::test_companion_ask_is_quota_gated_and_audited -q`
  - After repair: 1 passed.
- `backend/.venv/bin/python -m pytest backend/tests/test_mock_exam_calibration.py::test_weekly_mock_exam_full_loop_feeds_workbench_and_calibration backend/tests/test_mock_exam_calibration.py::test_taskstream_schedules_weekly_mock_when_missing_this_week -q`
  - After repair: 2 passed.
- `backend/.venv/bin/python -m pytest backend/tests/test_practice_taskstream_routes.py::test_practice_answer_contract backend/tests/test_essay_routes.py::test_essay_review_contract_with_quota backend/tests/test_essay_routes.py::test_essay_review_quota_exceeded_contract -q`
  - After repair: 3 passed.
- `backend/.venv/bin/python -m pytest backend/tests/test_onboarding_positions.py::test_onboarding_profile_derives_target_score_from_real_position_score backend/tests/test_onboarding_positions.py::test_position_search_filters_hard_conditions_and_returns_score_stats backend/tests/test_onboarding_positions.py::test_position_target_score_uses_l2_same_city_system_cohort -q`
  - After expectation repair: 3 passed.
- `backend/.venv/bin/python -m pytest backend/tests -q`
  - Final: 67 passed, 1 warning.

Repair track:

- Added rank test schema/config rows to `backend/tests/sql/minimal_schema.sql`.
- Fixed `MockExamService.submit_mock` local `session` shadowing by renaming the mock exam record to `mock_session`.
- Added `backend/app/domains/rank_system/deps.py` so route tests can override rank side effects without accidentally resolving production DB settings.
- Added `RankService.award_practice_answer` to keep combo lookup inside the rank service instead of in the practice route.
- Updated route tests to use authenticated bearer auth and fake rank service dependencies.
- Updated strict contract expectations for explicit `moduleName` and dynamic target score safety margins.

Retirement track:

- Existing direct rank calls in mock/onboarding remain until Task 5 introduces `progression`.
- `rank_system/deps.py` is an interim dependency boundary, not the final product owner.

## Task 2: Docket Open-Court Backend Contract

Date: 2026-07-07

Commands:

- `backend/.venv/bin/python -m pytest backend/tests/test_docket_open_court.py -q`
  - Before implementation: collection failed with `ModuleNotFoundError: No module named 'app.domains.docket'`.
  - After implementation: 3 passed, 1 warning.
- `backend/.venv/bin/python -m pytest backend/tests -q`
  - Final after Task 2: 70 passed, 1 warning.

Repair track:

- Added `backend/app/domains/docket/` with `schemas.py`, `service.py`, and `router.py`.
- Added `POST /api/v1/dockets/open-court`.
- Response includes `docketId`, `rankSnapshot`, `courtState`, `companionLine`, and 10 `daily_affair` cards.
- Cards include question stem/material/options and `sourceGrounding = product.questions`, but do not expose the correct answer.
- Docket ID and card IDs are stable per user/day until persistence lands.

Retirement track:

- `/api/v1/taskstream/today` and `/api/v1/feed/today` remain available, but no new homepage behavior was added there.
- Persistence is intentionally deferred to Task 3.

## Task 3: Persist Dockets and Docket Cards

Date: 2026-07-07

Commands:

- `backend/.venv/bin/python -m pytest backend/tests/test_docket_open_court.py::test_open_court_persists_and_reuses_daily_docket -q`
  - Before persistence schema: failed with missing `learner.dockets`.
  - After implementation: covered by full docket file.
- `backend/.venv/bin/python -m pytest backend/tests/test_docket_open_court.py -q`
  - Final after Task 3: 4 passed, 1 warning.
- `backend/.venv/bin/python -m pytest backend/tests -q`
  - Final after Task 3: 71 passed, 1 warning.

Repair track:

- Added `backend/app/domains/docket/repository.py`.
- Added `learner.dockets` and `learner.docket_cards` to the test schema.
- `DocketService.open_court` now checks active same-day docket first, returns stored cards when present, and creates one docket plus 10 cards only when absent.
- Card payload persists render-safe question content and options without correct answer.

Retirement track:

- Temporary deterministic generated IDs from Task 2 are replaced by persisted server-created UUIDs.
- Old today/feed paths remain untouched until the iOS home route is cut over.

## Task 4: Review Session Verdict API

Date: 2026-07-07

Commands:

- `backend/.venv/bin/python -m pytest backend/tests/test_review_session_verdict.py -q`
  - Before implementation: route returned 404.
  - After implementation: 2 passed, 1 warning.
- `backend/.venv/bin/python -m pytest backend/tests -q`
  - Final after Task 4: 73 passed, 1 warning.

Repair track:

- Added `backend/app/domains/review_session/` with schema, repository, service, and router.
- Added `POST /api/v1/review-sessions/{docketId}/cards/{cardId}/verdict`.
- Added `learner.verdicts` to test schema.
- Verdicts are server-side: selected answer is normalized, correctness comes from `product.questions.answer`, response stamps `准` or `驳`, and card status becomes `completed`.
- Response intentionally omits `xpGained`; progression event wiring is deferred.

Retirement track:

- Practice answer remains old execution path until iOS route is cut over.
- Rank/progression side effects are still deferred to the planned progression owner.

## iOS R0 Open-Court Wiring

Date: 2026-07-07

Commands:

- XcodeBuildMCP `session_show_defaults`
  - Confirmed project `/Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao.xcodeproj`, scheme `Gongkao`, simulator `iPhone 17`.
- XcodeBuildMCP `build_sim` with `CODE_SIGNING_ALLOWED=NO`
  - Baseline before iOS edits: succeeded.
  - After iOS edits and cleanup: succeeded.
- XcodeBuildMCP `build_run_sim` with `CODE_SIGNING_ALLOWED=NO`
  - Final: succeeded; app installed and launched on simulator.
- XcodeBuildMCP `stop_app_sim`
  - Final: succeeded.
- `backend/.venv/bin/python -m pytest backend/tests -q`
  - Final after backend + iOS + setup-script work: 73 passed, 1 warning.
- `bash -n scripts/server_setup_learning_ai_tables.sh`
  - Passed.
- `python3 /Users/zxg/.codex/aegis/scripts/aegis-workspace.py bundle --root /Users/zxg/Documents/新项目 --work 2026-07-07-bureaucratic-survival-execution`
  - Failed before bundle creation because this pre-existing work record lacks `task-intent-draft.json`.
- `python3 /Users/zxg/.codex/aegis/scripts/aegis-workspace.py check --root /Users/zxg/Documents/新项目`
  - Failed on pre-existing unindexed workspace markdown records, including old handoffs/plans/specs and this work record's `10-intent.md`.

Repair track:

- Added iOS `DocketModels`, `DocketStore`, and `DocketView`.
- Added iOS `ReviewCardsModels`, `ReviewCardsStore`, and `ReviewCardsView`.
- iPhone `.today` route now renders `DocketView`, which calls `POST /api/v1/dockets/open-court`.
- Review cards submit to `POST /api/v1/review-sessions/{docketId}/cards/{cardId}/verdict`.
- Updated `AppState.loadInitialData` so old `taskstream/feed` inputs no longer block the iPhone open-court home.
- Updated `scripts/server_setup_learning_ai_tables.sh` with non-destructive `create table if not exists` definitions for `learner.dockets`, `learner.docket_cards`, and `learner.verdicts`.

Retirement track:

- `TodayView` remains in the tree for old/iPad/debug surfaces but is no longer the iPhone today tab owner.
- `taskstream/today` and `feed/today` still load as non-blocking legacy data; full retirement remains a follow-up.
- Superseded by R1 below: `progression` owner has now been introduced for the iPhone main path.

## R1 Progression and Share Report

Date: 2026-07-07

Commands:

- `backend/.venv/bin/python -m pytest backend/tests/test_review_session_verdict.py backend/tests/test_progression_contract.py backend/tests/test_practice_taskstream_routes.py::test_practice_answer_contract backend/tests/test_essay_routes.py::test_essay_review_contract_with_quota -q`
  - Final: 5 passed, 1 warning.
- `backend/.venv/bin/python -m py_compile backend/app/domains/progression/*.py backend/app/domains/docket/*.py backend/app/domains/review_session/*.py backend/app/domains/practice/router.py backend/app/domains/essay/router.py backend/app/domains/mock_exam/service.py backend/app/domains/mock_exam/router.py backend/app/api/router.py`
  - Passed.
- `rg -n "RankService\\(|get_rank_service" backend/app/domains/practice backend/app/domains/essay backend/app/domains/mock_exam backend/app/domains/review_session backend/app/domains/docket`
  - No matches.
- `backend/.venv/bin/python -m pytest backend/tests -q`
  - Final after R1: 74 passed, 1 warning.
- XcodeBuildMCP `build_sim` with `CODE_SIGNING_ALLOWED=NO`
  - Succeeded.
- XcodeBuildMCP `build_run_sim` with `CODE_SIGNING_ALLOWED=NO`
  - Succeeded; app installed and launched on simulator.
- `bash -n scripts/server_setup_learning_ai_tables.sh`
  - Passed.

Repair track:

- Added `backend/app/domains/progression/` as the business owner for merit/fame/court-risk events.
- Review-card verdicts now update rank/fame through `ProgressionService` and return `progression` narrative to iOS.
- Docket, review-session, practice, essay, and mock-exam main execution paths no longer directly call or depend on `RankService`.
- iOS review cards decode `progression`, show merit/fame narrative, refresh rank, and enter a final court report state after the last card.
- Added `ShareReportView.swift` for local share-image rendering and system share sheet.
- Added `docs/aegis/work/2026-07-07-bureaucratic-survival-execution/95-launch-checklist.md`.
- Aegis workspace helper structural check remains a documentation-index hygiene follow-up; product verification is covered by pytest and XcodeBuildMCP.

Retirement track:

- `rank_system` remains as persistence/config底座.
- `progression` is now the canonical business owner for new main-path governance events.
- `taskstream/feed` remain available for iPad/internal/legacy surfaces; iPhone home remains `DocketView`.

## R2 Production Login/Open-Court Recovery

Date: 2026-07-07

Symptoms:

- User reported a login error.
- Simulator snapshot after signed launch showed an authenticated main shell, not the welcome screen.
- The visible error was `服务器返回 404：{"detail":"Not Found"}` after the iPhone home called `POST /api/v1/dockets/open-court`.

Commands:

- XcodeBuildMCP `build_run_sim`
  - Succeeded with normal signing defaults.
- XcodeBuildMCP `snapshot_ui`
  - Before production deploy: authenticated screen showed `服务器返回 404：{"detail":"Not Found"}`.
- `curl -sS https://kg.xingguoxian.cn/openapi.json`
  - Before deploy: production OpenAPI did not list `dockets`, `review-sessions`, or `progression` routes.
- `backend/.venv/bin/python -m pytest backend/tests/test_auth_apple_contract.py backend/tests/test_docket_open_court.py backend/tests/test_review_session_verdict.py backend/tests/test_progression_contract.py -q`
  - Final: 10 passed, 1 warning.
- `backend/.venv/bin/python -m py_compile backend/app/domains/docket/*.py backend/app/domains/review_session/*.py backend/app/domains/progression/*.py backend/app/api/router.py backend/app/domains/practice/router.py backend/app/domains/essay/router.py backend/app/domains/mock_exam/service.py backend/app/domains/mock_exam/router.py`
  - Passed.
- `scripts/server_setup_learning_ai_tables.sh`
  - Completed on production; reported `learner.dockets`, `learner.docket_cards`, and `learner.verdicts`.
- `rsync` backend route/domain files to `/srv/gongkao-dazi/backend`, then server-side `py_compile` and `create_app().openapi()` route check.
  - Passed; OpenAPI listed `/api/v1/dockets/open-court`, `/api/v1/progression/profile`, `/api/v1/progression/court-state`, `/api/v1/review-sessions/{docket_id}/cards/{card_id}/verdict`.
- `systemctl restart gongkao-dazi-api && systemctl is-active gongkao-dazi-api`
  - Service active.
- `curl -sS -i -X POST https://kg.xingguoxian.cn/api/v1/dockets/open-court -H 'Content-Type: application/json' -d '{}'`
  - Returned 401 `Authorization bearer token is required`, proving route exists and auth gate owns access.
- XcodeBuildMCP `stop_app_sim`, `launch_app_sim`, `snapshot_ui`
  - Existing Apple session rendered `九品县令` open-court header, 10 urgent cards, and the first card.
- Production journal after simulator launch:
  - `POST /api/v1/dockets/open-court HTTP/1.1` -> 200 OK.

Repair track:

- Root cause was a production release gap: iOS was already calling the new R1 canonical backend route, but production still exposed the pre-R1 route set.
- Fixed at the production backend owner by publishing `docket`, `review_session`, and `progression` route/domain code and applying the non-destructive table setup.
- Did not add client-side 404 fallback or route downgrade.

Retirement track:

- Old `taskstream` / `feed` routes remain for legacy/internal surfaces.
- iPhone main path remains `DocketView -> docket/review_session/progression`; no duplicate home owner was added.
- Fresh Apple first-login and one-card verdict smoke remain as TestFlight preflight checks.

## R3 Question HTML / Underline Recovery

Date: 2026-07-07

Symptoms:

- User reported that a question asking for the `画横线部分` had no visible underline.
- Simulator snapshot reproduced the first docket card with text equivalent to `它们像一群的小侦探...填入画横线部分最恰当的一项是：`.
- Production source row for the same question has `stem_html` containing `<u>            </u>`, so the missing line was not a source-data absence.

Root cause:

- The Docket API had been returning only plain `stem` / `materialText` in card payloads, so the main iPhone path dropped the canonical `product.questions.stem_html` rich-text contract.
- After the backend contract was restored, SwiftUI's imported HTML rendered whitespace-only `<u>` content as collapsed or visually invisible whitespace. This left the contract present but the answer blank still not visible.

Commands and evidence:

- `backend/.venv/bin/python -m pytest backend/tests/test_docket_open_court.py -q`
  - Final: 4 passed.
- `backend/.venv/bin/python -m pytest backend/tests/test_docket_open_court.py backend/tests/test_review_session_verdict.py backend/tests/test_auth_apple_contract.py -q`
  - Final: 9 passed, 1 warning.
- `backend/.venv/bin/python -m py_compile backend/app/domains/docket/*.py`
  - Passed.
- XcodeBuildMCP `build_run_sim`
  - Succeeded after the iOS renderer fix, installed and launched `com.xingguoxian.gongkaodazi` on iPhone 17 simulator.
- XcodeBuildMCP `snapshot_ui`
  - After fix: semantic text shows `它们像一群______的小侦探`.
- XcodeBuildMCP `screenshot`
  - Full evidence image: `exports/2026-07-07-docket-underline-full.jpg`.
  - Cropped evidence image: `exports/2026-07-07-underline-evidence.jpg`.
- Visual follow-up from the same screenshot pass:
  - `DocketView` now reserves bottom clearance for the floating iOS tab bar, so docket text no longer paints behind the tab-bar glass.
- `curl -fsS https://kg.xingguoxian.cn/openapi.json | jq -r '.components.schemas.DocketCard.properties | keys[]'`
  - Production schema includes `stemHtml` and `materialHtml`.
- `curl -fsS https://kg.xingguoxian.cn/health`
  - Returned `{"status":"ok","app":"gongkao-dazi","environment":"production",...}`.
- `ssh ... 'systemctl is-active gongkao-dazi-api'`
  - Returned `active`.

Repair track:

- Backend canonical owner: `backend/app/domains/docket/` now carries `stemHtml`, `materialHtml`, and option `html` from `QuestionDetail` into API schemas and persisted card payloads.
- Existing same-day dockets are not silently stuck on stale plain payloads: `list_cards` joins `product.questions` and prefers current `stem_html/material_html`.
- iOS canonical renderer: `apps/ios/Gongkao/Gongkao/QuestionRichText.swift` imports HTML and normalizes whitespace-only `<u>` content into a visible answer blank derived from the HTML tag itself.
- Consumer surfaces now use `QuestionRichText` for Docket review cards, task execution, mock exam, and onboarding baseline question views.
- Docket page layout owner: `apps/ios/Gongkao/Gongkao/DocketView.swift` gives the scroll container bottom clearance for the floating tab bar instead of patching individual answer buttons.

Retirement track:

- Plain `stem` / `materialText` remain as readable text fields and accessibility-safe fallback data, but they are no longer the owner for rich question intent on main question surfaces.
- No front-end phrase guessing was added. The app does not look for `画横线` text; it renders the typed source HTML contract.
- Any future question surface should use `QuestionRichText` rather than adding a second renderer or re-parsing the plain stem.

## R4 Docket Page Advance / Option Label Recovery

Date: 2026-07-07

Symptoms:

- User reported that after advancing, the next card kept the previous question stem while the options changed.
- User also reported duplicate option labels: the UI showed a left circular `A/B/C/D` and option text that still began with `A、` / `B、`.
- Simulator snapshot reproduced the issue at `2/10`: stem still showed the `小红帽/小侦探` question, while options were `A、抽丝剥茧 捉襟见肘`, `B、循序渐进 进退维谷`, etc.

Diagnosis:

- Production DB check showed the persisted docket itself was not randomly changing and not intrinsically corrupt: card 1 and card 2 had distinct `question_id`, payload stems, and product `stem_html`.
- The deployed repository code was still reading options from old same-day docket payloads that lacked `html` and contained source `option_text` with label prefixes.
- The iOS card view did not force a full `ReviewCardSurface` rebuild on card identity changes; with `Text(AttributedString)` this can leave rich stem rendering from the previous card while other fields update.

Commands and evidence:

- XcodeBuildMCP `snapshot_ui`
  - Before fix: `2/10` showed the first card stem with second-card options and duplicate labels.
- Production SQL over `learner.docket_cards` + `product.questions`
  - Card 1: `cq_2d1daed268070794d361f4b2`, stem begins `在太空实验室...`, options begin `A、未卜先知`.
  - Card 2: `cq_d3444047e66aede839e683ff`, stem begins `面对新的形势和要求...`, options begin `A、抽丝剥茧...`.
- `backend/.venv/bin/python -m pytest backend/tests/test_docket_open_court.py backend/tests/test_review_session_verdict.py backend/tests/test_auth_apple_contract.py -q`
  - Final: 9 passed, 1 warning.
- `backend/.venv/bin/python -m py_compile backend/app/domains/docket/*.py`
  - Passed.
- Production deploy:
  - Rsynced `backend/app/domains/docket/` to `/srv/gongkao-dazi/backend/app/domains/docket/`.
  - Remote `py_compile app/domains/docket/*.py` passed.
  - Restarted `gongkao-dazi-api`; `systemctl is-active gongkao-dazi-api` returned `active`.
- Production code/data verification with deployed `DocketRepository.list_cards()`:
  - Card 1 returned stem `在太空实验室...` and options `[('A', '未卜先知', '未卜先知'), ('B', '明察秋毫', '明察秋毫')]`.
  - Card 2 returned stem `面对新的形势和要求...` and options `[('A', '抽丝剥茧 捉襟见肘', '抽丝剥茧 捉襟见肘'), ('B', '循序渐进 进退维谷', '循序渐进 进退维谷')]`.
- XcodeBuildMCP `build_run_sim`
  - Succeeded after iOS patch.
- XcodeBuildMCP `snapshot_ui`
  - After backend deploy on first card: options are exposed as `A, 未卜先知` and `B, 明察秋毫`, no duplicate `A、` / `B、`.
- XcodeBuildMCP `build_sim`
  - Final: succeeded, no diagnostics.
- `curl -fsS https://kg.xingguoxian.cn/health`
  - Returned production ok.

Repair track:

- Backend contract owner: `DocketOption` now normalizes `text` to remove duplicated label prefixes because `label` is already a first-class field.
- Backend repository owner: same-day reused dockets now hydrate options from `product.question_options`, restoring option `html` for old payloads without deleting persistent docket rows.
- iOS presentation owner: `DocketOption.displayText` mirrors the existing `QuestionOption.displayText` behavior, and `ReviewCardSurface` uses `.id(card.cardId)` so SwiftUI rebuilds the full rich-text card on advance.

Retirement track:

- No random-per-question client behavior was added. The product rule remains one open-court selection of 10 persisted daily cards.
- Old same-day payloads remain in the database, but they are no longer authoritative for option rich text when product options exist.
- The only unverified piece is a tool limitation: current automation could not reliably scroll/click into the second card after the fix. The direct production repository object and iOS build are verified; final UI click-through should be repeated manually or with a tap-capable simulator tool.

## R5 Court Report Next-Step / Docket Review Closure

Date: 2026-07-07

Symptoms:

- User reported that after finishing the docket flow there was no clear next step.
- Product diagnosis: the court report had only the sharing action (`呈送战报`), so the learning loop ended at a social/share surface instead of returning the learner to a review object.

Product decision:

- Court report is not the terminal state.
- Primary action after a 10-card docket must enter the workbench: `去府衙复盘错卷` when there are wrong cards, `去府衙归档战报` when all cards are correct.
- Sharing remains available but is secondary.

Commands and evidence:

- `backend/.venv/bin/python -m pytest backend/tests/test_api.py::test_workbench_preview_uses_real_learner_objects backend/tests/test_review_session_verdict.py backend/tests/test_docket_open_court.py -q`
  - Final: 7 passed, 1 warning.
- `curl -fsS https://kg.xingguoxian.cn/health`
  - Returned production ok: `{"status":"ok","app":"gongkao-dazi","environment":"production",...}`.
- XcodeBuildMCP `session_show_defaults`
  - Confirmed project `/Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao.xcodeproj`, scheme `Gongkao`, simulator `iPhone 17`.
- XcodeBuildMCP `build_sim`
  - Succeeded after the court-report ordering cleanup and documentation updates.
- XcodeBuildMCP `build_run_sim`
  - Final: succeeded after the `府衙案牍` wording cleanup, installed and launched `com.xingguoxian.gongkaodazi`.
- XcodeBuildMCP `snapshot_ui`
  - Succeeded; current authenticated simulator rendered the open-court docket, first card, visible underline text, and non-duplicated option labels.
- XcodeBuildMCP `screenshot`
  - Persisted screenshot: `exports/2026-07-07-r5-open-court-snapshot.jpg`.

Repair track:

- iOS court report owner: `CourtReportPanel` now renders a primary workbench CTA above the share button.
- Navigation owner: `DocketView -> ReviewCardsView -> CourtReportPanel` carries `onOpenWorkbench`, and `AppShellView` switches the selected tab to `.workbench` and force-refreshes initial data.
- Backend workbench owner: `LearnerMemoryRepository` now lists `docket_review` objects from `learner.verdicts` grouped by docket, including wrong count, total count, docket id, latest answer time, and ordered question ids.
- iOS workbench owner: `WorkbenchObject`, `WorkbenchObjectPayload`, and `WorkbenchObjectDetailView` recognize `docket_review`; `DocketReviewDetail` renders the current docket's question material, stem, and options through `QuestionRichText`.
- Maintainability cleanup: `ShareReportPayload` now orders verdicts by the docket card order via `cardId` instead of using nondeterministic dictionary value order.
- Immersion cleanup: the workbench surface now uses `府衙案牍` / `暂无案牍` wording instead of generic `学习工作台` copy.

Retirement track:

- No client-side fake review path was added.
- `docket_review` is currently a read/inspect review detail, not a second-answer workflow.
- If wrong-card retry becomes a product requirement, add a dedicated docket retry contract rather than routing it through legacy practice task submission.

Uncovered:

- UI automation tap tooling was not exposed in this run, so the final "tap the new CTA and land in workbench" interaction was not re-clicked automatically in this slice.
- Prior simulator/data verification had already shown the workbench object `今日卷宗复盘 · 2026-07-07`; this slice verifies the compiled navigation path, backend contract tests, production health, and current authenticated open-court rendering.
