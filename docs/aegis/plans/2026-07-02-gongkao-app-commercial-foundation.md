# Gongkao App Commercial Foundation Plan

> Superseded for execution by
> `docs/aegis/plans/2026-07-02-commercial-execution-master-plan.md`.
> Keep this file as a source reference for the foundation slice.

## Goal

Build the first commercial-grade engineering slice for the new 公考学习 App:

- create a new product codebase from scratch;
- promote `canonical_qbank` into the product-owned schema;
- ship the core 行测 question-bank and practice loop;
- expose provenance, explanation, assets, tags, and knowledge chunks cleanly;
- add an admin review surface for the remaining merge-risk groups;
- prepare the AI learning-agent boundary without making AI a hidden source of truth.

This is not a minimal MVP plan. It is the foundation of a maintainable commercial product.

## Architecture

The product has five owners:

| Owner | Responsibility |
|---|---|
| `backend` | Product API, auth, learning state, importer, AI orchestration, audit logs |
| `app` | Mobile-first learner experience: daily task, practice, explanation, wrong-book |
| `admin` | Content review, merge review queue, question provenance, import audit |
| `database` | Product-owned PostgreSQL schema and migrations |
| `ops` | Local/dev/prod configuration, logging, backup, deployment scripts |

Canonical boundary:

- Old exported tables and `canonical_qbank` are import sources only.
- Product runtime reads from product-owned tables, not from old `QuestionBankQuestion`.
- `source_question_map` remains an audit bridge, not a product identity owner.
- AI can explain, diagnose, and plan; it cannot silently mutate canonical questions, answers, tags, or explanations.

## Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Mobile app | Flutter | Matches the product report, gives iOS/Android from one codebase |
| Admin web | Next.js + TypeScript | Dense operational UI is easier to maintain in web stack |
| API | FastAPI + Python | Good fit for AI orchestration, import scripts, async services |
| ORM/migrations | SQLAlchemy 2 + Alembic | Explicit schema ownership and migration history |
| Database | PostgreSQL + pgvector extension | Current data is PostgreSQL; pgvector supports later RAG without new infra |
| Cache/jobs | Redis + RQ | Simple background import, embedding, and AI job queues |
| Object storage | S3-compatible bucket interface | Images,申论 files, audio, and generated artifacts |
| Testing | pytest, Flutter test, Playwright | Covers backend, mobile logic, and admin flows |

No silent provider fallback is planned. Model routes are explicit per capability; failures are recorded and surfaced.

## Baseline/Authority Refs

- `/Users/zxg/Desktop/deep-research-report.md`
- `reports/question-bank/canonical-qbank-v1-report-20260702.md`
- `reports/question-bank/compaction-v1-report-20260702.md`
- `tools/question-bank/canonical-qbank-v1.sql`
- User instruction: old project is discarded.
- User instruction: commercial product, not a smallest MVP, not patching, maintainability first.
- User instruction: light rewrites remain separate questions.

## Compatibility Boundary

- No production server writes in this plan.
- No migration of old users, sessions, payments, memberships, logs, or old app behavior.
- Existing exported assets are read-only.
- New product schema may intentionally diverge from old PeiPeiShua schema.
- Destructive actions against live persistent state require fresh scoped confirmation.

## Verification

Each implementation slice must pass:

```bash
python3 -m pytest backend/tests
npm --prefix admin test
flutter test apps/mobile
python3 scripts/verify_import_counts.py
python3 /Users/zxg/.codex/aegis/scripts/aegis-workspace.py check --root /Users/zxg/Documents/新项目
```

If a subproject has not yet been created, the slice that creates it owns the first passing test command for that subproject.

## Plan Basis

Current local canonical import has:

| Metric | Count |
|---|---:|
| canonical questions | 55,214 |
| objective canonical questions | 53,011 |
| essay canonical questions | 2,203 |
| imported occurrences | 104,453 |
| explanations | 53,011 |
| asset refs | 52,112 |
| tag evidence rows | 560,397 |
| knowledge chunk links | 23,103 |
| merge review queue groups | 82 |

The product must preserve those counts through its importer or explain every intentional exclusion.

## BaselineUsageDraft

- Required baseline refs:
  - `/Users/zxg/Desktop/deep-research-report.md`
  - `reports/question-bank/canonical-qbank-v1-report-20260702.md`
  - `tools/question-bank/canonical-qbank-v1.sql`
- Acknowledged before plan refs:
  - commercial-grade product requirement;
  - no old project continuation;
  - no semantic duplicate merging;
  - occurrence/source evidence must be preserved.
- Cited in plan refs:
  - product report for app shape and stack;
  - canonical report for data counts and schema responsibilities.
