# Server-First Knowledge Base Implementation Plan

Date: 2026-07-03
Status: Ready for execution after baseline checkpoint

## Goal

Move heavy question-bank work off the local Mac and onto the new 火山云 host, while fixing the backend structure before product features grow. The next milestone is a product-owned knowledge base that can power the first real AI learning loop without becoming a pile of temporary patches.

Immediate executable objective:

`Server PostgreSQL + product knowledge schema + canonical qbank import + source-grounded question API`

## Architecture

The product is organized around stable owners, not pages:

1. `knowledge`: canonical questions, explanations, tags, source occurrences, knowledge chunks, and question assets.
2. `learners`: users, targets, memory, mastery state, answer history, and wrong-book records.
3. `taskstream`: AI-arranged daily tasks, task lifecycle, completion logs, and replanning inputs.
4. `practice`: 行测 question sessions, answer checking, source-grounded explanations, and mistake classification.
5. `essay`: 申论 materials, attempts, AI dimension review, rewrite comparison, and essay review objects.
6. `ai`: provider routing, prompts, retrieval context, quota, interaction audit, and cost estimates.
7. `entitlements`: StoreKit products, App Store transactions, subscription state, and AI quota gates.
8. `ops`: deployment, backups, log rotation, import verification, and server health.

The iOS app does not own learning logic. It renders taskstream/workbench contracts and submits learner actions. The backend owns product truth and learner memory.

## Tech Stack

- Server: 火山云 `101.96.198.253`, Ubuntu 24.04, `4C8G`, `60G` disk, `2M` public bandwidth.
- Runtime: Caddy, systemd, FastAPI, Python 3.12, PostgreSQL 16, pgvector, Redis/RQ when background jobs are introduced.
- Storage: PostgreSQL for structured data, TOS `gongkao-dazi-prod` for question assets, user uploads, and database backups.
- Client: native SwiftUI for iPhone and iPad.
- AI: configured Gemini reverse proxy for now, audited by feature/user/day, with no hidden fallback provider.

## Baseline/Authority Refs

- `docs/aegis/plans/2026-07-03-knowledge-base-to-magic-loop-master-plan.md`
- `docs/aegis/specs/2026-07-03-ai-agent-information-architecture.md`
- `docs/aegis/plans/2026-07-02-commercial-execution-master-plan.md`
- `reports/question-bank/canonical-qbank-v1-report-20260702.md`
- `tools/question-bank/canonical-qbank-v1.sql`
- `docs/ops/server-inventory.md`
- `data/question-bank-export/MANIFEST.md`

## Compatibility Boundary

- Do not continue old app code or old product behavior.
- Do not make the local Mac a production-like database host.
- Do not serve question images or user uploads through the 2M API server.
- Do not let staging schemas become runtime API authority.
- Do not add hidden anonymous users, fallback AI models, fallback auth, or degraded production paths.
- AI may write learner feedback, task suggestions, and audit logs; AI may not mutate canonical questions, canonical explanations, App Store entitlement state, or source occurrence evidence.

## Verification

Before claiming this phase complete:

```bash
python3 /Users/zxg/.codex/aegis/scripts/aegis-workspace.py check --root /Users/zxg/Documents/新项目
backend/.venv/bin/python -m pytest backend/tests
ssh -i ~/.ssh/gongkao_dazi_ed25519 root@101.96.198.253 'df -h / && systemctl is-active gongkao-dazi-api.service caddy postgresql'
curl -fsS https://kg.xingguoxian.cn/health
curl -fsS https://kg.xingguoxian.cn/api/v1/knowledge/questions/{canonical_question_id}
```

The final question API smoke test uses a real imported `canonical_question_id` captured from the server import verification output.

## Current Server Readiness

Observed on 2026-07-03:

- SSH works with `~/.ssh/gongkao_dazi_ed25519`.
- Hostname: `iv-yepqjczoqoobzsym7hrf`.
- Memory: `7.8GiB` total, about `7.1GiB` available.
- Disk: `/dev/vda2` `59G` total, about `54G` available.
- `gongkao-dazi-api.service`: active.
- Caddy: active.
- PostgreSQL: not installed yet.
- Existing app root: `/srv/gongkao-dazi`.

The local export is about `5.0G`, including a `297M` PostgreSQL custom dump and about `4.7G` of files/assets. This fits the server, but assets must be pushed to TOS or kept as import/reference material only; the API host should not become an image file server.

## Server Directory Contract

Use this server layout:

```text
/srv/gongkao-dazi/
  backend/                 # deployed FastAPI app
  scripts/                 # deployment and verification scripts
  ops/                     # caddy/systemd/env examples
  import-staging/          # temporary import files, not runtime source
    qbank/
    positions/
  backups/                 # short-lived local backup staging before TOS upload
  logs/                    # bounded app/import logs if not in journald
```

