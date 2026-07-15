# Sections 9-11 AI intake, mock exam, calibration - Checkpoint

- Task ID: 2026-07-03-sections-9-11-ai-mock-calibration
- Current todo: All section 9-11 backend slices implemented, deployed, and verified; record final evidence and workspace bundle/check.
- Active slice: evidence-closeout
- Completed todos:
  - Section 8.5 knowledge-map supplement is present in the onboarding/execution spec.
  - Section 9 companion ask API implemented with quota/audit and learner-context citations.
  - Section 9 external wrong-question intake implemented with `product.knowledge_map_nodes` validation and user confirmation before learner memory write.
  - Section 10 weekly guokao xingce mock implemented as 135 questions with review object and review-viewed gate.
  - Section 11 ability calibration implemented from baseline, practice, and mock exam evidence; taskstream consumes calibration and unviewed mock review.
  - Additive production schema script includes `learner.external_question_intakes`, `learner.external_wrong_questions`, and `learner.mock_exam_sessions`.
  - Backend code synced to `101.96.198.253`, package reinstalled, schema script rerun, and `gongkao-dazi-api.service` restarted.
- Evidence refs:
  - Local full backend tests: `cd backend && ./.venv/bin/python -m pytest -q` -> `62 passed, 1 warning`.
  - Local compile/script syntax: `compileall`, `bash -n` on server setup and refresh scripts -> exit 0.
  - Remote targeted tests: `24 passed, 1 warning` under `/srv/gongkao-dazi/backend`.
  - Remote service: `systemctl restart gongkao-dazi-api.service` then `systemctl is-active` -> `active`.
  - Remote smoke: `/health` and `/api/v1/bootstrap` 200; new authenticated routes return 401 rather than 404 when called without Bearer token.
  - Remote DB: new learner tables exist with `gongkao_api` `SELECT/INSERT/UPDATE`; counts are currently 0.
- Blocked on: none
- Next step: UI owner wires homepage companion, photo/extracted-text intake, mock execution, and review page against the deployed contracts.

## DriftCheckDraft

- Does the current work still serve the original task intent? yes
- Did the slice stay inside the compatibility boundary? yes, additive backend/schema/API only
- Did any new fallback, adapter, or duplicate owner appear? no provider fallback, no anonymous learner fallback, no AI-created tag owner
- Is retirement/follow-up explicit? yes, UI/OCR work is outside this backend slice
- Evidence decision: continue to final closeout
