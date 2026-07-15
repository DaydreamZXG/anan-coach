# Knowledge Base To Magic Loop Master Plan

Date: 2026-07-03
Status: Proposed execution plan

## Goal

Turn 考公搭子 from a runnable shell into a source-grounded AI preparation agent. The first product milestone is not "more pages"; it is one real loop:

`AI arranges today -> learner completes 行测/申论 task -> system checks against trusted sources -> AI gives actionable feedback -> user memory updates -> tomorrow is replanned`.

## Architecture

The product has four durable layers:

1. Product Knowledge Base: canonical qbank, AI-washed explanations, tags, knowledge chunks, position data, curated essay materials, and official/manual policy sources.
2. Learner Memory: target profile, diagnostic state, practice answers, wrong-book records, essay attempts, rewrite versions, mastery estimates, and task logs.
3. AI Orchestrator: retrieval, planner, explanation, essay review, quota, audit, and cost tracking.
4. Native Client: iPhone/iPad taskstream and workbench UI using the backend as the source of truth.

Obsidian may be used only as an internal authoring/editorial workspace for curated materials. It is not a runtime product database, user memory store, permission system, or multi-user knowledge backend.

## Tech Stack

- Backend: FastAPI, SQLAlchemy 2, Alembic, PostgreSQL, pgvector, Redis/RQ when background jobs are needed.
- Storage: PostgreSQL for structured product/user data; TOS for uploaded images/PDFs and essay drafts when file storage is required.
- AI: configured model gateway, with Gemini reverse proxy allowed as current provider; all calls audited by feature/user/day.
- iOS/iPadOS: SwiftUI, Swift Concurrency, AuthenticationServices, StoreKit 2.
- Verification: pytest, importer parity checks, xcodebuild simulator builds, route contract tests, Aegis workspace check.

## Baseline / Authority Refs

- `/Users/zxg/Desktop/deep-research-report.md`
- `docs/aegis/specs/2026-07-03-ai-agent-information-architecture.md`
- `docs/aegis/plans/2026-07-02-commercial-execution-master-plan.md`
- `docs/aegis/plans/2026-07-03-ai-workbench-runnable-slice.md`
- `reports/question-bank/canonical-qbank-v1-report-20260702.md`
- `tools/question-bank/canonical-qbank-v1.sql`

## Compatibility Boundary

- No old React Native, Flutter, or feature-grid client path becomes runtime authority.
- No open-web Deep Research in the first release.
- No semantic duplicate merge beyond the existing approved qbank policy.
- AI may not mutate canonical questions, explanations, positions, entitlements, pricing, or App Store transaction authority.
- Test fixtures may exist in tests only; no hidden production fallback user, fallback model, fallback auth, or degraded product path.

## Plan Basis

- The research report says the opportunity is not "more questions"; it is an AI-first preparation workflow.
- The latest IA spec says the app should be `今日 / 工作台 / 我的`, with 行测 and 申论 as first-class tracks.
- The current runnable app has preview-only taskstream/workbench contracts. The next work must replace static preview data with product-owned source and learner-memory data.

## BaselineUsageDraft

- Required baseline refs: research report, latest IA spec, commercial master plan, qbank report, current runnable slice evidence.
- Acknowledged before plan refs: iOS-first, Apple login, App Store IAP, tight AI quota, no open-web research, Obsidian not runtime, commercial maintainability.
- Cited in plan refs: qbank counts, source-grounded workbench pattern, AI boundary, taskstream owner, two-track 行测/申论 model.
- Missing refs: final App Store IAP product creation details are not needed for this plan's early slices.
- Decision: continue.

## Requirement Ready Check

- Requirement source refs: user conversation, research report, IA spec, commercial master plan.
- Goals and scope refs: build first magical loop grounded in product knowledge and user memory.
- User / scenario refs: learner wants the app to decide what to do, teach with trusted sources, remember mistakes, and replan.
- Requirement item refs: product KB, learner memory, taskstream planner, 行测 answer loop, 申论 rewrite loop, iOS integration, audit/quota.
- Acceptance / verification criteria refs: imported qbank count parity, route contract tests, iPhone/iPad builds, no old feature-grid runtime path.
- Open blocker questions: none for planning.
- Decision: ready.

## Architecture Integrity Lens

- Invariant: backend product database owns source truth and learner memory; iOS renders and records actions.
- Canonical owner / contract: `backend/app/domains/knowledge`, `practice`, `essay`, `taskstream`, and `ai` own the first magical loop.
- Responsibility overlap: preview endpoints and static Swift fixtures must not remain behavior owners after their slice is replaced.
- Higher-level simplification: everything the user sees should be either a task, a study object, or account/commercial state.
- Retirement / falsifier: if a user can still browse "刷题/选岗" as product identity, the plan failed.
- Verdict: proceed.