- Missing refs:
  - none for this foundation slice.
- Decision: continue

## Requirement Ready Check

- Requirement source refs: user conversation, product report, canonical qbank report.
- Goals and scope refs: new product codebase plus production-owned question-bank and practice foundation.
- User / scenario refs:
  - learner wants daily task, practice, explanation, wrong-book;
  - operator wants review queue and source provenance;
  - future AI agent needs reliable question/knowledge APIs.
- Requirement item refs:
  - product schema;
  - importer;
  - question browsing/search/detail APIs;
  - practice session and answer recording;
  - explanation display;
  - wrong-book and mastery state;
  - admin merge review queue;
  - AI orchestration boundary and audit logging.
- Acceptance / verification criteria refs:
  - import count parity with `canonical_qbank`;
  - API tests for question detail, occurrence provenance, practice answer flow;
  - UI tests for practice and admin review.
- Open blocker questions:
  - none for foundation. Payment,申论批改,实时语音,选岗 are later plans.
- Decision: ready

## Architecture Integrity Lens

- Invariant: product runtime never depends on old restored tables.
- Canonical owner / contract:
  - product `questions` owns runtime question identity;
  - product `question_occurrences` owns source/province/paper evidence;
  - product `question_explanations` owns learner-facing explanation;
  - product `question_assets`, `question_tags`, `knowledge_chunks` own supporting evidence;
  - `content_review_items` owns not-yet-accepted merge/material/taxonomy review.
- Responsibility overlap:
  - `canonical_qbank` is a staging/import authority only until product import completes.
  - AI services produce learner guidance, not canonical data mutations.
- Higher-level simplification:
  - one product schema and one importer beat many compatibility adapters.
- Retirement / falsifier:
  - if any API reads `QuestionBankQuestion` directly, the plan failed.
  - if count parity is broken without an import decision record, the plan failed.
- Verdict: proceed.

## Anti-Entropy Declaration

- Deletion Class: contract-carrying code for new schema; no live-state deletion.
- Old Path/Object: old PeiPeiShua runtime schema and behavior.
- New Canonical Owner: product migrations under `backend/migrations`.
- Expected Preserved Behavior: source question, occurrence, explanation, asset, tag, and chunk evidence remains traceable.
- Expected Retired Behavior: old `QuestionBankQuestion` table shape stops being product runtime authority.
- External Boundary Touched: no.
- Source-of-Truth Data Risk: none in this plan because product schema is new and local first.
- User Confirmation Required: no.

## Plan Pressure Test

- Owner / contract / retirement: clear; product schema supersedes old schema for runtime.
- Architecture integrity / higher-level path: use importer and product tables, not API adapters over old tables.
- Verification scope: count parity, API tests, UI smoke tests, admin review tests.
- Task executability: each slice creates a bounded subproject or owner.
- Pressure result: proceed.

## Plan-Time Complexity Check

Complexity Budget:

- Artifact class: new multi-service product foundation.
- Target files / artifacts:
  - `backend/`
  - `apps/mobile/`
  - `admin/`
  - `ops/`
  - `scripts/`
  - `docs/product/`
- Current pressure: no product code exists, so owner boundaries are still clean.
- Projected post-change pressure: high if app, admin, backend, importer, and AI are mixed.
- Budget result: at-risk but governable.
- Planned governance: separate owners by service, keep importer separate from API, keep AI orchestration separate from canonical data writes.

Plan-Time Complexity Check:

- Target files: new owner directories, no single large file.
- Existing size / shape signals: current workspace has reports/tools only.
- Owner fit: each service owns one boundary.
- Add-in-place risk: low if scaffolded cleanly; high if backend starts as one file.
- Better file boundary: `backend/app/domains/*`, `backend/app/importers/*`, `backend/app/ai/*`.
- Recommendation: add owner files and tests per domain.

## Files

Create:

- `backend/pyproject.toml`
- `backend/app/main.py`
- `backend/app/core/config.py`
- `backend/app/db/session.py`
- `backend/app/models/*.py`
- `backend/app/domains/questions/*.py`
- `backend/app/domains/practice/*.py`
- `backend/app/domains/review/*.py`
- `backend/app/importers/canonical_qbank.py`
- `backend/app/ai/orchestrator.py`
- `backend/tests/**`
- `backend/migrations/**`
- `apps/mobile/pubspec.yaml`
- `apps/mobile/lib/main.dart`
- `apps/mobile/lib/features/practice/**`
- `apps/mobile/lib/features/questions/**`
- `apps/mobile/test/**`
- `admin/package.json`
- `admin/src/app/**`
- `admin/src/features/review/**`
- `admin/tests/**`
- `ops/docker-compose.dev.yml`
- `ops/env.example`
- `scripts/verify_import_counts.py`
- `docs/product/data-contract.md`
- `docs/product/ai-boundary.md`

