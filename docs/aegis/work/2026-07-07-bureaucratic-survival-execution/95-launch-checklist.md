# R1 Launch Checklist

Date: 2026-07-07

## Release Candidate State

- Backend local contract state: `74 passed, 1 warning`.
- iOS simulator state: `build_sim` succeeded and `build_run_sim` succeeded for `Gongkao` on `iPhone 17`.
- Main product loop: `open-court -> review verdict -> progression feedback -> share report`.
- Production backend deploy: executed in R2 on 2026-07-07; `open-court` route is live and simulator existing-login smoke renders the docket.
- TestFlight/App Store upload: not executed in this session.

## Production Backend Release Evidence

1. Applied non-destructive schema setup on production:
   - `learner.dockets`
   - `learner.docket_cards`
   - `learner.verdicts`
   - Used `scripts/server_setup_learning_ai_tables.sh`; no destructive SQL.
2. Rsynced backend changed files to `/srv/gongkao-dazi/backend`.
3. Restarted only the active unit: `gongkao-dazi-api`.
4. Verified:
   - `GET https://kg.xingguoxian.cn/health` -> 200.
   - production OpenAPI lists `dockets`, `review-sessions`, and `progression`.
   - unauthenticated `POST /api/v1/dockets/open-court` -> 401, not 404.
   - simulator existing Apple session auto-opened court and production journal logged `POST /api/v1/dockets/open-court` -> 200.
5. Still confirm with a fresh Apple account before TestFlight:
   - authenticated `POST /api/v1/review-sessions/{docketId}/cards/{cardId}/verdict`
   - authenticated `GET /api/v1/progression/profile`

## Must Do Before TestFlight

1. Confirm the app points at the intended API base URL: `https://kg.xingguoxian.cn`.
2. Run a signed archive with Team `BRG82P5ZB7` and Bundle ID `com.xingguoxian.gongkaodazi`.
3. Smoke test with a real Apple login account:
   - onboarding/profile gate
   - open court
   - answer one correct and one wrong card
   - rank/profile refresh
   - finish 10 cards and open share sheet
4. Capture at least one iPhone screenshot for each surface:
   - Welcome/auth
   - 开衙
   - 卡片批阅
   - 准/驳反馈
   - 政务战报

## Known Non-Blocking R1 Gaps

- iPad still uses the older study desk shell; iPhone is the launch focus.
- `taskstream` and `feed` backend routes remain available for internal/legacy surfaces.
- `rank_system` remains the persistence底座; `progression` is now the business owner.
- Punishment/御史台 challenge is not fully implemented; current R1 returns court-risk deltas and民心变化.
- No commerce, subscription, or consumable purchase flow is included in R1.