## Execution Roadmap

### Phase 0: Authority And Repository Baseline

Purpose: stop the project from drifting again before deeper work starts.

Files:

- `docs/aegis/plans/2026-07-03-knowledge-base-to-magic-loop-master-plan.md`
- `docs/aegis/INDEX.md`
- repository git baseline

Work:

1. Mark this plan plus the latest IA spec and commercial master plan as the active authority.
2. Treat older Flutter/email-auth/announcement-first bridge details as historical source refs, not active implementation direction.
3. Make a clean baseline commit before large schema/importer work, because the current repo is mostly untracked.

Verification:

```bash
python3 /Users/zxg/.codex/aegis/scripts/aegis-workspace.py check --root /Users/zxg/Documents/新项目
git status --short
```

Acceptance:

- active authority is clear;
- no new implementation starts from an untracked, ambiguous baseline.

### Phase 1: Product Knowledge Base v1

Purpose: create the backend source of truth that can power source-grounded AI.

Files to create/modify:

- `backend/app/db/base.py`
- `backend/app/db/session.py`
- `backend/app/models/question.py`
- `backend/app/models/knowledge.py`
- `backend/app/models/source.py`
- `backend/app/importers/canonical_qbank.py`
- `backend/app/domains/knowledge/router.py`
- `backend/tests/test_knowledge_import.py`
- `backend/tests/test_knowledge_routes.py`
- `scripts/verify_qbank_import.py`

Data imported:

- canonical questions;
- objective and essay question classification;
- accepted AI-washed explanations for objective questions;
- source occurrence records;
- tags;
- knowledge chunk links;
- question assets metadata.

First-release retrieval:

- structured lookup by question id/module/tag/source;
- text search over stem/explanation/knowledge chunks;
- pgvector embedding job prepared after structured import is stable.

Verification:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_knowledge_import.py backend/tests/test_knowledge_routes.py
python3 scripts/verify_qbank_import.py --report reports/question-bank/canonical-qbank-v1-report-20260702.md
```

Acceptance:

- product tables match canonical report counts;
- a question can return stem, answer, preferred explanation, tags, chunks, and source provenance;
- essay questions are imported without requiring stored explanation.

### Phase 2: Learner Identity And Memory

Purpose: make learning state real per user before claiming personalization.

Files to create/modify:

- `backend/app/models/user.py`
- `backend/app/models/learner.py`
- `backend/app/domains/auth/apple.py`
- `backend/app/domains/auth/router.py`
- `backend/app/domains/auth/deps.py`
- `backend/app/domains/memory/service.py`
- `backend/tests/test_auth_apple_contract.py`
- `backend/tests/test_learner_memory.py`
- `apps/ios/Gongkao/Gongkao/ProfileView` owner file if split from `FeatureViews.swift`

Data owned:

- users;
- learner profiles;
- target exam/region/position group;
- study availability;
- mastery estimates;
- practice answer history;
- wrong-book records;
- essay attempts and rewrite versions.

Verification:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_auth_apple_contract.py backend/tests/test_learner_memory.py
```

Acceptance:

- backend has a real user boundary;
- learner memory is private by user;
- no production anonymous fallback user exists.

### Phase 3: 行测 Real Practice Loop

Purpose: make the first half of the magic loop real.

Files to create/modify:

- `backend/app/domains/practice/router.py`
- `backend/app/domains/practice/service.py`
- `backend/app/domains/practice/schemas.py`
- `backend/app/domains/taskstream/service.py`
- `backend/tests/test_practice_loop.py`
- `backend/tests/test_taskstream_planner.py`
- `apps/ios/Gongkao/Gongkao/TaskExecutionView.swift`

Flow:

1. `GET /api/v1/taskstream/today` returns real tasks for the signed-in learner.
2. User starts a 行测 task.
3. Backend selects questions from product KB using target module, weak tags, and recent mistakes.
4. User submits answers.
5. Backend returns correctness, explanation, source grounding, error type, and next action.
6. Learner memory and wrong-book update.

