# Sections 9-11 AI intake, mock exam, calibration - Intent

## TaskIntentDraft

- Requested outcome: Implement sections 9-11 backend contracts: ask companion/external wrong-question intake with quota, weekly mock exam with review, and continuous ability calibration.
- Goal: Implement sections 9-11 backend contracts: ask companion/external wrong-question intake with quota, weekly mock exam with review, and continuous ability calibration.
- Success evidence:
- Section 9 routes exist and require authenticated learner context: `/api/v1/companion/ask`, `/api/v1/companion/external-questions/resolve`, `/api/v1/companion/external-questions/{intake_id}/confirm`.
- External wrong-question intake rejects free/generated tags, rejects unsupported image nodes, persists only user-confirmed `external` memory with `node_id`.
- Companion answers are quota/audit gated and do not use provider fallback.
- Section 10 routes exist for weekly 135-question guokao mock, submission, review, and review-viewed status.
- Mock exam generation uses the guokao blueprint, excludes recent practice/mock questions, and refuses shortened mocks when the question bank is insufficient.
- Section 11 route returns ability calibration from baseline, daily practice, and mock exam evidence; taskstream consumes mock review/calibration.
- Local full backend test suite passes; server code is deployed and production service remains active.
- Stop condition: Stop when success evidence is satisfied or a blocker/risk requires pause.
- Non-goals:
- iOS UI for homepage companion, photo intake, mock execution, and review page.
- Real OCR/image upload pipeline; current backend contract accepts extracted text/tag candidates and enforces map validation.
- Anonymous learner fallback, fake data, or provider fallback.
- Scope: backend domains, product/learner/ai_audit schema, tests, server setup scripts, Aegis evidence
- Change kinds:
- implementation
- Risk hints:
- AI cost surface: companion ask and external intake are quota/audit gated.
- Source-of-truth boundary: runtime cannot create `product.knowledge_map_nodes`; missing/free tags must be rejected.
- Production schema change: additive tables under `learner.*`, no destructive data migration.

## BaselineReadSetHint

- `docs/aegis/specs/2026-07-03-onboarding-and-execution-fixes-spec.md` sections 8.5, 9, 10, 11.
- `docs/aegis/specs/2026-07-03-method-cards-and-guokao-focus-spec.md` for method-card/knowledge-map boundaries.
- Existing auth/memory/taskstream/ai audit backend domains.

## BaselineUsageDraft

- Required baseline refs:
- `docs/aegis/specs/2026-07-03-onboarding-and-execution-fixes-spec.md`
- `docs/aegis/specs/2026-07-03-method-cards-and-guokao-focus-spec.md`
- Acknowledged before plan:
- yes
- Cited in plan:
- sections 8.5-11 are the acceptance baseline
- Missing refs:
- none
- Advisory decision: continue

## ImpactStatementDraft

- Compatibility boundary: additive backend/API/schema work only; no fallback users, no free AI-created tags, no shortened mock exams, no production data deletion.
- Affected layers:
- FastAPI routes, companion/mock_exam/calibration domains, taskstream planner input, learner/product/ai_audit schema, tests, deployment scripts.
- Owners:
- `product.knowledge_map_nodes` remains product-owned canonical map.
- `learner.external_question_intakes`, `learner.external_wrong_questions`, and `learner.mock_exam_sessions` are learner-owned runtime records.
- `ai_audit.*` owns quota and interaction logs.
- Invariants:
- All learner routes require authenticated backend session.
- External wrong questions write memory only after user confirmation.
- AI/provider failure does not create fallback content; companion quota is refunded on provider failure.
- Weekly mock is exactly 135 xingce questions or unavailable.
- Non-goals:
- UI implementation and real OCR pipeline are left for the iOS/UI owner.

These records are Method Pack drafts / hints, not authoritative runtime decisions.
