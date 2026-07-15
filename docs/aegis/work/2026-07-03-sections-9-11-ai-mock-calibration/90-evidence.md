# Sections 9-11 AI intake, mock exam, calibration - Evidence

## Local Verification

- Command: `cd backend && ./.venv/bin/python -m pytest -q`
- Result: exit 0; `62 passed, 1 warning in 28.84s`.
- Scope: full backend contract suite, including auth, onboarding, positions, method cards, essay materials, practice/taskstream, companion, external intake, mock exam, and calibration tests.

- Command: `cd backend && ./.venv/bin/python -m compileall -q app tests`
- Result: exit 0.
- Scope: Python syntax/import compilation for backend app and tests.

- Command: `bash -n scripts/server_setup_learning_ai_tables.sh && bash -n scripts/server_refresh_guokao_exam_tag_weights.sh && bash -n scripts/server_refresh_knowledge_map_nodes.sh`
- Result: exit 0.
- Scope: shell syntax for schema and product refresh scripts.

## Implemented Contracts

- Section 9 companion ask:
  - Route: `POST /api/v1/companion/ask`.
  - Uses authenticated `user_id`/`learner_key`, learner memory/profile/mock context, method-card citations, `ai_audit` quota/logging, and the configured AI provider.
  - Provider failure logs failure and refunds quota; no alternate provider/content fallback is introduced.

- Section 9 external wrong-question intake:
  - Routes: `POST /api/v1/companion/external-questions/resolve`, `POST /api/v1/companion/external-questions/{intake_id}/confirm`.
  - Writes `learner.external_question_intakes` for auditability.
  - Writes `learner.external_wrong_questions` only after user confirmation.
  - Rejects missing/free/generated tags, low confidence, non-unique candidates, `containsImage`, and nodes where `support_status != text_supported`.
  - Matches only existing published `product.knowledge_map_nodes`; request-time AI cannot create nodes.

- Section 10 weekly mock exam:
  - Routes: `POST /api/v1/mock-exams/weekly`, `POST /api/v1/mock-exams/{session_id}/submit`, `GET /api/v1/mock-exams/{session_id}/review`, `POST /api/v1/mock-exams/{session_id}/review/view`.
  - Blueprint: 135 xingce questions: 常识判断 20, 言语理解与表达 40, 数量关系 15, 判断推理 40, 资料分析 20.
  - Selection uses published text-supported knowledge-map nodes with near-5 occurrence/weight, excludes recent 60-day practice/mock questions, and fails unavailable rather than shortening the mock.
  - Review payload contains estimated score, module accuracy/change, wrong clusters, method-card repair actions, next-week planner reason, and viewed status.

- Section 11 calibration:
  - Route: `GET /api/v1/calibration/ability`.
  - Computes score/confidence from latest mock exam, enough daily practice, or baseline/self-report in that priority order.
  - Taskstream prioritizes unviewed mock review, schedules missing weekly mock, and otherwise includes calibration data/reason.

## Production Deployment

- Server: `101.96.198.253`
- Runtime path: `/srv/gongkao-dazi/backend`
- Service: `gongkao-dazi-api.service`
- Deploy actions:
  - Synced `backend/app`, `backend/tests`, `backend/README.md`, `backend/pyproject.toml`.
  - Synced server setup/refresh scripts to `/srv/gongkao-dazi/scripts`.
  - Reinstalled backend package with `/srv/gongkao-dazi/backend/.venv/bin/python -m pip install -q -e /srv/gongkao-dazi/backend`.
  - Ran `bash scripts/server_setup_learning_ai_tables.sh`.
  - Restarted `gongkao-dazi-api.service`.

## Production Schema Evidence

- Command: `bash scripts/server_setup_learning_ai_tables.sh`
- Result: exit 0; script reported 23 expected tables across `learner`, `ai_audit`, and `product`.
- New table status:
  - `learner.external_question_intakes`: exists, count 0.
  - `learner.external_wrong_questions`: exists, count 0.
  - `learner.mock_exam_sessions`: exists, count 0.
- Grants:
  - `gongkao_api` has `SELECT`, `INSERT`, `UPDATE` on the three new learner tables.
  - `gongkao_api` has `SELECT` on `product.knowledge_map_nodes` and `product.exam_tag_weights`.

## Production Runtime Evidence

- Command: `ssh ... 'systemctl restart gongkao-dazi-api.service && sleep 2 && systemctl is-active gongkao-dazi-api.service'`
- Result: exit 0; `active`; uvicorn startup completed.

- Command: `curl -fsS https://kg.xingguoxian.cn/health`
- Result: exit 0; response includes `"status":"ok"` and `"environment":"production"`.

- Command: `curl -fsS https://kg.xingguoxian.cn/api/v1/bootstrap`
- Result: exit 0; response includes feature flags:
  - `"companion": true`
  - `"externalWrongQuestionIntake": true`
  - `"weeklyMockExam": true`
  - `"abilityCalibration": true`

- Commands:
  - `curl -X POST https://kg.xingguoxian.cn/api/v1/companion/ask ...`
  - `curl -X POST https://kg.xingguoxian.cn/api/v1/mock-exams/weekly`
  - `curl https://kg.xingguoxian.cn/api/v1/calibration/ability`
- Result: each returned 401 with `"Authorization bearer token is required"`, proving deployed routes are registered and protected rather than missing.

## Remote Targeted Tests

- Command: `ssh ... 'sudo -u deploy -H bash -lc "export PATH=/usr/lib/postgresql/16/bin:$PATH; cd /srv/gongkao-dazi/backend && .venv/bin/python -m compileall -q app tests && .venv/bin/python -m pytest -q tests/test_companion_external.py tests/test_mock_exam_calibration.py tests/test_practice_taskstream_routes.py tests/test_api.py tests/test_learner_memory.py"'`
- Result: exit 0; `24 passed, 1 warning in 8.46s`.
- Scope: deployed code against temporary PostgreSQL, covering the new companion/external/mock/calibration flow and adjacent taskstream/auth/bootstrap regressions.

## Uncovered Scope / Residual Risk

- Real image upload/OCR/multimodal extraction is not implemented in this slice; backend currently accepts extracted text/tag candidates and enforces validation.
- New authenticated flows were not exercised against a real production user token to avoid inserting/deleting production test data.
- iOS UI for the new contracts remains a separate owner task.
