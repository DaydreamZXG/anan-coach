# Developer API v1

All content routes require `Authorization: Bearer $QBANK_API_KEY` and JSON.

## Create a session

`POST /v1/sessions`

Practice: `{"requestId":"practice-0001","kind":"practice","exam":"guokao","questionCount":10}`

Mock: `{"requestId":"mock-0001","kind":"mock","exam":"shengkao"}`

Allowed exams are `guokao`, `shengkao`, and `sydw`. A practice pack contains 5-20 questions. A mock uses the complete server template and can contain more than 20.

## Read a page

`GET /v1/sessions/{sessionId}/questions?offset=0&limit=20`

`limit` is 1-20. Before submission, questions contain `questionId`, `stem`, `options`, and `module`; they never contain the answer or explanation.

## Submit

`POST /v1/sessions/{sessionId}/submit`

```json
{"requestId":"submit-0001","answers":[{"questionId":"opaque-question-id","answer":"A"}]}
```

The first valid submission freezes the result and releases answers and explanations. Exact request replay is idempotent. A different second submission is rejected.

There is intentionally no list-all, arbitrary cursor, question-by-ID, or bulk export endpoint. Do not infer one.
