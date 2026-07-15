# IAP Sandbox Verification Runbook

Updated: 2026-07-09

This runbook is the required external verification path before claiming the
first paid release is ready. It covers App Store Connect, backend certificate
configuration, StoreKit purchase/restore, server notification handling, and
evidence capture.

## Preconditions

- App Store Connect bundle ID: `com.xingguoxian.gongkaodazi`.
- App Store Connect subscription product IDs:
  - `gongkao_ai_pro_monthly`
  - `gongkao_ai_pro_yearly`
- Products are in one auto-renewable subscription group.
- Product metadata and pricing are approved for sandbox testing.
- Sandbox tester account is available and not signed into production purchase history.
- Backend deploy includes the current `commerce` domain and database tables.
- `APP_STORE_ROOT_CERTIFICATE_PATHS` points to Apple root certificate PEM/DER files on the server.
- App Store Server Notifications V2 URL is configured:

```text
https://kg.xingguoxian.cn/api/v1/commerce/app-store/notifications
```

## Backend Preflight

1. Confirm `GET /api/v1/commerce/catalog` returns both configured product IDs.
2. Confirm unauthenticated product catalog does not expose local membership switches.
3. Confirm an authenticated free user gets `tier=free` from `GET /api/v1/commerce/entitlement`.
4. Confirm missing or invalid `APP_STORE_ROOT_CERTIFICATE_PATHS` causes transaction and notification verification to reject instead of granting Pro.
5. Confirm backend logs can identify:
   - `commerce.app_store_transactions`
   - `commerce.app_store_notification_events`
   - `commerce.user_entitlements`
   - `commerce.entitlement_audit_logs`

## Purchase Walkthrough

1. Install the sandbox build on a clean simulator or device.
2. Sign in through the app so the backend user ID is known.
3. Open Profile and load the AI Pro paywall.
4. Confirm StoreKit prices are shown for both product IDs.
5. Buy `gongkao_ai_pro_monthly`.
6. Confirm iOS submits `VerificationResult<Transaction>.jwsRepresentation`.
7. Confirm backend verifies:
   - Apple certificate chain
   - bundle ID
   - product ID
   - transaction ID
   - original transaction ID
   - `appAccountToken`
   - non-expired and non-revoked subscription state
8. Confirm `GET /api/v1/commerce/entitlement` returns `tier=pro`.
9. Confirm Pro quotas are visible in the paywall and high-value AI features use Pro limits.

Pass standard: no Pro entitlement appears before verified App Store transaction
acceptance, and the entitlement belongs only to the authenticated user whose
UUID matches `appAccountToken`.

## Restore Walkthrough

1. Remove the app or clear local state.
2. Reinstall and sign in as the same backend user.
3. Tap restore purchases.
4. Confirm current StoreKit entitlements are submitted to the backend.
5. Confirm server entitlement returns `tier=pro`.

Pass standard: restore does not rely on local membership state and still goes
through server-side signed transaction verification.

## Notification Walkthrough

1. Trigger a renewal event in sandbox.
2. Confirm `commerce.app_store_notification_events` records the
   `notificationUUID`.
3. Confirm the nested `signedTransactionInfo` is verified and recorded.
4. Confirm entitlement dates move forward for active renewal.
5. Replay the same notification payload.
6. Confirm duplicate `notificationUUID` returns without reprocessing the nested
   transaction.

Pass standard: first delivery updates entitlement; duplicate delivery does not
write another transaction or change entitlement.

## Refund Or Revocation Walkthrough

1. Trigger sandbox refund/revocation from App Store tooling.
2. Confirm server notification is received.
3. Confirm nested transaction status is recorded as revoked or expired.
4. Confirm `commerce.user_entitlements.subscription_end` moves to the
   revocation or expiry time.
5. Confirm high-value AI features fall back to free quotas after entitlement
   expiry.

Pass standard: refunded or revoked subscription cannot keep Pro quota.

## Evidence To Archive

- Backend deploy commit and environment snapshot without secrets.
- App Store Connect product screenshots showing product IDs and subscription group.
- Transaction API request/response IDs.
- Notification UUIDs used for first delivery and duplicate replay.
- Database rows for transaction, notification event, entitlement, and audit log.
- iOS screen recording of purchase, entitlement refresh, restore, and paywall quota display.
- Test command output:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_commerce.py -q
backend/.venv/bin/python -m pytest backend/tests -q
```

```text
XcodeBuildMCP build_sim extraArgs=["CODE_SIGNING_ALLOWED=NO"]
```

## Release Blockers

- App Store products missing or renamed.
- `APP_STORE_ROOT_CERTIFICATE_PATHS` missing or invalid on the server.
- Server notification URL not configured or returning non-2xx for valid Apple payloads.
- Sandbox purchase grants Pro to a user whose UUID does not match `appAccountToken`.
- Restore grants Pro from local state without server verification.
- Duplicate notification reprocesses a nested transaction.
- Refund/revocation leaves Pro entitlement active.
