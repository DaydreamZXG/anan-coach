# Gongkao App Commercial Execution Master Plan

## Goal

Build the new 公考学习 App from scratch as a commercial product, using the local
canonical question bank and existing position database as import sources.

This plan supersedes the execution authority of:

- `docs/aegis/plans/2026-07-02-gongkao-app-commercial-foundation.md`
- `docs/aegis/plans/2026-07-02-mvp-bridge-plan.md`

Those two plans remain useful source references. This master plan is the one to
execute because it resolves their conflicts:

- auth is an explicit prerequisite slice;
- the first commercial client is iOS-first, not cross-platform-first;
- learner auth uses Sign in with Apple, with admin auth separated;
- `gwy.db` is an import source only, not a runtime dependency;
- TypeScript target-position code is reference behavior only, not source of truth;
- no silent fallback or degraded product path is allowed;
- photo explanation cannot mutate canonical explanations;
- announcement and deadline features are deferred out of the core release plan;
- App Store IAP, entitlement usage, and AI cost usage are auditable by feature/day.

## Architecture

The product has these durable owners:

| Owner | Runtime authority |
|---|---|
| `backend` | FastAPI API, auth, product schema access, importers, practice, AI orchestration, audit |
| `apps/ios` | Native SwiftUI learner app for iPhone and iPad: Apple login, onboarding, task stream, practice, position matching, IAP paywall |
| `admin` | Next.js operations console: content review, position review, entitlement, AI-cost, and IAP audit |
| `database` | PostgreSQL product schema and Alembic migrations |
| `ops` | Local/dev/prod config, verification, logging, backup/deploy scripts |

Source-of-truth boundaries:

- Product runtime tables own app behavior after import.
- `canonical_qbank` is a staging/audit source for questions.
- `/Users/zxg/Documents/公考选岗计划/data/gwy.db` is a staging/audit source for positions.
- Old PeiPeiShua tables, old React Native code, and `target-position-engine` do not become runtime dependencies.
- AI output is learner guidance unless explicitly reviewed by a product/admin workflow.
- App Store transaction state is the only first-release payment authority.

## Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| API | FastAPI, Python 3.11+, SQLAlchemy 2, Alembic | Importers and AI orchestration stay Python-native |
| Database | PostgreSQL + pgvector | Product-owned schema, later RAG support |
| Background jobs | Redis + RQ | Import, embedding, AI batch jobs |
| iOS/iPadOS | SwiftUI, Swift Concurrency, StoreKit 2, AuthenticationServices | First release targets iPhone and iPad App Store review and IAP |
| Admin | Next.js + TypeScript | Dense operational review surfaces |
| Tests | pytest, XCTest/XCUITest, Node test/Playwright | Every slice owns fresh verification |

Model routes are explicit, logged, and feature-gated. The currently available
Gemini reverse proxy may be configured as the active provider for development
and early production, but it is not a hidden fallback path. Domestic model
providers can be added later through explicit provider configuration and release
verification. Every AI request records feature, user, provider, model, token or
image units, and estimated cost.

## Baseline/Authority Refs

- `/Users/zxg/Desktop/deep-research-report.md`
- `reports/question-bank/canonical-qbank-v1-report-20260702.md`
- `reports/question-bank/compaction-v1-report-20260702.md`
- `tools/question-bank/canonical-qbank-v1.sql`
- `docs/aegis/plans/2026-07-02-gongkao-app-commercial-foundation.md`
- `docs/aegis/plans/2026-07-02-mvp-bridge-plan.md`
- `/Users/zxg/Documents/公考选岗计划/data/gwy.db`
- `/Users/zxg/Documents/公考选岗计划/target-position-engine/src/types.ts`
- User instruction: commercial product, not smallest MVP, not patching, maintainability first.
- User commercial decisions on 2026-07-02: iOS-first, Sign in with Apple, iOS IAP, tight free AI quota, Gemini reverse proxy acceptable temporarily, announcements optional/deferred.

