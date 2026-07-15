# R0 time budget update + guokao weight refresh evidence

## Scope

- Apply revised Section 3 budget defaults:
  - parttime default: 120 minutes
  - fulltime default: 480 minutes
  - max daily minutes: 720
  - parttime task cap: 25 minutes
  - fulltime task block: 40-120 minutes
- Materialize `product.exam_tag_weights` on production server.
- Keep client onboarding/profile budget controls aligned with the revised range and hour-first display.

## Production Checks

- `./scripts/server_setup_learning_ai_tables.sh`
  - Result: succeeded.
  - Added/confirmed `learner_profiles_daily_minutes_check`.
- `./scripts/server_refresh_guokao_exam_tag_weights.sh`
  - Result: succeeded.
  - `INSERT 0 607`.
  - Window: `2022-2026`.
- Read-only production verification:
  - `product.exam_tag_weights` guokao/xingce rows: `607`.
  - `learner_profiles_daily_minutes_check`: present.

## Local Verification

- `cd backend && ./.venv/bin/python -m pytest -q`
  - Result: `50 passed, 1 warning`.
- `cd backend && ./.venv/bin/python -m compileall -q app tests`
  - Result: passed.
- `bash -n scripts/server_setup_learning_ai_tables.sh`
  - Result: passed.
- `bash -n scripts/server_refresh_guokao_exam_tag_weights.sh`
  - Result: passed.
- XcodeBuildMCP `build_sim`
  - Result: `SUCCEEDED`.
  - Simulator profile: `iPhone 17`, scheme `Gongkao`.

## Anti-Entropy Notes

- Old 45/240/600 and 15-minute task-cap paths were replaced, not retained.
- Budget constants now live in `backend/app/domains/study_budget.py` to avoid duplicate owners.
- Existing user-provided `daily_minutes` values are respected when valid; only missing values derive the new defaults.