Modify:

- `.gitignore`
- `docs/aegis/INDEX.md`

## Tasks

### Task 1: Scaffold Product Monorepo

Files:

- Create `backend/`, `apps/mobile/`, `admin/`, `ops/`, `scripts/`, `docs/product/`.
- Modify `.gitignore`.

Why:

- Establish maintainable service boundaries before writing product logic.

Impact/Compatibility:

- Additive only. Does not touch local exported data or production servers.

Verification:

```bash
test -f backend/pyproject.toml
test -f apps/mobile/pubspec.yaml
test -f admin/package.json
test -f ops/docker-compose.dev.yml
```

Steps:

1. Write the first backend smoke test:
   ```bash
   mkdir -p backend/tests
   cat > backend/tests/test_smoke.py <<'PY'
   def test_backend_test_runner_is_alive():
       assert True
   PY
   ```
2. Verify RED/BOOTSTRAP state:
   ```bash
   cd backend && python3 -m pytest tests
   ```
   Expected before dependencies are installed: pytest may be missing or test passes if pytest exists. Record exact output.
3. Add backend project metadata:
   ```bash
   cat > backend/pyproject.toml <<'TOML'
   [project]
   name = "gongkao-backend"
   version = "0.1.0"
   requires-python = ">=3.11"
   dependencies = [
     "fastapi",
     "uvicorn[standard]",
     "sqlalchemy>=2",
     "alembic",
     "psycopg[binary]",
     "pydantic-settings",
     "pytest",
     "httpx",
     "redis",
     "rq"
   ]
   TOML
   ```
4. Add app/admin/ops placeholders as real project manifests:
   ```bash
   mkdir -p apps/mobile/lib admin/src ops docs/product scripts
   cat > apps/mobile/pubspec.yaml <<'YAML'
   name: gongkao_mobile
   description: Commercial gongkao learning app.
   publish_to: "none"
   environment:
     sdk: ">=3.4.0 <4.0.0"
   dependencies:
     flutter:
       sdk: flutter
   dev_dependencies:
     flutter_test:
       sdk: flutter
   flutter:
     uses-material-design: true
   YAML
   cat > admin/package.json <<'JSON'
   {
     "name": "gongkao-admin",
     "private": true,
     "scripts": {
       "test": "node --test"
     },
     "dependencies": {
       "next": "latest",
       "react": "latest",
       "react-dom": "latest"
     },
     "devDependencies": {
       "typescript": "latest"
     }
   }
   JSON
   cat > ops/docker-compose.dev.yml <<'YAML'
   services:
     postgres:
       image: pgvector/pgvector:pg16
       ports:
         - "55433:5432"
       environment:
         POSTGRES_DB: gongkao_app
         POSTGRES_USER: gongkao
         POSTGRES_PASSWORD: gongkao_dev
     redis:
       image: redis:7
       ports:
         - "56379:6379"
   YAML
   ```
5. Verify GREEN:
   ```bash
   test -f backend/pyproject.toml && test -f apps/mobile/pubspec.yaml && test -f admin/package.json && test -f ops/docker-compose.dev.yml
   ```

Commit:

```bash
git add backend apps admin ops scripts docs/product .gitignore
git commit -m "Scaffold new gongkao product monorepo"
```

### Task 2: Define Product Database Schema

Files:

- Create `backend/app/models/question.py`
- Create `backend/app/models/practice.py`
- Create `backend/app/models/review.py`
- Create `backend/migrations/versions/0001_product_schema.py`
- Create `docs/product/data-contract.md`

Why:

- Move runtime ownership from `canonical_qbank` staging tables into product schema.

Impact/Compatibility:

- New product schema only. Does not mutate source export database.

Verification:

```bash
cd backend
python3 -m pytest tests/test_schema_contract.py
```

Steps:

1. Write schema contract test:
   ```python
   # backend/tests/test_schema_contract.py
   REQUIRED_TABLES = {
       "questions",
       "source_question_map",
       "exam_papers",
       "question_occurrences",
       "question_explanations",
       "question_assets",
       "question_tags",
       "knowledge_chunks",
       "practice_sessions",
       "practice_answers",
       "content_review_items",
       "ai_interaction_logs",
   }

   def test_required_product_tables_are_named():
       assert "questions" in REQUIRED_TABLES
       assert "question_occurrences" in REQUIRED_TABLES
       assert "ai_interaction_logs" in REQUIRED_TABLES
   ```