## Compatibility Boundary

- No production server writes in this plan.
- No migration of old users, sessions, payments, memberships, logs, or old app behavior.
- Existing exported assets and `gwy.db` are read-only import sources.
- New product schema may intentionally diverge from old schemas.
- Android, web learner client, third-party payment, SMS login, and email/password learner login are not compatibility boundaries for the first release.
- Any destructive action against live persistent state requires fresh scoped confirmation.

## Verification

Fresh verification before claiming each slice complete:

```bash
python3 -m pytest backend/tests
npm --prefix admin test
xcodebuild test -project apps/ios/Gongkao/Gongkao.xcodeproj -scheme Gongkao -destination 'platform=iOS Simulator,name=iPhone 16'
xcodebuild test -project apps/ios/Gongkao/Gongkao.xcodeproj -scheme Gongkao -destination 'platform=iOS Simulator,name=iPad Pro 13-inch (M4)'
python3 scripts/verify_import_counts.py
python3 scripts/verify_position_import.py --source /Users/zxg/Documents/公考选岗计划/data/gwy.db
python3 /Users/zxg/.codex/aegis/scripts/aegis-workspace.py check --root /Users/zxg/Documents/新项目
```

If a subproject does not exist yet, the slice that creates it owns its first
passing test command.

## Plan Basis

Question-bank source state:

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

Position source state, verified from `gwy.db`:

| Metric | Count |
|---|---:|
| positions | 60,471 |
| 2024 positions | 18,947 |
| 2025 positions | 20,810 |
| 2026 positions | 20,714 |
| position_score_history | 15,387 |
| system_score_history | 308 |
| systems | 7 |
| position_tags | 0 |

Commercial boundary decisions:

| Decision | First-release choice |
|---|---|
| Client platform | iOS-first native app |
| Device family | iPhone and iPad |
| Learner login | Sign in with Apple only |
| Admin login | Separate admin email/password auth |
| Payment | App Store IAP; product IDs fixed early, App Store Connect subscription records may be created late |
| AI provider | Configured provider route, with Gemini reverse proxy allowed while available |
| Free AI quota | Tight daily quotas, auditable by user/feature/day |
| Announcements/deadlines | Deferred; add only when product needs it |
| Commercial validation | Position-match conversion, daily-task retention, AI willingness to pay |

## BaselineUsageDraft

- Required baseline refs:
  - product research report;
  - canonical qbank report;
  - mvp bridge plan;
  - position SQLite database and TS type definitions.
- Acknowledged before plan refs:
  - old project discarded;
  - source occurrences, tags, and knowledge chunks are valuable;
  - no semantic duplicate merging;
  - commercial-grade maintainability;
  - iOS-first, Apple login, IAP, and tight AI quota are accepted product boundaries.
- Cited in plan refs:
  - canonical counts;
  - position counts;
  - AI-first product flow from research report.
- Missing refs:
  - none for execution planning.
- Decision: continue

## Requirement Ready Check

- Requirement source refs: user conversation, research report, canonical qbank report, bridge plan.
- Goals and scope refs: build new product foundation plus commercial bridge slices.
- User / scenario refs:
  - learner enters with target exam/region/time budget;
  - learner can practice, see explanations, review wrong answers, and receive daily tasks;
  - learner can search/match positions and see risk;
  - learner signs in through Apple and pays through App Store IAP;
  - operator can review content, transactions, entitlement state, and AI-cost usage;
  - entitlements gate AI usage from the first commercial build.
- Requirement item refs:
  - Sign in with Apple learner auth;
  - separate admin auth;
  - product schema;
  - question importer and practice loop;
  - position importer and matching;
  - onboarding diagnostics and task stream;
  - photo explanation;
  - App Store IAP;
  - entitlement, usage, and AI-cost audit.
- Acceptance / verification criteria refs:
  - count parity with source reports;
  - API tests for each domain;
  - iOS tests for learner flows;
  - admin tests for review flows;
  - direct old-table runtime reads forbidden.