Verification:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_practice_loop.py backend/tests/test_taskstream_planner.py
xcodebuild build -project apps/ios/Gongkao/Gongkao.xcodeproj -scheme Gongkao -destination 'platform=iOS Simulator,name=iPhone 17' CODE_SIGNING_ALLOWED=NO
```

Acceptance:

- the app can complete a real 行测 task from imported data;
- answer feedback cites product sources;
- same-type repair task can be generated from mistakes.

### Phase 4: 申论 Rewrite Loop

Purpose: make 申论 first-class without pretending to provide human-grade scoring.

Files to create/modify:

- `backend/app/models/essay.py`
- `backend/app/domains/essay/router.py`
- `backend/app/domains/essay/service.py`
- `backend/app/ai/prompts/essay_review.py`
- `backend/app/domains/ai/audit.py`
- `backend/tests/test_essay_rewrite_loop.py`
- `backend/tests/test_ai_usage_quota.py`
- `apps/ios/Gongkao/Gongkao/EssayRewriteView.swift`

Flow:

1. User opens a 申论 task from 今日 or 工作台.
2. App shows material, prompt, word limit, and answer editor.
3. User submits answer.
4. AI returns dimension review: 要点完整度, 概括抽象度, 结构层次, 语言压缩, 格式规范.
5. User rewrites.
6. App compares version 1 and version 2.
7. Result becomes a 申论复盘 object and updates tomorrow's plan.

Verification:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_essay_rewrite_loop.py backend/tests/test_ai_usage_quota.py
xcodebuild build -project apps/ios/Gongkao/Gongkao.xcodeproj -scheme Gongkao -destination 'platform=iOS Simulator,name=iPad Pro 13-inch (M5)' CODE_SIGNING_ALLOWED=NO
```

Acceptance:

- 申论 has a complete material -> answer -> review -> rewrite -> comparison loop;
- AI call is quota-gated and audited;
- AI output is stored as learner feedback, not canonical content.

### Phase 5: Workbench Objects And Replanning

Purpose: make the app feel like NotebookLM for 公考, but vertical and owned.

Files to create/modify:

- `backend/app/models/workbench.py`
- `backend/app/domains/workbench/router.py`
- `backend/app/domains/workbench/service.py`
- `backend/app/domains/taskstream/planner.py`
- `backend/tests/test_workbench_objects.py`
- `backend/tests/test_replanning.py`
- `apps/ios/Gongkao/Gongkao/WorkbenchView.swift`

Generated objects:

- 今日测验;
- 高频考点卡;
- 错因复盘;
- 申论重写稿;
- 岗位风险简报;
- 周计划.

Verification:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_workbench_objects.py backend/tests/test_replanning.py
scripts/full_verify.sh
```

Acceptance:

- completing tasks creates or updates workbench objects;
- tomorrow's taskstream changes based on today's evidence;
- iPad can show source, task, and AI rationale side by side.

### Phase 6: Commercial Guardrails

Purpose: make the first useful product safe to operate with cost control.

Files to create/modify:

- `backend/app/models/entitlement.py`
- `backend/app/domains/entitlements/router.py`
- `backend/app/domains/ai/quota.py`
- `backend/tests/test_entitlements.py`
- `backend/tests/test_ai_cost_audit.py`
- `apps/ios/Gongkao/Gongkao/PaywallView.swift`

Scope:

- server-side AI quota by feature/user/day;
- StoreKit product ids already fixed;
- paywall states for AI-heavy actions;
- AI interaction logs with feature, provider, model, token/image unit estimate, latency, and cost estimate.

Verification:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_entitlements.py backend/tests/test_ai_cost_audit.py
xcodebuild build -project apps/ios/Gongkao/Gongkao.xcodeproj -scheme Gongkao -destination 'platform=iOS Simulator,name=iPhone 17' CODE_SIGNING_ALLOWED=NO
```

Acceptance:

- free users cannot trigger unlimited AI cost;
- Pro entitlement controls higher limits;
- App Store transaction verification remains the payment authority.

## Recommended Immediate Next Slice

Start with Phase 0 and Phase 1, not UI polish.

Next concrete objective:

`Product Knowledge Base v1: import canonical qbank into product-owned backend tables and expose one source-grounded question API.`

Why this first:

- without it, AI explanations are theater;
- with it, 行测 practice, workbench objects, and task planning can become real;
- it uses existing high-value assets: 55,214 canonical questions, 104,762 source records, 562,539 tag links, and 23,103 knowledge chunks.

## Risks

- Import may expose mismatches between canonical report counts and product schema assumptions.
- pgvector setup may vary between local and production PostgreSQL.
- Apple login can slow local iteration, so auth tests must use signed-token fixtures while production routes remain Apple-only.
- AI essay review quality must be audited; do not market it as final human-grade scoring.

## Retirement

- Retire preview-only taskstream/workbench data once real APIs return product data.
- Retire old bridge-plan Flutter/email-auth/announcement-first instructions as active implementation authority.
- Retain old qbank exports and reports as audit artifacts.

## Success Metric For First Magic Loop

The milestone is complete when a signed-in test learner can:

1. open 今日;
2. complete one real 行测 task from imported qbank;
3. receive source-grounded explanation and error tag;
4. submit one 申论 answer and rewrite it after AI dimension feedback;
5. see a workbench object created from the session;
6. reopen 今日 and see tomorrow's plan changed because of the session.