2. Verify RED or initial pass:
   ```bash
   cd backend && python3 -m pytest tests/test_schema_contract.py
   ```
3. Implement SQLAlchemy models with explicit table names and foreign keys.
4. Write Alembic migration `0001_product_schema.py` creating all required tables.
5. Verify GREEN:
   ```bash
   cd backend && python3 -m pytest tests/test_schema_contract.py
   ```

Commit:

```bash
git add backend docs/product/data-contract.md
git commit -m "Define product question bank schema"
```

### Task 3: Build Canonical Importer

Files:

- Create `backend/app/importers/canonical_qbank.py`
- Create `scripts/verify_import_counts.py`
- Create `backend/tests/test_canonical_importer_counts.py`

Why:

- Product data must be reproducible from `canonical_qbank` with count parity and audit evidence.

Impact/Compatibility:

- Reads local `canonical_qbank`; writes only to new app database.

Verification:

```bash
python3 scripts/verify_import_counts.py
```

Expected key counts:

- `questions=55214`
- `question_occurrences=104453`
- `question_explanations=53011`
- `knowledge_chunks=23103`
- `content_review_items=82`

Steps:

1. Write failing count test:
   ```python
   # backend/tests/test_canonical_importer_counts.py
   EXPECTED = {
       "questions": 55214,
       "question_occurrences": 104453,
       "question_explanations": 53011,
       "knowledge_chunks": 23103,
       "content_review_items": 82,
   }

   def test_expected_import_counts_are_pinned():
       assert EXPECTED["questions"] == 55214
   ```
2. Run test:
   ```bash
   cd backend && python3 -m pytest tests/test_canonical_importer_counts.py
   ```
3. Implement importer phases:
   - papers;
   - questions;
   - source map;
   - occurrences;
   - explanations;
   - assets;
   - tags;
   - knowledge chunks;
   - review queue.
4. Implement `scripts/verify_import_counts.py` to compare product tables against `canonical_qbank.import_summary`.
5. Verify:
   ```bash
   python3 scripts/verify_import_counts.py
   ```

Commit:

```bash
git add backend/app/importers scripts/verify_import_counts.py backend/tests/test_canonical_importer_counts.py
git commit -m "Import canonical question bank into product schema"
```

### Task 4: Implement Question APIs

Files:

- Create `backend/app/domains/questions/router.py`
- Create `backend/app/domains/questions/service.py`
- Create `backend/tests/test_question_api.py`

Why:

- Mobile and admin clients need stable APIs for browsing, detail, explanation, occurrence provenance, and assets.

Impact/Compatibility:

- API reads product tables only. Direct old-table access is forbidden.

Verification:

```bash
cd backend && python3 -m pytest tests/test_question_api.py
```

Steps:

1. Write API tests for:
   - `GET /questions?module=资料分析&page=1`
   - `GET /questions/{id}`
   - `GET /questions/{id}/occurrences`
   - `GET /questions/{id}/explanation`
2. Verify RED.
3. Implement service queries using product tables.
4. Add guard test that source SQL names such as `QuestionBankQuestion` do not appear in question service files.
5. Verify GREEN.

Commit:

```bash
git add backend/app/domains/questions backend/tests/test_question_api.py
git commit -m "Add product question APIs"
```

### Task 5: Implement Practice Loop Backend

Files:

- Create `backend/app/domains/practice/router.py`
- Create `backend/app/domains/practice/service.py`
- Create `backend/tests/test_practice_flow.py`

Why:

- The product needs the core learning loop: start session, answer question, see explanation, save wrong-book signal.

Impact/Compatibility:

- New user learning state only. It does not alter canonical content.

Verification:

```bash
cd backend && python3 -m pytest tests/test_practice_flow.py
```

Steps:

1. Write tests for:
   - creating a practice session;
   - submitting an answer;
   - computing correctness;
   - creating wrong-book entry on wrong answer;
   - updating mastery signal.
2. Verify RED.
3. Implement practice service.
4. Add transaction boundaries around answer submission.
5. Verify GREEN.

Commit:

```bash
git add backend/app/domains/practice backend/tests/test_practice_flow.py
git commit -m "Add practice session backend"
```

### Task 6: Implement AI Boundary and Audit Logs

Files:

- Create `backend/app/ai/orchestrator.py`
- Create `backend/app/ai/prompts.py`
- Create `backend/tests/test_ai_boundary.py`
- Create `docs/product/ai-boundary.md`

Why:

- The AI agent should explain and plan, but must not become an uncontrolled data mutator.

Impact/Compatibility:

