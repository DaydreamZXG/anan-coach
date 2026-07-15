# AI 上岸顾问改造证据

日期：2026-07-09

## EvidenceBundleDraft

### Target test

Command:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_position_decision.py -q
```

Result:

```text
3 passed, 1 warning in 3.79s
```

### Backend regression

Command:

```bash
backend/.venv/bin/python -m pytest backend/tests -q
```

Result:

```text
77 passed, 1 warning in 32.80s
```

### iOS onboarding integration build

Command:

```text
XcodeBuildMCP build_sim extraArgs=["CODE_SIGNING_ALLOWED=NO"]
```

Result:

```text
SUCCEEDED in 2.22s, warnings: 0, errors: 0
```

### Slice 4 iOS 鹿鸣上岸方案 build

Command:

```text
XcodeBuildMCP build_sim extraArgs=["CODE_SIGNING_ALLOWED=NO"]
```

Result:

```text
SUCCEEDED in 21.382s, warnings: 0, errors: 0
```

### Slice 4 backend regression

Command:

```bash
backend/.venv/bin/python -m pytest backend/tests -q
```

Result:

```text
77 passed, 1 warning in 40.32s
```

### Slice 5 iOS 鹿鸣今日路线 build

Command:

```text
XcodeBuildMCP build_sim extraArgs=["CODE_SIGNING_ALLOWED=NO"]
```

Result:

```text
SUCCEEDED in 16.555s, warnings: 0, errors: 0
```

### Slice 5 backend regression

Command:

```bash
backend/.venv/bin/python -m pytest backend/tests -q
```

Result:

```text
77 passed, 1 warning in 37.52s
```

### Prediction contract covered

- If a candidate position has only one direct score sample, `position_decision` treats it as insufficient for decision scoring.
- The regional-major predictor excludes the target position itself from the sample set.
- With two prior-year national-exam samples for the same region/major/city/system/education/political/service scope, the decision report returns `decisionTargetScore`, `decisionScoreBasis`, `predicted_score`, and a concrete score gap.

### Product/UI direction spec

- Wrote `docs/aegis/specs/2026-07-09-ai-shangan-mascot-ui-spec.md`.
- Chose `公考搭子 / 上岸搭子` as product naming direction and `鹿鸣` as the AI companion role.
- Set next implementation target as Slice 4: turn the onboarding target result into an `AI 上岸方案` surface with mascot, score gap, decision basis, position buckets, missing conditions, and next-plan CTA.
- Kept `position_decision` as the decision owner; mascot UI must not duplicate scoring logic.

### Slice 4 UI contract covered

- `OnboardingTargetStep` now starts with `PositionDecisionReport`, which renders the AI 上岸方案 instead of the old plain recommendation-list card.
- `LumingMascotView` is a real SwiftUI vector mascot, registered through `LumingShanganPlanView.swift`.
- Decision target score, score gap, `decisionScoreBasis`, sample counts, confidence, and evidence summary are visible in the report.
- Selecting a recommended position stores its `PositionDecisionItem` in `OnboardingStore.selectedDecisionItem`.
- `saveProfile()` now prefers `selectedDecisionItem.decisionTargetScore` when target score text is empty, so the planner receives the decision score rather than falling back to stale position entry-score data.
- New主路径 scan did not find `幕僚`、`县令`、`御史台`、`惩罚`、`假数据` in the edited onboarding decision files.

### Slice 5 home route covered

- `LumingTodayRouteView` renders the post-onboarding home surface from real `TodayTaskstream` fields: `target`, `trackMix`, `tasks`, `completedTaskIds`, `rationale`, and task `sourceGrounding`.
- `TodayView` now prioritizes the 鹿鸣今日路线 over feed cards. Feed cards are retained only as supplemental reminders after the primary route.
- iPhone `AppSection.today` now opens `TodayView`, not the old `DocketView`.
- iPad `StudyDeskView` center canvas reuses `LumingTodayRouteView`, so iPhone and iPad share the same home-route product direction.
- Main-path wording scan for `AppShellView.swift`、`FeatureViews.swift`、`StudyDeskView.swift`、`CoachComponents.swift`、`LumingTodayRouteView.swift`、`LumingShanganPlanView.swift` found no old `政务/幕僚/县令/府衙/仕途/开衙/会试/案卷/批阅` user-facing terms except in non-main workbench object taxonomy or unrelated legacy files.

### Slice 6 commerce targeted backend verification

Command:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_commerce.py backend/tests/test_essay_routes.py backend/tests/test_companion_external.py backend/tests/test_ai_audit_repository.py -q
```

Result:

```text
20 passed, 1 warning in 9.01s
```

### Slice 6 commercial audit hardening verification