Non-negotiables:

- `/srv/gongkao-dazi/import-staging` may be deleted and rebuilt.
- Runtime API must read PostgreSQL product tables, not files under `import-staging`.
- Local database backups are temporary; durable backups go to TOS under `db-backups/`.
- Log retention must be bounded; do not let journald or import logs eat the 60G disk.

## PostgreSQL Schema Contract

Use separate schemas so staging, product runtime, and audit are never confused:

```text
source_qbank      # restored old export tables; staging only
canonical_qbank   # approved canonical derivation from existing SQL; staging/audit
product           # runtime product tables read by API
learner           # user-owned learning state and memory
ai_audit          # AI calls, quota, retrieval traces, cost estimates
commerce          # StoreKit products, transactions, entitlements
ops               # import runs, verification summaries, data version records
```

Runtime API read/write rules:

- Read from `product`, `learner`, `ai_audit`, `commerce`, and `ops`.
- Do not serve app behavior directly from `source_qbank`.
- Do not serve app behavior directly from `canonical_qbank` after product import is complete.
- Keep `source_qbank` and `canonical_qbank` as audit and reimport evidence.

## Product Table Groups

The first product knowledge release creates these table groups:

```text
product.questions
product.question_options
product.question_explanations
product.question_occurrences
product.exam_papers
product.question_assets
product.question_tags
product.knowledge_chunks
product.question_knowledge_links
ops.import_runs
ops.import_count_checks
```

The second release adds learner memory:

```text
learner.users
learner.apple_identities
learner.profiles
learner.targets
learner.mastery_estimates
learner.practice_sessions
learner.practice_answers
learner.wrong_book_items
learner.essay_attempts
learner.daily_task_logs
```

The third release adds AI and commerce controls:

```text
ai_audit.interaction_logs
ai_audit.retrieval_traces
ai_audit.usage_daily
commerce.iap_products
commerce.app_store_transactions
commerce.user_entitlements
commerce.entitlement_usage_daily
```

## Backend Code Structure

Refactor toward this structure before deeper feature work:

```text
backend/app/
  main.py
  api/
    router.py                 # versioned router composition only
  core/
    config.py
    errors.py
    security.py
  db/
    base.py
    session.py
    migrations/
  domains/
    knowledge/
      models.py
      schemas.py
      repository.py
      service.py
      router.py
      importer.py
    taskstream/
      schemas.py
      service.py
      planner.py
      router.py
    practice/
      schemas.py
      service.py
      router.py
    essay/
      schemas.py
      service.py
      router.py
    learners/
      models.py
      service.py
      router.py
    ai/
      provider.py
      retrieval.py
      quota.py
      audit.py
      prompts/
    entitlements/
      storekit.py
      service.py
      router.py
  importers/
    qbank_server_import.py
    position_server_import.py
```

Rules:

- `router.py` files translate HTTP to services; they do not own business logic.
- `service.py` files own workflow decisions.
- `repository.py` files own SQL query shape.
- `models.py` files own persistence mapping.
- `schemas.py` files own API contracts.
- Importers are one-way pipelines into product tables, not runtime adapters.

## Execution Slices

### Slice 0: Baseline And Server Posture

Purpose: prevent drift before installing stateful services.

Steps:

1. Record this plan in `docs/aegis/INDEX.md`.
2. Run Aegis workspace check.
3. Confirm server disk and service state.
4. Make a repository baseline commit before schema/importer changes.

Verification:

```bash
python3 /Users/zxg/.codex/aegis/scripts/aegis-workspace.py check --root /Users/zxg/Documents/新项目
git status --short
ssh -i ~/.ssh/gongkao_dazi_ed25519 root@101.96.198.253 'df -h / && free -h'
```

Acceptance:

- The active plan is indexed.
- The server has enough disk for a 5G import staging pass.
- There is a clean git baseline before product schema work.

### Slice 1: Install PostgreSQL And Hard Server Boundaries

Purpose: prepare the server as the only heavy import host.

Steps:

1. Install PostgreSQL 16 and contrib packages.
2. Install pgvector if available from package source; otherwise build/package it as an explicit ops step.
3. Create database `gongkao_dazi`.
4. Create least-privilege runtime and importer roles.
5. Create schemas `source_qbank`, `canonical_qbank`, `product`, `learner`, `ai_audit`, `commerce`, and `ops`.
6. Configure local-only PostgreSQL access; API connects over localhost.
7. Configure bounded journald retention.

Verification:

```bash
ssh -i ~/.ssh/gongkao_dazi_ed25519 root@101.96.198.253 'systemctl is-active postgresql && sudo -u postgres psql -d gongkao_dazi -c "\dn"'
```

Acceptance:

- PostgreSQL is active.
- Runtime schemas exist.
- PostgreSQL is not exposed as a public network service.