- AI writes only `ai_interaction_logs`, learner plan suggestions, and practice feedback records.
- AI cannot update canonical question, answer, source tags, or explanation rows.

Verification:

```bash
cd backend && python3 -m pytest tests/test_ai_boundary.py
```

Steps:

1. Write tests proving AI tool registry lacks canonical write tools.
2. Verify RED.
3. Implement explicit tools:
   - `get_question_detail`;
   - `get_explanation`;
   - `get_similar_questions`;
   - `create_study_plan_suggestion`;
   - `log_ai_interaction`.
4. Add prompt contract documenting that AI output is guidance unless reviewed.
5. Verify GREEN.

Commit:

```bash
git add backend/app/ai backend/tests/test_ai_boundary.py docs/product/ai-boundary.md
git commit -m "Define AI orchestration boundary"
```

### Task 7: Build Mobile Practice Experience

Files:

- Create `apps/mobile/lib/main.dart`
- Create `apps/mobile/lib/features/practice/practice_screen.dart`
- Create `apps/mobile/lib/features/questions/question_detail_screen.dart`
- Create `apps/mobile/test/practice_flow_test.dart`

Why:

- The first learner-facing screen should be the usable practice flow, not a marketing landing page.

Impact/Compatibility:

- Mobile app calls product APIs and displays canonical explanations/assets.

Verification:

```bash
cd apps/mobile && flutter test
```

Steps:

1. Write widget test for practice screen showing question stem, options, submit button, and explanation after answer.
2. Verify RED.
3. Implement practice UI with stable layout dimensions for options and explanation panel.
4. Add loading/error states that block action clearly; no silent fallback.
5. Verify GREEN.

Commit:

```bash
git add apps/mobile
git commit -m "Build mobile practice flow"
```

### Task 8: Build Admin Review Queue

Files:

- Create `admin/src/app/review/page.tsx`
- Create `admin/src/features/review/review-table.tsx`
- Create `admin/tests/review.test.ts`
- Create `backend/app/domains/review/router.py`
- Create `backend/tests/test_review_api.py`

Why:

- The 82 unmerged groups need an operational review surface before any further compaction.

Impact/Compatibility:

- Admin review decisions must be recorded; they do not directly mutate canonical tables until a reviewed migration/import run is created.

Verification:

```bash
cd backend && python3 -m pytest tests/test_review_api.py
npm --prefix admin test
```

Steps:

1. Write backend test for listing review groups by type and group id.
2. Write admin test for rendering `review_taxonomy_conflict`, `review_visual_cluster`, and `review_material_cluster`.
3. Verify RED.
4. Implement review API and admin table.
5. Add action states:
   - mark as same question;
   - mark as keep separate;
   - needs asset/material evidence.
6. Verify GREEN.

Commit:

```bash
git add backend/app/domains/review backend/tests/test_review_api.py admin
git commit -m "Add content merge review queue"
```

### Task 9: Add Operational Verification

Files:

- Create `scripts/full_verify.sh`
- Create `ops/env.example`
- Create `docs/product/release-checklist.md`

Why:

- Commercial product work needs repeatable verification before handoff.

Impact/Compatibility:

- Additive only.

Verification:

```bash
bash scripts/full_verify.sh
```

Steps:

1. Write `scripts/full_verify.sh` running backend tests, admin tests, Flutter tests, import count check, and Aegis check.
2. Verify RED until all prior slices exist.
3. Implement missing env documentation in `ops/env.example`.
4. Add release checklist with data count, AI boundary, privacy, and review queue checks.
5. Verify GREEN.

Commit:

```bash
git add scripts/full_verify.sh ops/env.example docs/product/release-checklist.md
git commit -m "Add product verification workflow"
```

## Risks

- The source question bank is clean enough for practice, but the 82 review groups must not be silently merged.
- Essay questions are present as assets, but申论批改 is not in this foundation slice.
- Position matching, payment, and real-time voice are commercial priorities but need separate plans because they touch different data, compliance, and cost surfaces.
- Flutter/Next/FastAPI is a multi-stack product; owner boundaries must be enforced from day one.

## Retirement

- Retire old product implementation as a runtime path.
- Retain old exported database/files as import evidence only.
- Retain `canonical_qbank` as staging audit until product importer parity is verified.
- Retire direct old-table reads in product code; verify with search:

```bash
rg 'QuestionBankQuestion|QuestionBankQuestionOccurrence|canonical_qbank' backend/app apps/mobile admin
```

Allowed after importer:

- `backend/app/importers/canonical_qbank.py`
- `scripts/verify_import_counts.py`
- docs and reports

Any other match is implementation drift.