- Open blocker questions:
  - none for this master plan.
- Decision: ready

## Architecture Integrity Lens

- Invariant: product runtime never reads old PeiPeiShua tables or `gwy.db`.
- Canonical owner / contract:
  - `questions`, `question_occurrences`, `question_explanations`, `question_assets`, `question_tags`, `knowledge_chunks`;
  - `positions`, `position_score_histories`, `position_score_links`, `system_score_histories`, `position_tags`;
  - `users`, `user_apple_identities`, `admin_users`, `auth_sessions`;
  - `practice_sessions`, `practice_answers`, `wrong_book_items`, `mastery_signals`;
  - `user_diagnostics`, `study_plans`, `daily_task_logs`;
  - `iap_products`, `app_store_transactions`, `app_store_notification_events`;
  - `user_entitlements`, `user_packs`, `entitlement_usage_daily`, `entitlement_audit_logs`, `ai_usage_daily`;
  - `ai_interaction_logs`.
- Responsibility overlap:
  - source imports preserve evidence;
  - product tables serve runtime;
  - AI tools read product data and write suggestions/audit, not canonical content.
- Higher-level simplification:
  - one product schema with importers beats runtime adapters over legacy sources.
- Falsifiers:
  - runtime code imports from `QuestionBankQuestion`;
  - runtime code reads `gwy.db`;
  - photo explanation writes canonical explanation;
  - learner auth uses email/password, SMS, or social login in the iOS app;
  - payment state is accepted without App Store signed transaction verification;
  - AI can change entitlements or prices.
- Verdict: proceed.

## Anti-Entropy Declaration

- Deletion Class: contract-carrying code and new derived product state; no live-state deletion.
- Old Path/Object: old PeiPeiShua runtime schema, `canonical_qbank` staging schema, `gwy.db`, old RN adapter.
- New Canonical Owner: product migrations and product domain services.
- Expected Preserved Behavior: questions, occurrences, explanations, assets, tags, chunks, positions, score history remain traceable.
- Expected Retired Behavior: old tables and SQLite adapter stop being runtime authority.
- External Boundary Touched: no.
- Source-of-Truth Data Risk: none in this plan because imports write only new product DB.
- User Confirmation Required: no.

## Plan Pressure Test

- Owner / contract / retirement: explicit by domain and import boundary.
- Architecture integrity / higher-level path: import to product schema, no legacy runtime adapter.
- Verification scope: count parity, API, iOS UI, Apple auth, StoreKit/IAP, AI boundary, entitlement audit, runtime old-source scan.
- Task executability: split into slices; each slice has tests and commands.
- Pressure result: proceed.

## Plan-Time Complexity Check

Complexity Budget:

- Artifact class: multi-service commercial foundation.
- Target files / artifacts: `backend/`, `apps/ios/`, `admin/`, `ops/`, `scripts/`, `docs/product/`.
- Current pressure: clean workspace, no product code yet.
- Projected post-change pressure: high across Apple auth, qbank, positions, AI, IAP, and entitlements.
- Budget result: at-risk but governed.
- Planned governance:
  - importer code separate from runtime services;
  - AI orchestration separate from canonical writes;
  - entitlement audit derived from verified App Store transaction state;
  - announcements/deadlines deferred out of the core release plan.

Plan-Time Complexity Check:

- Target files: new owner directories and migrations.
- Existing size / shape signals: no product app code exists.
- Owner fit: each slice owns one domain boundary.
- Add-in-place risk: high if backend becomes one large service file.
- Better file boundary: `backend/app/domains/<domain>/`, `backend/app/importers/`, `backend/app/ai/`.
- Recommendation: add owner files, test per domain, enforce old-source scan.

## Files

Create or modify across the plan:

- `backend/pyproject.toml`
- `backend/app/main.py`
- `backend/app/core/config.py`
- `backend/app/db/session.py`
- `backend/app/models/*.py`
- `backend/app/domains/auth/**`
- `backend/app/domains/questions/**`
- `backend/app/domains/practice/**`
- `backend/app/domains/review/**`
- `backend/app/domains/positions/**`
- `backend/app/domains/onboarding/**`
- `backend/app/domains/taskstream/**`
- `backend/app/domains/iap/**`
- `backend/app/domains/entitlements/**`
- `backend/app/importers/canonical_qbank.py`
- `backend/app/importers/gwy_position.py`
- `backend/app/ai/**`
- `backend/migrations/**`
- `backend/tests/**`
- `apps/ios/Gongkao/**`
- `apps/ios/GongkaoTests/**`
- `apps/ios/GongkaoUITests/**`
- `admin/**`
- `ops/docker-compose.dev.yml`
- `ops/env.example`
- `scripts/verify_import_counts.py`
- `scripts/verify_position_import.py`
- `scripts/full_verify.sh`
- `docs/product/data-contract.md`
- `docs/product/ai-boundary.md`
- `docs/product/pricing-model.md`
- `docs/product/iap-products.md`
- `docs/product/release-checklist.md`
- `.gitignore`

## Execution Slices

### Slice 0: Product Foundation And Question Bank

Purpose:

- scaffold the monorepo;
- define product schema;
- import `canonical_qbank`;
- implement question APIs, practice loop, AI boundary, iOS practice screen, and admin review queue.

Acceptance:

- product importer preserves `55,214` questions, `104,453` occurrences, `53,011` explanations, `23,103` chunks, and `82` review groups;
- objective questions use preferred AI-washed explanations when accepted by the canonical import; essay questions do not require explanations;
- product runtime has no direct old-table reads.

Verification:

```bash
python3 -m pytest backend/tests
npm --prefix admin test
xcodebuild test -project apps/ios/Gongkao/Gongkao.xcodeproj -scheme Gongkao -destination 'platform=iOS Simulator,name=iPhone 16'
python3 scripts/verify_import_counts.py
rg 'QuestionBankQuestion|QuestionBankQuestionOccurrence|canonical_qbank' backend/app apps/ios admin
```

Allowed `canonical_qbank` references after import:

- `backend/app/importers/canonical_qbank.py`
- `scripts/verify_import_counts.py`
- docs/tests explicitly marked as import verification.

### Slice 0.5: Authentication

Purpose:

- add Sign in with Apple learner auth, app sessions, and `get_current_user`;
- add separate admin credentials for the operations console;
- unblock all user-specific features.

Schema:

```sql
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  display_name TEXT,
  avatar_url TEXT,
  education TEXT,
  major TEXT,
  graduation_year INTEGER,
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE user_apple_identities (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  apple_sub TEXT NOT NULL UNIQUE,
  email TEXT,
  is_private_email BOOLEAN NOT NULL DEFAULT false,
  last_identity_token_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE auth_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  refresh_token_hash TEXT NOT NULL UNIQUE,
  device_id TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE admin_users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Auth contract:

- iOS obtains Apple identity token and authorization code through `AuthenticationServices`.
- Backend verifies Apple issuer, audience, signature, expiry, and nonce before creating or linking `users`.
- Learner email/password, SMS login, and third-party social login are not implemented in the first release.
- Admin email/password auth is scoped to `admin` only and does not create learner accounts.
- iPad layout is a first-release requirement: navigation, split views, practice surfaces, task stream, paywall, and photo explanation must be intentionally adapted rather than scaled iPhone layouts.

Verification:

```bash
cd backend && python3 -m pytest tests/test_apple_auth.py tests/test_admin_auth.py
xcodebuild test -project apps/ios/Gongkao/Gongkao.xcodeproj -scheme Gongkao -destination 'platform=iOS Simulator,name=iPhone 16' -only-testing:GongkaoTests/AuthFlowTests
npm --prefix admin test
```

### Slice 1: Position Engine

Purpose:

- import `/Users/zxg/Documents/公考选岗计划/data/gwy.db` to product PostgreSQL;
- expose position search/detail/match APIs;
- create position UI and admin review surface.

Critical correction from bridge plan:

- `target-position-engine/src/types.ts` is a reference behavior contract, not source of truth.
- `position_score_history` is code/year source data. Because `position_code` may map to multiple positions, do not force one score row into one `position_id`.
- Use a link/evidence table for score-to-position matching.

Schema shape:

```sql
CREATE TABLE positions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id TEXT NOT NULL UNIQUE,
  year INTEGER NOT NULL,
  position_code TEXT NOT NULL,
  name TEXT NOT NULL,
  department_full TEXT NOT NULL,
  system_code TEXT NOT NULL,
  region TEXT NOT NULL,
  city TEXT,
  paper_type TEXT NOT NULL,
  headcount INTEGER NOT NULL DEFAULT 1,
  education_min TEXT,
  major_required TEXT,
  political_req TEXT,
  hukou_req TEXT,
  service_min_yrs INTEGER,
  remark TEXT,
  raw_json JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (year, position_code, department_full)
);