### Slice 2: Transfer Import Inputs To Server

Purpose: stop using the Mac as the data workhorse.

Steps:

1. Create `/srv/gongkao-dazi/import-staging/qbank`.
2. Transfer `data/question-bank-export/db/question-bank-subset-20260702.dump`.
3. Transfer SQL derivation files under `tools/question-bank/`.
4. Transfer count reports under `reports/question-bank/`.
5. Transfer only asset manifests needed for `question_assets` metadata.
6. Push image/file assets to TOS separately before any public serving path is added.

Verification:

```bash
ssh -i ~/.ssh/gongkao_dazi_ed25519 root@101.96.198.253 'du -h -d 2 /srv/gongkao-dazi/import-staging | sort -h'
```

Acceptance:

- The server has all DB/import inputs.
- The API host still does not serve asset files publicly.

### Slice 3: Restore Source And Build Canonical Audit Schema

Purpose: recreate the approved canonical qbank on the server.

Steps:

1. Restore the custom dump into a staging database or schema.
2. Run the approved canonical SQL derivation.
3. Store import summary rows under `ops.import_runs` and `ops.import_count_checks`.
4. Verify counts against `canonical-qbank-v1-report-20260702.md`.

Verification:

```bash
ssh -i ~/.ssh/gongkao_dazi_ed25519 root@101.96.198.253 'sudo -u postgres psql -d gongkao_dazi -c "select * from canonical_qbank.import_summary order by metric;"'
```

Acceptance:

- Canonical count is `55,214`.
- Objective count is `53,011`.
- Essay count is `2,203`.
- Objective explanations count is `53,011`.
- Knowledge chunk links count is `23,103`.
- No unreviewed semantic/fuzzy merge is introduced.

### Slice 4: Create Product Knowledge Tables And Importer

Purpose: separate runtime product data from canonical audit derivation.

Steps:

1. Add backend DB session and migration structure.
2. Add knowledge domain models and schemas.
3. Add importer from `canonical_qbank` to `product`.
4. Add count parity tests.
5. Add indexes for question lookup by id, module, type, tag, source, and text search.

Verification:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_knowledge_import.py
```

Acceptance:

- `product.questions` has `55,214` rows.
- Objective questions have explanations.
- Essay questions import without requiring explanations.
- Source occurrences, tags, assets, and chunks are linked through product IDs.

### Slice 5: Expose Source-Grounded Knowledge API

Purpose: give iOS and future AI retrieval one trusted question contract.

Steps:

1. Add `GET /api/v1/knowledge/questions/{question_id}`.
2. Add query endpoints for module/tag/source filters.
3. Return stem, options, answer, preferred explanation, source occurrences, tags, knowledge chunks, and asset references.
4. Add route contract tests.
5. Deploy backend to server and smoke-test HTTPS.

Verification:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_knowledge_routes.py
curl -fsS https://kg.xingguoxian.cn/api/v1/knowledge/questions/{canonical_question_id}
```

Acceptance:

- The API returns a real imported question with provenance.
- The response does not expose internal staging table names as product behavior.
- The API returns asset references as TOS/object keys or metadata, not local server file paths.

### Slice 6: Retire Preview Ownership

Purpose: prevent the UI shell from becoming a fake product.

Steps:

1. Keep preview endpoints only until iOS calls the real knowledge/taskstream route.
2. Replace static task data with backend-owned contracts slice by slice.
3. Remove or mark preview-only code when real taskstream/practice routes land.

Verification:

```bash
rg -n "preview|sample|static" backend/app apps/ios/Gongkao/Gongkao
```

Acceptance:

- No preview route owns real learning behavior.
- iOS surfaces are fed by backend product contracts.

## Disk And Bandwidth Policy

The 60G disk is enough for the first import, but only if we keep roles clean:

- PostgreSQL target for first phase: keep under `12-15G`.
- Import staging: acceptable temporarily around `5-8G`, then can be deleted.
- Logs: cap at `1-2G`.
- Backups: upload to TOS and remove local staging copy after verification.
- Images/assets: TOS only for product serving; not Caddy static serving.

The 2M bandwidth is acceptable for API JSON and App Store/TestFlight testing. It is not acceptable for image delivery, large backups, or batch asset downloads.

## Risk Controls

- If pgvector packaging is slow, structured search ships first; embedding jobs are prepared but not presented as a fallback AI path.
- If import count parity fails, stop and fix importer/schema; do not patch API responses.
- If server disk falls below `10G`, stop import work and move staging/backups to TOS before continuing.
- If the Gemini reverse proxy key remains the same as the key exposed in chat, rotate it before public production.

## Stop Condition

This phase is done only when the server hosts a real product knowledge API backed by imported product tables, with count parity verified and preview data no longer acting as product truth for the knowledge surface.
