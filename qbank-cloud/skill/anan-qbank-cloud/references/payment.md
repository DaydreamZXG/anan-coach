# Membership and SkillHub payment

- `week`: CNY 9.90, valid 7 days.
- `month`: CNY 19.90, valid 30 days.

Invoke `POST /v1/membership/invoke` with a stable `requestId` and `plan`. A new unpaid invocation returns HTTP 402 with `WeixinPay-Required` and `X-Out-Trade-No`. A payment-capable agent completes the WeChat flow and retries with the same `X-Out-Trade-No`.

The service must query WeChat order state and fulfill only after `SUCCESS`. The same paid order returns the same entitlement identity, API key, and expiry without extending membership.

Production preorder signing uses `SKILLHUB-SHA256-RSA2048`, RSA PKCS#1 v1.5 with SHA-256, and this exact text including the trailing newline:

```text
POST
/palmpayminiapp/clawagentpay/preorder
{timestamp}
{nonce}
{payment_required}
```

The foundation backend ships a complete signer and a fake payment gateway for tests. Live sale remains blocked until a real WeChat order adapter, merchant callback/refund policy, production secrets, and a licensed `developer_api` catalog are configured and verified.