CREATE TABLE position_score_histories (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  position_code TEXT NOT NULL,
  year INTEGER NOT NULL,
  entry_score_min REAL,
  entry_score_max REAL,
  admission_min REAL,
  admission_max REAL,
  signup_count INTEGER,
  ratio REAL,
  data_source TEXT,
  raw_json JSONB,
  UNIQUE (position_code, year)
);

CREATE TABLE position_score_links (
  position_id UUID NOT NULL REFERENCES positions(id),
  score_history_id UUID NOT NULL REFERENCES position_score_histories(id),
  match_method TEXT NOT NULL,
  confidence TEXT NOT NULL,
  evidence JSONB NOT NULL DEFAULT '{}',
  PRIMARY KEY (position_id, score_history_id)
);
```

Verification:

```bash
python3 scripts/verify_position_import.py --source /Users/zxg/Documents/公考选岗计划/data/gwy.db
cd backend && python3 -m pytest tests/test_position_import.py tests/test_position_api.py tests/test_position_matcher.py
xcodebuild test -project apps/ios/Gongkao/Gongkao.xcodeproj -scheme Gongkao -destination 'platform=iOS Simulator,name=iPhone 16' -only-testing:GongkaoTests/PositionFlowTests
npm --prefix admin test
```

Expected source counts:

- `positions=60471`
- `position_score_history=15387`
- `system_score_history=308`
- `systems=7`

### Slice 2: Cold-Start Diagnosis, Task Stream, Photo Explanation

Purpose:

- first-launch onboarding;
- 12-minute diagnostic quiz;
- study plan and daily task stream;
- photo-to-explanation using explicit vision route.

AI boundary:

- AI may write `study_plans`, `daily_task_logs`, and `ai_interaction_logs`.
- AI may update diagnostic estimates only through a versioned diagnostic update record.
- AI may not mutate `questions`, `question_explanations`, `positions`, `position_score_histories`, entitlements, or pricing.
- Photo explanation with no product question match is stored as an ephemeral AI explanation in audit/learning records, not canonical content.
- Each AI call records provider, model, feature, user, token/image units, latency, and estimated cost in `ai_interaction_logs` and aggregates into `ai_usage_daily`.

Free quota policy:

| Feature | Free tier | Pro tier |
|---|---:|---:|
| View canonical explanations | unlimited | unlimited |
| AI text explanation for unmatched/manual question | 3/day | 100/day |
| Photo explanation | 1/day | 30/day |
| Position AI risk narrative | 2/day | 50/day |
| Study-plan regeneration | 0/day after first onboarding plan | 10/month |

The quota values are server-configured and audited; the iOS client never
hard-codes commercial limits.

Photo feature behavior:

- `POST /api/v1/questions/photo-explain` extracts question text and tries to match product questions.
- If matched, return product question and canonical explanation.
- If unmatched and entitlement allows usage, return an AI-generated temporary explanation with `matched=false` and `canonical_question_id=null`.
- If entitlement blocks usage, return the paywall state and do not perform the model call.
- Text-input explanation is a separate explicit user flow, not a substitute path for failed photo recognition.

Verification:

```bash
cd backend && python3 -m pytest tests/test_onboarding.py tests/test_taskstream.py tests/test_ai_planner.py tests/test_photo_explain.py
xcodebuild test -project apps/ios/Gongkao/Gongkao.xcodeproj -scheme Gongkao -destination 'platform=iOS Simulator,name=iPhone 16' -only-testing:GongkaoTests/OnboardingFlowTests -only-testing:GongkaoTests/TaskStreamTests
```

### Slice 3: iOS IAP, Entitlements, And Commercial Gate

Purpose:

- implement StoreKit product contract and paywall surfaces;
- verify App Store signed transactions server-side;
- derive Pro entitlement state and enforce AI feature gates.

Scope correction from bridge plan:

- daily usage belongs in `entitlement_usage_daily`, not mutable counters on `user_entitlements`;
- payment path is not deferred: first release uses App Store IAP;
- App Store Connect subscription records can be created late, but product IDs, server config, entitlement semantics, and StoreKit client integration are planned and tested early;
- no web checkout, Alipay/WeChat Pay, manual payment, or hidden payment path in this slice.

Schema:

```sql
CREATE TABLE iap_products (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  platform TEXT NOT NULL DEFAULT 'ios',
  product_id TEXT NOT NULL UNIQUE,
  tier TEXT NOT NULL,
  billing_period TEXT NOT NULL,
  price_band TEXT NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE user_entitlements (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL UNIQUE REFERENCES users(id),
  tier TEXT NOT NULL DEFAULT 'free',
  subscription_start TIMESTAMPTZ,
  subscription_end TIMESTAMPTZ,
  features JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE app_store_transactions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  product_id TEXT NOT NULL REFERENCES iap_products(product_id),
  original_transaction_id TEXT NOT NULL,
  transaction_id TEXT NOT NULL UNIQUE,
  web_order_line_item_id TEXT,
  environment TEXT NOT NULL,
  purchase_date TIMESTAMPTZ NOT NULL,
  expires_date TIMESTAMPTZ,
  revocation_date TIMESTAMPTZ,
  signed_transaction_info TEXT NOT NULL,
  verification_status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE app_store_notification_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  notification_uuid TEXT NOT NULL UNIQUE,
  notification_type TEXT NOT NULL,
  subtype TEXT,
  signed_payload TEXT NOT NULL,
  processed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE entitlement_usage_daily (
  user_id UUID NOT NULL REFERENCES users(id),
  feature TEXT NOT NULL,
  usage_date DATE NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, feature, usage_date)
);

CREATE TABLE entitlement_audit_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  action TEXT NOT NULL,
  feature TEXT,
  allowed BOOLEAN,
  metadata JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Product configuration:

- `gongkao_ai_pro_monthly`: AI Pro monthly, price target `¥29-39`.
- `gongkao_ai_pro_yearly`: AI Pro yearly, price target `¥199-299`.
- Exact App Store price tiers are recorded in `docs/product/iap-products.md` after App Store Connect setup.
- Product IDs, limits, and visible prices are server-configured and versioned; the iOS app renders server product metadata plus StoreKit product data.

Verification:

```bash
cd backend && python3 -m pytest tests/test_entitlements.py tests/test_feature_gating.py tests/test_app_store_iap.py tests/test_ai_usage_limits.py
xcodebuild test -project apps/ios/Gongkao/Gongkao.xcodeproj -scheme Gongkao -destination 'platform=iOS Simulator,name=iPhone 16' -only-testing:GongkaoTests/PaywallFlowTests
npm --prefix admin test
```

### Slice 4: Full Verification And Release Readiness

Purpose:

- add `scripts/full_verify.sh`, env examples, and release checklist.

Verification:

```bash
bash scripts/full_verify.sh
```

The script must run:

- backend tests;
- admin tests;
- iOS and iPadOS tests;
- question import count verification;
- position import count verification;
- old-source runtime scan;
- StoreKit/IAP contract tests;
- AI usage-limit and cost-audit tests;
- iPad layout smoke tests for task stream, practice, position matching, paywall, and photo explanation;
- Aegis workspace check.

## Execution Order

```text
Slice 0   Product foundation and question bank
Slice 0.5 Auth
Slice 1   Position engine
Slice 2   Cold-start diagnosis, task stream, photo explanation
Slice 3   iOS IAP, entitlements, and commercial gate
Slice 4   Full verification and release readiness
```

Parallelism:

- Slice 1 importer can begin while Slice 0 is being scaffolded once product DB conventions are stable.
- Slice 3 backend IAP contract tests can begin after auth and entitlement schema exist.
- Slice 3 paywall UI should wait until at least one gated value from Slices 1-2 is functional.

## Deferred Scope

Announcements, deadline reminders, automated scraping, push notifications,
Android, web learner client, and non-Apple payments are outside this execution
plan. They require their own plan when the product evidence says they are worth
the added operational surface.

## Risks

| Risk | Mitigation |
|---|---|
| Auth was absent from the earlier foundation plan | Make Slice 0.5 mandatory before user-specific slices |
| Position code duplicates make direct score FK unsafe | Use code/year score table plus `position_score_links` evidence table |
| Photo explanation could pollute canonical data | Store unmatched explanations only as ephemeral AI/audit records |
| AI study plans can be low-quality | Validate plan JSON schema and store confidence/audit records |
| AI cost can run away | Enforce server-side quotas before model calls and audit cost by user/feature/day |
| App Store payment state can drift | Derive entitlement from verified App Store transactions and server notifications |
| IAP products are created late | Keep product IDs and entitlement contract stable in code/config; use StoreKit testing before App Store records are complete |
| iPad becomes stretched iPhone UI | Add explicit iPad layout requirements and simulator smoke tests before release |
| Entitlement counters can be corrupted by daily resets | Use per-user/feature/date usage table |
| App Review or IAP compliance changes | Keep iOS auth/payment native and document App Store product IDs, review notes, and entitlement rules |
| Over-broad scope | Execute slices in order and verify each before expanding |

## Retirement

- Retire old PeiPeiShua runtime schema as product authority.
- Retire `canonical_qbank` as runtime authority after product import parity.
- Retire `gwy.db` as runtime authority after position import parity.
- Retain old exports, `canonical_qbank`, `gwy.db`, and TS adapter code as audit/reference artifacts.
- Runtime drift scan:

```bash
rg 'QuestionBankQuestion|QuestionBankQuestionOccurrence|canonical_qbank|gwy\\.db|SqliteTargetPositionEngineAdapter' backend/app apps/ios admin
```

Allowed matches:

- importers;
- verification scripts;
- docs/tests explicitly marked as source-import checks.

## Success Criteria

After all slices:

1. User registers/logs in through Sign in with Apple.
2. User completes onboarding and diagnostic.
3. User searches/matches positions from imported 60,471-position dataset.
4. User opens the app to a daily task stream.
5. User practices questions from the imported 55,214-question bank.
6. User sees canonical explanations and occurrence/source provenance when needed.
7. Wrong answers create review/mastery signals.
8. User can use photo explanation with clear matched/unmatched state.
9. User can purchase AI Pro through App Store IAP and receive server-verified entitlement.
10. Admin can review the 82 merge-risk groups, IAP transactions, entitlement state, and AI-cost audit.
11. Free tier has real value; AI Pro and packs are gated by auditable entitlements and tight cost limits.