Command:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_commerce.py backend/tests/test_essay_routes.py backend/tests/test_companion_external.py backend/tests/test_ai_audit_repository.py -q
```

Result:

```text
22 passed, 1 warning in 46.76s
```

Covered audit fixes:

- StoreKit purchases bind `appAccountToken` to the authenticated backend user UUID.
- Backend rejects a valid signed transaction if `appAccountToken` does not match the current user.
- App Store Server Notifications V2 route verifies notification payloads, records `notificationUUID`, verifies nested transaction JWS, and expires Pro entitlement on revoked/expired transactions.
- StoreKit `Transaction.updates` submits completed subscription updates to the backend when the app has login state.

### Slice 6 iOS StoreKit/paywall build

Command:

```text
XcodeBuildMCP build_sim extraArgs=["CODE_SIGNING_ALLOWED=NO"]
```

Result:

```text
SUCCEEDED in 11.792s, warnings: 0, errors: 0
```

### Slice 6 backend full regression

Command:

```bash
backend/.venv/bin/python -m pytest backend/tests -q
```

Result:

```text
82 passed, 1 warning in 37.48s
```

Post-audit rerun:

```text
84 passed, 1 warning in 46.57s
```

### Slice 6 final iOS simulator build

Command:

```text
XcodeBuildMCP build_sim extraArgs=["CODE_SIGNING_ALLOWED=NO"]
```

Result:

```text
SUCCEEDED in 3.239s, warnings: 0, errors: 0
```

Post-audit rerun:

```text
SUCCEEDED in 1.994s, warnings: 0, errors: 0
```

### Slice 6 final commercial static audit

Commands:

```bash
rg -n "reserve_quota\(|refund_quota\(|ESSAY_REVIEW_DAILY_LIMIT|COMPANION_ASK_DAILY_LIMIT|EXTERNAL_WRONG_INTAKE_DAILY_LIMIT" backend/app/domains/essay backend/app/domains/companion
rg -n "TODO|FIXME|placeholder|假数据|本地会员|fake|mock success|未校验|兜底" backend/app/domains/commerce apps/ios/Gongkao/Gongkao/Commerce*.swift docs/product/iap-products.md
rg -n "appAccountToken|app_account_token|verify_notification|app-store/notifications|Transaction\.updates|APP_STORE_ROOT_CERTIFICATE_PATHS" backend/app/domains/commerce apps/ios/Gongkao/Gongkao/CommerceStore.swift apps/ios/Gongkao/Gongkao/CommerceViews.swift apps/ios/Gongkao/Gongkao/FeatureViews.swift docs/product/iap-products.md docs/ops/server-inventory.md
```

Results:

```text
No stale hardcoded quota owner references found in essay/companion domains.
No TODO/FIXME/placeholder/fake-success/local-membership markers found in commerce/paywall contract files.
Required commercial anchors found: appAccountToken binding, backend app_account_token check, App Store notification verification route, Transaction.updates server submission, and APP_STORE_ROOT_CERTIFICATE_PATHS ops notes.
```

### Slice 6 App Store notification replay hardening

Target command:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_commerce.py -q
```

Result:

```text
8 passed, 1 warning in 34.75s
```

Regression command:

```bash
backend/.venv/bin/python -m pytest backend/tests -q
```

Result:

```text
85 passed, 1 warning in 25.82s
```

iOS build command:

```text
XcodeBuildMCP build_sim extraArgs=["CODE_SIGNING_ALLOWED=NO"]
```

Result:

```text
SUCCEEDED in 3.383s, warnings: 0, errors: 0
```

Covered:

- App Store Server Notifications now record `notificationUUID` before transaction processing.
- Duplicate `notificationUUID` deliveries return `processed=false` and do not verify or apply the nested transaction again.
- Replay test covers a refund notification followed by a duplicate notification carrying an active transaction; the duplicate does not re-grant Pro and does not write a second transaction.

### Slice 6 external IAP verification runbook

Files:

- `docs/ops/iap-sandbox-verification.md`
- `docs/ops/server-inventory.md`

Covered:

- App Store Connect product and subscription group prerequisites.
- Server `APP_STORE_ROOT_CERTIFICATE_PATHS` and notification URL prerequisites.
- Sandbox purchase, restore, notification renewal, duplicate notification replay, and refund/revocation walkthroughs.
- Evidence archive requirements and release blockers.

### Slice 6 production commerce schema hardening

