# IAP Products And Entitlements

Updated: 2026-07-09

> 本文档记录现有 iOS App Store 商品与技术契约，不是微信小程序的新商业决策。小程序与后续 App 共用的价值边界、免费能力和计费原则以 [学习输入输出、拍题搜索与商业权益边界](2026-07-12-learning-io-search-commerce-boundaries.md) 为准；具体额度需经真实成本压测后由服务端目录发布。

## Product IDs

| Product ID | Tier | Period | First price target | App role |
|---|---|---|---|---|
| `gongkao_ai_pro_monthly` | Pro | Monthly | `CNY_29_39` | Short sprint and early conversion |
| `gongkao_ai_pro_yearly` | Pro | Yearly | `CNY_199_299` | Full national-exam preparation cycle |

These IDs are stable runtime contract values. App Store Connect price tiers can
be configured later, but the product IDs must not be renamed without a migration
plan across backend, iOS, and App Store Connect.

## Entitlement Contract

The backend `commerce` domain is the owner for:

- IAP product catalog
- App Store signed transaction ingestion
- User entitlement state
- Per-user feature quota gates
- Entitlement audit logs

The iOS app renders the server catalog and StoreKit product metadata. It does
not decide tier, quota, entitlement expiry, or product validity.

`commerce.app_store_transactions` is the source of truth for verified App Store
transaction facts. `commerce.user_entitlements` is a derived snapshot for fast
entitlement reads. After each verified transaction or first-seen server
notification, the backend reconciles the user's Pro entitlement from all
currently active, non-revoked transactions. This prevents an old monthly
subscription refund or expiry from shortening a newer active yearly
subscription.

`originalTransactionId` identifies the subscription chain, not a blanket
revocation key for every later transaction in that chain. A revoked transaction
excludes the matching `transactionId`, or the same `originalTransactionId` +
`productId` + `expiresDate` service period, from active entitlement
reconciliation. Later active renewals or product switches in the same chain must
remain eligible for Pro.

## Feature Quotas

| Feature | Free | Pro | Period |
|---|---:|---:|---|
| 鹿鸣 AI 问答 (`companion_ask`) | 3 | 60 | Day |
| 申论 AI 批改 (`essay_review`) | 1 | 20 | Day |
| 外部错题解析 (`external_wrong_intake`) | 1 | 30 | Day |
| 拍照解析 (`photo_explain`) | 1 | 30 | Day |
| 岗位风险解释 (`position_narrative`) | 2 | 50 | Day |
| 学习计划重排 (`study_plan_regeneration`) | 0 | 10 | Month |

The server enforces these limits through `commerce.entitlement_usage_daily`.
AI provider logs stay in `ai_audit.interaction_logs`.

`commerce.entitlement_usage_daily.usage_date` stores the start date of the
quota period. Daily quotas use the calendar day; monthly quotas use the first
day of the month. Entitlement responses must read usage through the same
period mapping used by reservation/refund, so monthly quotas do not reset every
day.

## Verification Boundary

The app submits `VerificationResult<Transaction>.jwsRepresentation` to
`POST /api/v1/commerce/app-store/transactions`.

iOS must start purchases with `Product.PurchaseOption.appAccountToken(userId)`,
where `userId` is the backend authenticated learner UUID. The backend rejects
transactions whose signed `appAccountToken` does not equal the authenticated
user. This prevents a valid App Store transaction from being replayed onto a
different learner account.

The backend grants Pro only after all of the following are true:

- The signed transaction JWS verifies against the configured Apple root certificate chain.
- `bundleId` equals `com.xingguoxian.gongkaodazi`.
- `productId`, `transactionId`, and `originalTransactionId` match the submitted request.
- `appAccountToken` equals the authenticated backend user ID.
- The product ID exists in `commerce.iap_products`.
- The transaction is not revoked.
- The subscription `expiresDate` is in the future.

If `APP_STORE_ROOT_CERTIFICATE_PATHS` is not configured, transactions are
rejected and no Pro entitlement is granted.

## Server Notifications

App Store Server Notifications V2 should point to:

```text
POST /api/v1/commerce/app-store/notifications
```

The backend verifies the notification `signedPayload`, records the
`notificationUUID` before processing, skips duplicate `notificationUUID`
deliveries, verifies nested `signedTransactionInfo` only for first-seen
notifications, and updates the same Pro entitlement owner:

- Active transaction: record the transaction fact, then reconcile Pro entitlement.
- Expired or revoked transaction: update the transaction fact, then reconcile
  the user entitlement. If no active non-revoked transactions remain,
  `subscriptionEnd` moves to the latest expired or revoked transaction end.

If multiple App Store products exist for the same user, notification handling
must reconcile the full user entitlement from `commerce.app_store_transactions`
instead of applying the single notification directly to
`commerce.user_entitlements`.

Notification processing also requires `APP_STORE_ROOT_CERTIFICATE_PATHS`.
