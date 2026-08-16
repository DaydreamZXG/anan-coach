---
name: anan-qbank-cloud
description: Connect exam-prep apps to paid, bounded question sessions for China's national civil-service exam, provincial civil-service exams, and public-institution recruitment exams. Use when an agent needs a 5-20 question practice pack, a complete template-backed mock paper, paged question retrieval, or post-submission scoring and explanations through the Anan QBank Cloud API.
---

# 岸岸题库云

Use this Skill to integrate an application with paid question delivery for `guokao`, `shengkao`, or `sydw`.

## Choose the operation

- For a focused exercise, create a `practice` session with `questionCount` from 5 through 20.
- For a complete paper, create a `mock` session. Do not set `questionCount`; the server chooses the full template.
- Read either kind of session in pages of at most 20 questions.
- Submit the session once to receive the score, correct answers, and explanations.

Read [references/api.md](references/api.md) before composing a direct HTTP integration. Read [references/payment.md](references/payment.md) when handling membership activation or a `402 Payment Required` response.

## Required configuration

Require `QBANK_BASE_URL` and `QBANK_API_KEY` in the local environment. Never request, print, log, or commit the SkillHub developer secret, RSA private key, or plaintext API key. Never accept a key pasted into public conversation; tell the user to rotate it and configure it locally.

## Execute a question flow

Use `scripts/qbank_client.py` for deterministic calls:

```bash
python3 scripts/qbank_client.py create --kind practice --exam guokao --count 10 --request-id app-run-0001
python3 scripts/qbank_client.py create --kind mock --exam shengkao --request-id app-run-0002
python3 scripts/qbank_client.py page --session-id ses_xxx --offset 0 --limit 20
python3 scripts/qbank_client.py submit --session-id ses_xxx --request-id submit-0001 --answers-file answers.json
```

Treat returned `sessionId`, ordering, and question identities as opaque. Preserve them exactly. Use a new `requestId` for a new intent and reuse the same value only for an exact retry.

## Enforce product boundaries

- Do not enumerate the catalog, invent cursor endpoints, scrape all questions, or parallelize calls to reconstruct the database.
- Do not expose answers or explanations before the session is submitted.
- Do not split a complete paper into separate practice sessions. A complete paper is one server-side `mock` session, even though delivery is paged.
- Do not exceed 20 questions in one page request or 60 total requests per minute per API key.
- Do not represent demo content as a real past paper. A response with `catalogMode: demo` is for integration verification only.
- On `CATALOG_UNAVAILABLE`, stop. Do not fall back to unlicensed or invented production questions.
- On `ENTITLEMENT_EXPIRED`, obtain a new paid membership; never bypass expiry.

## Handle failures

- `401 UNAUTHORIZED`: check that the local API key is configured and has not been revoked.
- `402 PAYMENT_PENDING`: wait for verified provider success, then retry the same membership order.
- `403 ENTITLEMENT_EXPIRED`: purchase and fulfill another week or month membership.
- `409 IDEMPOTENCY_CONFLICT` or `ALREADY_SUBMITTED`: do not mutate or replay with different data.
- `429 RATE_LIMITED`: back off until the rolling 60-second window clears.
- `503 CATALOG_UNAVAILABLE`: report that no licensed production catalog is active for the requested operation.
