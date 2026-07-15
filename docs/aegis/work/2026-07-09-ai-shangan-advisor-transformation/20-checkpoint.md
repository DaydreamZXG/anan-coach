# AI 上岸顾问改造执行检查点

日期：2026-07-09

## TodoCheckpointDraft

- Current todo: Slice 6 第一波商业化闭环已完成代码层审计与验证；下一步是外部 App Store 沙盒、证书和生产配置验收。
- Completed todos:
  - Added transformation notebook plan.
  - Added `position_decision` schemas, service, router.
  - Mounted router in API router.
  - Added contract tests.
  - Updated backend README and handover domain table.
  - Aligned test minimal schema with existing optional `positions.has_extra_exam` behavior.
  - Added iOS models for the position decision response.
  - Added onboarding store state and API call for `GET /api/v1/position-decisions/recommendations`.
  - Connected target-confirmation onboarding to show the AI 上岸顾问 report with summary, buckets, score gap, reasons, risk level, and target-position selection.
  - Persisted manually supplied shenlun score when it is used for the decision report.
  - Planned Slice 3 in the parent notebook: national-exam regional-major score prediction for decision target scores.
  - Added real-score sample access in `positions` for national-exam regional-major prediction.
  - Added `position_decision.score_prediction` for prior-year regional/major prediction tiers.
  - Added `decisionTargetScore` and `decisionScoreBasis` to decision items.
  - Updated iOS onboarding models and report row to show the decision target score.
  - Wrote `docs/aegis/specs/2026-07-09-ai-shangan-mascot-ui-spec.md`.
  - Added `LumingShanganPlanView.swift` with the鹿鸣 vector mascot, AI 上岸方案 header, decision metrics, score-basis evidence, position bucket board, missing-condition/action panel, and current-score input state.
  - Reworked `OnboardingTargetStep` so the AI 上岸方案 is the primary target-confirmation surface.
  - Added `OnboardingStore.selectedDecisionItem` so selecting a recommended position persists its `decisionTargetScore` into the generated plan profile update.
  - Cleared stale decision-item state when users manually select/deselect positions outside the decision report.
  - Fixed a `MockExamView` timer main-actor warning.
  - Added `LumingTodayRouteView.swift` for the post-onboarding home route.
  - Changed iPhone Today tab from old `DocketView` to `TodayView`.
  - Reordered `TodayView` so `taskstream/today` route is primary and feed cards are only supplemental reminders.
  - Updated iPad `StudyDeskView` center canvas to reuse `LumingTodayRouteView`.
  - Reworded visible main-path labels from old government-role terms to `鹿鸣 / 今日路线 / 工作台 / 我的`.
  - Added backend `commerce` domain for IAP catalog, App Store transaction ingestion, entitlements, quota reservation/refund, and audit logs.
  - Moved essay review, companion ask, and external wrong-question intake quota gates to `commerce`.
  - Added StoreKit 2 `CommerceStore` and Profile paywall with purchase/restore, server entitlement state, quotas, and StoreKit prices.
  - Added `docs/product/iap-products.md` and server inventory notes for `APP_STORE_ROOT_CERTIFICATE_PATHS`.
  - Commercial audit fix: StoreKit purchases now bind `appAccountToken` to backend user UUID and backend rejects mismatched signed transactions.
  - Commercial audit fix: StoreKit transaction updates can submit completed/pending transactions to the server when the app has login state.
  - Commercial audit fix: backend now exposes App Store Server Notifications V2 ingestion and processes active/expired/revoked nested transactions.
  - Commercial audit fix: duplicate App Store Server Notification `notificationUUID` deliveries are now skipped before nested transaction processing, with replay coverage for refund followed by duplicate active transaction.
  - Added `docs/ops/iap-sandbox-verification.md` as the external App Store sandbox purchase/restore/notification/refund/replay verification runbook.
  - Commercial deployment fix: production setup script now enforces commerce tier/billing/status check constraints and adds App Store transaction/notification/audit query indexes.
  - Commercial quota fix: feature usage reservations and entitlement displays now use each feature's quota period, so monthly `study_plan_regeneration` does not reset daily while daily features still reset daily.
  - Commercial entitlement fix: user Pro entitlement is now reconciled from verified App Store transaction facts, so refund/expiry of an old monthly product does not shorten an active yearly subscription.
  - Commercial entitlement fix: revoked transactions no longer invalidate every active transaction sharing the same `originalTransactionId`; revocation matching is limited to the same transaction or same original/product/expires service period.
- Active slice: external launch readiness.
- Blocked-on items: none.
- Next step: configure App Store Connect subscription products, server Apple root certificate paths, App Store Server Notifications URL, then run a real sandbox purchase/restore/refund/replay walkthrough.

## DriftCheckDraft

- Original task intent still served: yes.
- Compatibility boundary preserved: yes, existing endpoints remain.
- New owner appeared: yes, intentionally `position_decision` and `commerce`.
- Fallback/adapter added: no.
- Persistent-state risk: medium; new `commerce` tables are required for product catalog, entitlements, App Store transaction records, entitlement usage, and audit logs. Production setup script now includes commerce constraints/indexes, but no production database migration has been executed from this workspace.
- Decision: continue.

## ResumeStateHint

Resume from the plan at `docs/aegis/plans/2026-07-09-ai-shangan-advisor-transformation.md`. The backend `position_decision` owner, iOS onboarding consumption slice, national-exam regional-major score prediction slice, Slice 4 鹿鸣上岸方案 UI, Slice 5 鹿鸣今日上岸路线首页, and Slice 6 第一波商业化闭环 are implemented. Keep decision logic in `position_decision`, use positions only for real score sample access, keep鹿鸣 as explanation/companion UI rather than a duplicate decision owner, and keep paid access in `commerce` with App Store signed transaction verification. Do not grant Pro without configured Apple root certificate verification.