Target command:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_server_setup_schema_contract.py backend/tests/test_commerce.py -q
```

Result:

```text
10 passed, 1 warning in 7.22s
```

Regression command:

```bash
backend/.venv/bin/python -m pytest backend/tests -q
```

Result:

```text
87 passed, 1 warning in 39.94s
```

Script syntax command:

```bash
bash -n scripts/server_setup_learning_ai_tables.sh
```

Result:

```text
exit 0
```

iOS build command:

```text
XcodeBuildMCP build_sim extraArgs=["CODE_SIGNING_ALLOWED=NO"]
```

Result:

```text
SUCCEEDED in 3.577s, warnings: 0, errors: 0
```

Covered:

- Production setup script now enforces commerce product tier, billing period, user entitlement tier, and App Store transaction verification status constraints.
- Production setup script now adds query indexes for user transactions, original transaction histories, notification events, and entitlement audit logs.
- `backend/tests/test_server_setup_schema_contract.py` prevents production setup drift from the stricter test schema on commercial state constraints.

### Slice 6 quota period correctness

Target command:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_commerce.py -q
```

Result:

```text
10 passed, 1 warning in 11.93s
```

Target plus setup contract command:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_server_setup_schema_contract.py backend/tests/test_commerce.py -q
```

Result:

```text
12 passed, 1 warning in 8.67s
```

Regression command:

```bash
backend/.venv/bin/python -m pytest backend/tests -q
```

Result:

```text
89 passed, 1 warning in 42.84s
```

iOS build command:

```text
XcodeBuildMCP build_sim extraArgs=["CODE_SIGNING_ALLOWED=NO"]
```

Result:

```text
SUCCEEDED in 4.160s, warnings: 0, errors: 0
```

Covered:

- `study_plan_regeneration` is a monthly Pro quota and now reserves against the month-start usage bucket instead of the current calendar day.
- Daily quotas still reset on the next calendar day.
- Entitlement response usage reads use the same per-feature period mapping as reservation/refund, so the paywall quota display does not show stale monthly usage.

### Slice 6 subscription switch entitlement reconcile

Target command:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_commerce.py -q
```

Result:

```text
11 passed, 1 warning in 26.18s
```

Target plus setup contract command:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_server_setup_schema_contract.py backend/tests/test_commerce.py -q
```

Result:

```text
13 passed, 1 warning in 20.72s
```

Regression command:

```bash
backend/.venv/bin/python -m pytest backend/tests -q
```

Result:

```text
90 passed, 1 warning in 41.45s
```

Script syntax command:

```bash
bash -n scripts/server_setup_learning_ai_tables.sh
```

Result:

```text
exit 0
```

iOS build command:

```text
XcodeBuildMCP build_sim extraArgs=["CODE_SIGNING_ALLOWED=NO"]
```

Result:

```text
SUCCEEDED in 3.249s, warnings: 0, errors: 0
```

Covered:

- `commerce.app_store_transactions` is now the source of truth for verified App Store transaction facts.
- `commerce.user_entitlements` is reconciled from active, non-revoked transactions after transaction registration and first-seen server notifications.
- Test coverage includes a monthly subscription followed by a yearly subscription; a refund notification for the monthly original transaction does not expire the active yearly entitlement.
- Old single-notification entitlement mutation paths `upsert_pro_entitlement` and `expire_pro_entitlement` were removed.

### Slice 6 originalTransactionId revocation precision

Reference:

- Apple Developer video transcript notes that a subscription renewal keeps the
  same `originalTransactionId` as the original purchase while each event has its
  own transaction identifier.

Target command:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_commerce.py -q
```

Result:

```text
12 passed, 1 warning in 15.80s
```

