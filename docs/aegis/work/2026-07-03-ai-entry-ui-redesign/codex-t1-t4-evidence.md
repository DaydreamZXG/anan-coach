# Codex T1-T4 Evidence

- Date: 2026-07-03
- Scope: execute Fable T1-T4 under the Phase 2 Apple Bearer session boundary.
- T1: `/api/v1/bootstrap` now reads `product.*` counts; `/api/v1/workbench/preview` requires Bearer auth and returns only learner-derived practice/essay objects, or empty `sections`.
- T2: `learner.practice_answers` remains the canonical table; `GET /api/v1/taskstream/today` now returns `completedTaskIds` derived from today's practice answers and essay attempts.
- T3: `reserve_quota(limit=0)` rejects before insert; AI provider failure now refunds the reservation and writes a failed interaction log.
- T4: backend route list documents `/api/v1/workbench/preview` and Bearer session ownership.
- iOS: workbench/source-base panels now consume `/api/v1/workbench/preview`; old `PreviewData.workbenchSections` was removed.
- S1 method-card check: added `applicability`, `time_budget_sec`, `accuracy_rule`, and `validation` to the YAML/table contract.
- Retired path: hardcoded `55214`, static workbench learning objects, backend app `省联考` execution wording.
- Retained path: `X-Learner-Id` only in negative tests and historical audit docs.
- Verification: `backend/.venv/bin/python -m pytest backend/tests` -> 30 passed, 1 warning.
- Verification: remote `/srv/gongkao-dazi/backend/.venv/bin/python -m pytest tests` -> 30 passed, 1 warning.
- Verification: `scripts/full_verify.sh` -> backend 30 passed; iPhone 17 build succeeded; iPad Pro 13-inch (M5) build succeeded; Aegis workspace check passed.
- Remote smoke: service active; `/api/v1/bootstrap` returns real counts (`canonicalQuestions=54801`, `knowledgeChunks=23103`); `/taskstream/today` and `/workbench/preview` reject missing Bearer with 401; knowledge search returns product data.
