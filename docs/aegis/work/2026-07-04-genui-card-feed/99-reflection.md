# GenUI card feed backend phase 1 - Reflection

Implemented the phase-one backend feed as a new `backend/app/domains/feed`
owner instead of adding GenUI logic to `taskstream`. The feed endpoint adapts
the existing `/api/v1/taskstream/today` result, so task scheduling remains owned
by the old planner while card ordering and payload shaping live in the feed
orchestrator.

What changed:

- Added the closed v1 card schema vocabulary and `TodayFeed` response model.
- Added `GET /api/v1/feed/today`, authenticated through the existing auth path.
- Added rule-based card generation for currently supported data: goal gap,
  coach brief, practice/essay/method-card task shapes, mock exam due, pending
  mock review, and calibration note.
- Added an invariant check that feed task-card minutes must equal the old
  taskstream scheduled minutes.
- Kept unsupported future card types schema-only; no fabricated wrong-cluster,
  quota, streak, companion, or external-capture cards.
- Passed through `TodayCalibration.statusLabel` so feed can render the real
  calibration label without another invented label.

Verification:

- `cd backend && . .venv/bin/activate && pytest tests/test_feed_today.py -q`
  passed: 5 tests.
- `cd backend && . .venv/bin/activate && pytest -q` passed: 67 tests, 1
  existing Starlette/httpx deprecation warning.
- `python3 /Users/zxg/.codex/aegis/scripts/aegis-workspace.py check --root /Users/zxg/Documents/新项目`
  passed after indexing the new feed spec.

Residual risk:

- `daysLeft` remains `null` because no canonical exam-date source is in the
  feed input contract yet.
- `wrong_cluster`, `streak_restart`, `quota_notice`, `companion_prompt`, and
  `external_capture_nudge` are not emitted until their source systems are ready.
- The old `/api/v1/taskstream/today` endpoint is intentionally retained for the
  one-version compatibility window.

Method Pack output does not grant completion authority.