Target plus setup contract command:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_server_setup_schema_contract.py backend/tests/test_commerce.py -q
```

Result:

```text
14 passed, 1 warning in 18.13s
```

Regression command:

```bash
backend/.venv/bin/python -m pytest backend/tests -q
```

Result:

```text
91 passed, 1 warning in 35.81s
```

Script syntax command:

```bash
bash -n scripts/server_setup_learning_ai_tables.sh
```

Result:

```text
exit 0
```

iOS build command:

```text
XcodeBuildMCP build_sim extraArgs=["CODE_SIGNING_ALLOWED=NO"]
```

Result:

```text
SUCCEEDED in 3.172s, warnings: 0, errors: 0
```

Covered:

- Revoke matching no longer treats `originalTransactionId` as a blanket invalidation key for every active transaction in the chain.
- Active transactions are excluded only by matching `transactionId`, or by matching the same `originalTransactionId` + `productId` + `expiresDate` service period.
- Test coverage includes a revoked original transaction and a later active transaction with the same `originalTransactionId`; the later active transaction keeps Pro entitlement.

### Aegis workspace structure check

Command:

```bash
python3 /Users/zxg/.codex/aegis/scripts/aegis-workspace.py check --root /Users/zxg/Documents/新项目
```

Result:

```text
exit 1
```

Notes:

- The check failed on pre-existing unindexed Aegis markdown records under older 2026-07-06/2026-07-07 workstreams.
- The current 2026-07-09 AI 上岸顾问 plan/checkpoint/evidence/spec records are indexed in `docs/aegis/INDEX.md`.
- Rerun after production commerce schema hardening produced the same older-workstream indexing failures.

### Slice 6 commercial contract covered

- `GET /api/v1/commerce/catalog` returns the stable first-wave IAP products `gongkao_ai_pro_monthly` and `gongkao_ai_pro_yearly`.
- `GET /api/v1/commerce/entitlement` returns free by default and Pro only from active server-side entitlement state.
- `POST /api/v1/commerce/app-store/transactions` records verified App Store signed transactions and grants Pro only when the transaction is active.
- Expired verified transactions are recorded as `verified_expired` and do not grant Pro.
- Daily quota owner moved to `commerce.entitlement_usage_daily`; AI model interactions remain in `ai_audit.interaction_logs`.
- Free-to-Pro same-day upgrade uses the new Pro limit when reserving feature usage.
- iOS paywall loads server catalog, StoreKit products, current entitlement, purchases, restore purchases, and submits `VerificationResult<Transaction>.jwsRepresentation` to the backend.
- iOS purchase uses StoreKit `appAccountToken(userId)`; backend requires signed transaction `appAccountToken` to match authenticated user.
- App Store Server Notifications V2 endpoint can process active, expired, and revoked nested transactions into the same entitlement owner, and duplicate `notificationUUID` deliveries are not reprocessed.
- Product/ops contract recorded in `docs/product/iap-products.md` and `docs/ops/server-inventory.md`.

## Changed Files

- `docs/aegis/plans/2026-07-09-ai-shangan-advisor-transformation.md`
- `docs/aegis/specs/2026-07-09-ai-shangan-mascot-ui-spec.md`
- `backend/app/domains/position_decision/__init__.py`
- `backend/app/domains/position_decision/score_prediction.py`
- `backend/app/domains/position_decision/schemas.py`
- `backend/app/domains/position_decision/service.py`
- `backend/app/domains/position_decision/router.py`
- `backend/app/domains/positions/repository.py`
- `backend/app/domains/positions/service.py`
- `backend/app/api/router.py`
- `backend/app/api/routes.py`
- `backend/app/core/config.py`
- `backend/app/domains/commerce/__init__.py`
- `backend/app/domains/commerce/app_store.py`
- `backend/app/domains/commerce/catalog.py`
- `backend/app/domains/commerce/deps.py`
- `backend/app/domains/commerce/repository.py`
- `backend/app/domains/commerce/router.py`
- `backend/app/domains/commerce/schemas.py`
- `backend/app/domains/commerce/service.py`
- `backend/app/domains/companion/router.py`
- `backend/app/domains/companion/service.py`
- `backend/app/domains/essay/router.py`
- `backend/app/domains/essay/service.py`
- `backend/tests/test_ai_audit_repository.py`
- `backend/tests/test_commerce.py`
- `backend/tests/test_companion_external.py`
- `backend/tests/test_essay_routes.py`
- `backend/tests/test_position_decision.py`
- `backend/tests/sql/minimal_schema.sql`
- `backend/README.md`
- `scripts/server_setup_learning_ai_tables.sh`
- `docs/product/iap-products.md`
- `docs/ops/server-inventory.md`
- `apps/ios/Gongkao/Gongkao/CommerceModels.swift`
- `apps/ios/Gongkao/Gongkao/CommerceStore.swift`
- `apps/ios/Gongkao/Gongkao/CommerceViews.swift`
- `apps/ios/Gongkao/Gongkao/LumingShanganPlanView.swift`
- `apps/ios/Gongkao/Gongkao/LumingTodayRouteView.swift`
- `apps/ios/Gongkao/Gongkao.xcodeproj/project.pbxproj`
- `apps/ios/Gongkao/Gongkao/AppModels.swift`
- `apps/ios/Gongkao/Gongkao/AppShellView.swift`
- `apps/ios/Gongkao/Gongkao/FeatureViews.swift`
- `apps/ios/Gongkao/Gongkao/StudyDeskView.swift`
- `apps/ios/Gongkao/Gongkao/CoachComponents.swift`
- `apps/ios/Gongkao/Gongkao/OnboardingModels.swift`
- `apps/ios/Gongkao/Gongkao/OnboardingStore.swift`
- `apps/ios/Gongkao/Gongkao/OnboardingFlowView.swift`
- `apps/ios/Gongkao/Gongkao/OnboardingSteps.swift`
- `apps/ios/Gongkao/Gongkao/OnboardingBaselineSteps.swift`
- `apps/ios/Gongkao/Gongkao/MockExamView.swift`
- `docs/HANDOVER.md`
- `docs/aegis/INDEX.md`
- `docs/aegis/work/2026-07-09-ai-shangan-advisor-transformation/10-intent.md`
- `docs/aegis/work/2026-07-09-ai-shangan-advisor-transformation/20-checkpoint.md`
- `docs/aegis/work/2026-07-09-ai-shangan-advisor-transformation/90-evidence.md`
