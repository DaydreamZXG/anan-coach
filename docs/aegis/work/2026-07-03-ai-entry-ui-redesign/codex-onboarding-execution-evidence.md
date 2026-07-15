# Codex Onboarding And Execution Fixes Evidence

- Date: 2026-07-03
- Scope: implement backend/data slice from `docs/aegis/specs/2026-07-03-onboarding-and-execution-fixes-spec.md`; UI remains owned by fable.
- Boundary held: no iOS/UI edits in this slice, no fake learner fallback, no fabricated target scores or essay materials.
- Profiles: `learner.profiles` now carries hard conditions, `study_mode`, `target_position_id`, target/baseline fields, and `onboarding_complete`.
- Onboarding API: added authenticated profile get/update, complete, placement-test assembly, and placement-test answer submission.
- Positions API: added authenticated hard-condition search over `product.positions`, entry-score evidence, and target-score derivation as average entry score + 3 only when real score data exists.
- Time budget R0: `/api/v1/taskstream/today` reads learner profile; part-time defaults to 45 minutes and keeps each planned task at or below 15 minutes; full-time defaults to 240 minutes.
- Baseline honesty: planner reason text distinguishes self-report, placement-test, and no-baseline states; `planBasis` remains `rules_v1`.
- Goal honesty: `target.scoreGapAvailable` marks whether `scoreGap` is backed by both target score and baseline, so UI can show "待摸底" instead of a fake numeric gap.
- Essay materials: retired static material text; essay task now returns `materials[]`, `questionId`, and `materialIncomplete`, with full material assembled from `product.knowledge_chunks`.
- Placement persistence: `learner.placement_attempts` records question ids, answers, module snapshot, and estimated xingce score, then updates profile baseline.
- Compatibility retained: old essay `material` string remains as a concatenation of full materials for current iOS compatibility.
- Known data limitation: `product.positions` has no first-time/fresh-graduate condition column, so search reports `unverifiedConditions=["freshGraduate"]` instead of pretending it filtered that field.
- Verification: `backend/.venv/bin/python -m pytest backend/tests` -> 43 passed, 1 warning.
- Remote deploy: synced backend app/tests/readme/pyproject and scripts to `101.96.198.253`, ran additive schema script, restarted `gongkao-dazi-api.service`.
- Remote verification: temporary PostgreSQL database via `TEST_DATABASE_URL` -> `/srv/gongkao-dazi/backend/.venv/bin/python -m pytest -p no:cacheprovider tests` -> 43 passed, 1 warning.
- Remote smoke: service active; `/health` returns `ok`; `/api/v1/bootstrap` returns real product counts; unauthenticated onboarding/positions routes return 401; production read-only counts include `position_entry_score_links=18382` and essay material chunks `16499`.
