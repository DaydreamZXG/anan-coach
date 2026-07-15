# Gongkao App MVP Bridge Plan

> Superseded for execution by
> `docs/aegis/plans/2026-07-02-commercial-execution-master-plan.md`.
> Keep this file as a source reference for bridge-slice ideas and position data inventory.

## Goal

Supplement the [commercial foundation plan](2026-07-02-gongkao-app-commercial-foundation.md) (Slice 0: database + practice loop + AI boundary) with the missing slices that turn a question bank into the report-defined commercial MVP.

The deep-research report defines the MVP as five deliverables:

| Report MVP Item | Slice 0 Status | This Plan |
|---|---|---|
| 错题复盘 (wrong-book review) | ✅ Done (practice loop + mastery signal) | — |
| 选岗智能匹配 (position matching) | ❌ Not in scope | **Slice 1** — Integrate existing 60K position data |
| 冷启动诊断 (cold-start diagnosis) | ❌ Not in scope | **Slice 2** — Onboarding quiz + AI plan generation |
| 每日任务流 (daily task stream) | ❌ Not in scope | **Slice 2** — AI-orchestrated daily task screen |
| 拍题讲解 (photo → explanation) | ❌ Only text explanation API | **Slice 2** — Add vision model + photo upload |

And the report's commercialization model:

| Report Tier | This Plan |
|---|---|
| Layer 1: Free acquisition (announcements, position matching, limited AI) | **Slice 3** (announcements) + **Slice 4** (entitlements) |
| Layer 2: AI Pro subscription | **Slice 4** — Subscription scaffold + feature gating |
| Layer 3: Sprint upsell packs | **Slice 4** — Pack entitlement scaffold |

The existing position data in `公考选岗计划` covers `选岗智能匹配` — it needs to be productized into the backend schema and exposed through FastAPI, not left as a client-side SQLite file.

## Architecture (Foundation + Bridge)

```
 ┌──────────────────────────────────────────────────────────────────┐
 │                       Slices 3–4 (This Plan)                     │
 │  Announcement engine · Subscription gating · Paywall middleware │
 └──────────────────────────────────────────────────────────────────┘
 ┌──────────────────────────────────────────────────────────────────┐
 │                    Slice 2 (This Plan)                            │
 │  Onboarding wizard · AI orchestrator · Daily task stream         │
 │  Multi-modal photo→explanation                                  │
 └──────────────────────────────────────────────────────────────────┘
 ┌──────────────────────────────────────────────────────────────────┐
 │                    Slice 1 (This Plan)                            │
 │  Position search/matching API · Score-aware risk engine          │
 │  Import 公考选岗计划 data → product schema                        │
 └──────────────────────────────────────────────────────────────────┘
 ┌──────────────────────────────────────────────────────────────────┐
 │              Slice 0 (Foundation Plan — done first)              │
 │  Product schema · Question bank · Practice loop · AI boundary   │
 │  Admin review · Mobile practice UI                              │
 └──────────────────────────────────────────────────────────────────┘
```

Product owner boundaries expand:

| Owner | Current (Foundation) | New (This Plan) |
|---|---|---|
| `backend` | Question APIs, practice, import, AI orchestration | **+Position engine, +Onboarding flow, +Task stream, +Announcement engine, +Entitlements** |
| `app` | Practice screen, question detail, wrong-book | **+Position browser, +Onboarding wizard, +Daily task stream, +Profile/settings** |
| `admin` | Review queue, merge review | **+Position data review, +Announcement editor, +Entitlement test console** |
| `database` | Questions, practice, review | **+Positions, +Score history, +Announcements, +User entitlements** |
| `ops` | Docker, CI, env | **+Multi-model routing config, +Billing sandbox** |

New owner:

| Owner | Responsibility |
|---|---|
| `billing` | Payment provider integration, webhook handling, receipt (a separate plan; this plan scaffolds the entitlement check, not the payment flow) |

## Asset Inventory: What Exists in 公考选岗计划

The `gwy.db` SQLite at `/Users/zxg/Documents/公考选岗计划/data/gwy.db` already contains production-quality data. It must be imported into the product PostgreSQL schema, not consumed directly.

| Asset | Count | Granularity | Notes |
|---|---|---|---|
| positions | 60,471 (2024: 18,947 + 2025: 20,810 + 2026: 20,714) | Row per position per year | Schema: id, year, position_code, name, department_full, system_code, region, city, headcount, education_min, major_required, political_req, service_min_yrs, paper_type, remark |
| position_score_history | 15,387 (2025: 8,087 + 2026: 7,300) | Position-level entry scores | entry_score_min/max, admission data optional, signup_count available for all |
| system_score_history | 308 rows (2015–2019) | System × region × year | Fallback when position-level data is missing, covers 6 big systems |
| position_tags | 0 (empty) | Derived tag per position | Needs ETL implementation — this plan populates it |
| systems | 7 rows | Enum table | tax, customs, maritime, railwayPolice, statistics, marketReg, others |

**Score coverage quality:**

| Metric | Value |
|---|---|
| 2026 positions with same-year position-level score | 20,422 / 20,714 (98.6%) |
| 2025 positions with same-year position-level score | 20,317 / 20,810 (97.6%) |
| Positions with unique code-in-year AND same-year score | 4,920 (2026), 5,826 (2025) — unambiguous direct match |
| Ambiguous code-in-year matches (multiple positions share same code) | 4,641 (2026) — need department_full disambiguation |
| signup_count coverage | All 15,387 score records have it |

**TS adapter pattern** (from `target-position-engine/src/adapters/`):

```
TargetPositionEngineAdapter (interface)
├── MockTargetPositionEngineAdapter   (4 scenarios, 4 mock positions)
├── SqliteTargetPositionEngineAdapter (reads gwy.db via injected SqlExec)
└── ApiTargetPositionEngineAdapter    (placeholder for future HTTP calls)
```

This adapter pattern does NOT need to be ported to Python/FastAPI. The existing TS code is designed for client-side RN usage. For the new product, the backend (FastAPI) owns the position data, and both the mobile app and admin UI call the FastAPI position API. The SqliteAdapter was a stopgap for the old RN project; the new product stores positions in PostgreSQL.

**Migration path:**

```
Old project: RN App → SqliteAdapter → gwy.db (SQLite)
New product: Flutter App → FastAPI /positions/* → PostgreSQL (product schema)
                                    ↕ (import)
                              gwy.db (import source only, never read at runtime)
```

## Compatibility Boundary

- The position data in `公考选岗计划` is an import source, not a runtime dependency.
- After import, `gwy.db` and the `target-position-engine/` TS package become historical artifacts.
- No migration of old users, sessions, or payments — same as Slice 0.
- The TS adapter interface (types.ts) is a source of truth for the domain model; the FastAPI position service must implement the same semantics.

## Plan Structure

Slice 1–4 build on Slice 0. They can be developed in parallel once Slice 0 database schema exists, but they must be released in order:

```
Slice 0: Foundation (题库底座)          → must ship first (question bank is a dependency)
Slice 1: 选岗引擎 (position engine)     → can start in parallel with Slice 0
Slice 2: 冷启动诊断 + 任务流 + 拍题     → depends on Slice 0 (questions) + Slice 1 (positions)
Slice 3: 公告/节点提醒                  → independent of Slices 1–2, but needed for free-tier value
Slice 4: 变现层 (entitlements)          → depends on Slices 1–3 having value to gate
```

---

## Slice 1: Position Engine (选岗引擎产品化)

### Files

- Create `backend/app/models/position.py` — SQLAlchemy models
- Create `backend/app/models/score_history.py` — system + position score history
- Create `backend/app/models/announcement.py` — preview for Slice 3
- Create `backend/migrations/versions/0002_position_schema.py` — position tables
- Create `backend/app/importers/gwy_position.py` — import from gwy.db
- Create `backend/app/domains/positions/router.py`
- Create `backend/app/domains/positions/service.py`
- Create `backend/app/domains/positions/matcher.py` — user→position scoring logic
- Create `backend/tests/test_position_import.py`
- Create `backend/tests/test_position_api.py`
- Create `backend/tests/test_position_matcher.py`
- Create `apps/mobile/lib/features/positions/position_browser_screen.dart`
- Create `apps/mobile/lib/features/positions/position_detail_screen.dart`
- Create `apps/mobile/lib/features/positions/risk_dashboard_card.dart`
- Create `apps/mobile/test/position_flow_test.dart`
- Create `admin/src/app/positions/page.tsx`
- Create `admin/src/features/positions/position-table.tsx`
- Create `admin/tests/position-review.test.ts`
- Modify `docs/product/data-contract.md`
- Modify `docs/aegis/INDEX.md`

### Database Schema Additions

```sql
-- Inherit existing systems table from gwy.db (map to product schema)
CREATE TABLE product_positions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       TEXT NOT NULL,           -- 'gk-2026-00001' from source, for audit
    year            INTEGER NOT NULL,
    position_code   TEXT NOT NULL,           -- 11-digit national code
    name            TEXT NOT NULL,           -- '一级行政执法员'
    department_full TEXT NOT NULL,           -- '国家税务总局XX市税务局'
    system_code     TEXT NOT NULL,
    region          TEXT NOT NULL,
    city            TEXT,
    paper_type      TEXT NOT NULL,
    headcount       INTEGER NOT NULL DEFAULT 1,
    education_min   TEXT,
    major_required  TEXT,
    political_req   TEXT,
    hukou_req       TEXT,
    service_min_yrs INTEGER,
    remark          TEXT,
    -- Computed/derived
    is_popular      BOOLEAN,                 -- headcount >= 3
    risk_level      TEXT,                    -- computed from score history + headcount
    -- Audit
    raw_json        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (year, position_code, department_full)
);

CREATE TABLE position_score_histories (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    position_id     UUID REFERENCES product_positions(id),
    position_code   TEXT NOT NULL,           -- denormalized for lookup
    year            INTEGER NOT NULL,
    entry_score_min REAL,
    entry_score_max REAL,
    admission_min   REAL,
    admission_max   REAL,
    signup_count    INTEGER,
    ratio           REAL,                   --报录比
    data_source     TEXT,
    UNIQUE (position_code, year)
);

CREATE TABLE system_score_histories (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    system_code     TEXT NOT NULL,
    region          TEXT NOT NULL,
    year            INTEGER NOT NULL,
    entry_score_min REAL,
    entry_score_max REAL,
    admission_min   REAL,
    admission_max   REAL,
    confidence      TEXT NOT NULL DEFAULT 'aggregated',
    data_source     TEXT,
    UNIQUE (system_code, region, year)
);

CREATE TABLE position_tags (
    position_id UUID NOT NULL REFERENCES product_positions(id),
    tag         TEXT NOT NULL,
    PRIMARY KEY (position_id, tag)
);
```

### API Surface

```
GET  /api/v1/positions
  ?year=2026
  &system_code=tax
  &region=广东
  &q=search_text (full-text across name, department, major)
  &page=1&per_page=20

GET  /api/v1/positions/{id}
  Returns: position detail + score history + tags

GET  /api/v1/positions/{id}/score-history
  Returns: position_score_histories + system_score_histories as fallback

POST /api/v1/positions/match
  Body: { region, education, major, systems[], targetYear }
  Returns: ranked list with { position, riskLevel, scoreGap, confidence }

GET  /api/v1/positions/systems
  Returns: system enum list with paper_type
```

### Key Implementation Details

**Position Matcher (matcher.py):**

The core user→position scoring logic. Input: user profile (region, education, major, preferred systems, target year). Output: ranked position list with:

- **Match score**: how well the user's profile matches (education ≥ required, major overlap, region match)
- **Risk level**: based on score gap between user's current estimated score and the position's historical entry/admission score
- **Confidence**: high if position-level history exists for 2+ years; medium if system-level fallback; low if no history

Risk level calculation (ported from the TS adapter's logic):

```python
gap = admission_score - user_current_score
if gap < 5:    return "safe"
if gap < 12:   return "main"
if gap < 22:   return "challenge"
return "high_risk"
```

**Import Pipeline (importer/gwy_position.py):**

Reads `gwy.db` via SQLite CLI or aiosqlite, batch inserts into product PostgreSQL. Key steps:

1. Validate source db integrity (check table existence, row counts match expected)
2. Import systems first (7 rows, upsert by code)
3. Import positions in batches of 500 (60,471 rows)
4. Import position_score_history in batches (15,387 rows)
5. Import system_score_history (308 rows)
6. Compute position_tags — derived from: headcount, score percentiles, year-over-year trend
7. Verify: SELECT COUNT(*) parity against source counts
8. Log import_summary to meta table

### Verification

```bash
# Import count parity
python3 scripts/verify_position_import.py --source /path/to/gwy.db

# API tests
cd backend && python3 -m pytest tests/test_position_api.py

# Matcher logic tests
cd backend && python3 -m pytest tests/test_position_matcher.py

# Flutter position UI
cd apps/mobile && flutter test test/position_flow_test.dart
```

Expected counts:

| Table | Expected |
|---|---|
| product_positions | 60,471 |
| position_score_histories | 15,387 |
| system_score_histories | 308 |
| position_tags | TBD (depends on ETL rules) |

---

## Slice 2: Cold-Start Diagnosis + AI Task Orchestration

This is the highest-differentiation slice. It turns the product from "a question bank" into "an AI-driven备考工作台."

### Files

- Create `backend/app/domains/onboarding/router.py`
- Create `backend/app/domains/onboarding/service.py`
- Create `backend/app/domains/onboarding/schemas.py` — pydantic models for diagnostic input/output
- Create `backend/app/domains/taskstream/router.py`
- Create `backend/app/domains/taskstream/service.py`
- Create `backend/app/domains/taskstream/planner.py` — AI plan generation logic
- Create `backend/app/ai/planner_tools.py` — additional AI tools for plan generation
- Create `backend/app/ai/diagnostic_tools.py` — AI tools for diagnostic phase
- Create `backend/app/ai/prompts/diagnostic.py` — system prompt for cold-start diagnosis
- Create `backend/app/ai/prompts/planner.py` — system prompt for daily/weekly planning
- Create `backend/tests/test_onboarding.py`
- Create `backend/tests/test_taskstream.py`
- Create `backend/tests/test_ai_planner.py`
- Create `apps/mobile/lib/features/onboarding/` — wizard screens
- Create `apps/mobile/lib/features/taskstream/` — daily task stream screen
- Create `apps/mobile/lib/features/taskstream/task_card.dart`
- Create `apps/mobile/lib/features/camera/photo_capture_screen.dart`
- Create `apps/mobile/test/onboarding_flow_test.dart`
- Create `apps/mobile/test/taskstream_test.dart`
- Modify `backend/app/ai/orchestrator.py` — add planning and diagnostic tools

### Database Schema Additions

```sql
CREATE TABLE user_diagnostics (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    -- Target info
    target_year     INTEGER NOT NULL,
    target_region   TEXT NOT NULL,
    target_system   TEXT,
    target_position_id UUID REFERENCES product_positions(id),
    -- Time budget
    daily_minutes   INTEGER NOT NULL,       -- how many min/day user can commit
    study_days      TEXT[],                 -- array of weekdays: ['mon','tue','wed','thu','fri']
    exam_date       DATE,
    -- Diagnostic quiz results
    quiz_module_scores JSONB,               -- { "verbal": 0.72, "math": 0.55, ... }
    quiz_total_score REAL,
    -- Computed plan
    risk_level      TEXT,
    current_estimate REAL,                  -- estimated current score based on quiz
    target_entry    REAL,                   -- position entry score target
    target_admission REAL,                  -- position admission score target
    score_gap       REAL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE study_plans (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    diagnostic_id   UUID REFERENCES user_diagnostics(id),
    week_start      DATE NOT NULL,
    week_end        DATE NOT NULL,
    plan_json       JSONB NOT NULL,         -- full plan document from AI
    -- plan_json structure:
    -- { "focus": "言语+资料分析",
    --   "daily_plans": [
    --     { "date": "2026-07-08", "tasks": [
    --       { "type": "practice", "module": "verbal", "count": 15, "difficulty": "mixed" },
    --       { "type": "review", "wrong_book_module": "math", "count": 5 },
    --       { "type": "explanation", "question_id": "..." },
    --       { "type": "essay", "prompt": "概括题：..." }
    --     ], "total_minutes": 40 }
    --   ]
    -- }
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE daily_task_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    plan_id         UUID REFERENCES study_plans(id),
    task_date       DATE NOT NULL,
    task_type       TEXT NOT NULL,          -- 'practice' | 'review' | 'essay' | 'mock' | 'explanation'
    module_key      TEXT,
    question_ids    UUID[],
    planned_count   INTEGER NOT NULL,
    completed_count INTEGER NOT NULL DEFAULT 0,
    total_minutes   INTEGER,               -- actual time spent
    status          TEXT NOT NULL DEFAULT 'pending', -- 'pending' | 'in_progress' | 'completed' | 'skipped'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### Flows

**Flow 1: Cold-Start Onboarding (First Launch)**

```
User opens app → sees "设定备考目标" wizard:
  Step 1: Which exam? (国考/省考/事业编) + target year
  Step 2: Your region (省/市) + preferred systems (税务/海关/...)
  Step 3: Import education info (学历/专业) — auto-fills position filter
  Step 4: Time budget — "周一到周五每晚40分钟，周末2小时"
  Step 5: 12-min diagnostic quiz — 2 questions per module, timed
  Step 6: Review results — AI generates "可报岗位清单 + 本周计划 + 今日任务"

API calls:
  POST /api/v1/onboarding/start        → creates user_diagnostics record
  POST /api/v1/onboarding/quiz         → submits quiz answers, computes scores
  POST /api/v1/onboarding/generate     → AI generates plan + position recommendations
```

**Flow 2: Daily Task Stream**

```
User opens app → sees task stream screen:
  ┌──────────────────────────────────────┐
  │  ☀️ 早上好，今天任务时间约40分钟      │
  │                                      │
  │  [📝 今日练习] 言语理解 15题          │
  │    目标正确率 70% · 预计25分钟        │
  │                                      │
  │  [📖 错题复盘] 数量关系 5题           │
  │    上次正确率 45% · 预计10分钟        │
  │                                      │
  │  [✍️ 申论小题] 概括题 1题            │
  │    预计5分钟                         │
  │                                      │
  │  ──────────────────────────────────  │
  │  全部完成 → 明日预告                 │
  └──────────────────────────────────────┘

Each task card is tappable → opens the practice/question/essay screen.
Tasks generate dynamically from study_plans.plan_json.

API calls:
  GET  /api/v1/taskstream/today          → returns today's tasks from active study_plan
  POST /api/v1/taskstream/complete       → marks a task complete, updates mastery
  POST /api/v1/taskstream/regenerate     → AI re-plans if user falls behind
```

**Flow 3: Photo → Explanation (拍题讲解)**

```
User taps camera icon in app:
  1. Capture/upload photo of a question
  2. POST /api/v1/questions/photo-explain with image
  3. Backend: vision model (Qwen-Omni / Qwen3-VL) extracts question text
  4. Match extracted text against qbank (embedding similarity)
  5. If match found → return existing explanation
  6. If no match → AI generates explanation for the extracted question text
  7. Return: { matched: bool, question_text, explanation, similar_questions[] }

API:
  POST /api/v1/questions/photo-explain  (multipart: image)
  → { matched, question_text, explanation, similar_questions }
```

### AI Orchestration Boundary

This slice expands the AI boundary defined in Slice 0. New AI tools:

| Tool | Description | Access |
|---|---|---|
| `get_user_profile` | Read user's diagnostic + current mastery | allowed |
| `get_position_detail` | Read position + score history | allowed |
| `get_module_weights` | Read recommended weights for target position | allowed |
| `generate_weekly_plan` | Produce plan JSON for user | allowed — writes study_plans |
| `generate_daily_tasks` | Today's tasks from plan | allowed — writes daily_task_logs |
| `get_wrong_book_summary` | User's current weak modules | allowed |
| `update_diagnostic` | Update user's estimated score after more data | limited |

**Non-allowed** (enforced by orchestrator):
- AI cannot modify product_positions, position_score_histories, question_explanations
- AI cannot grant entitlements or change pricing
- AI cannot override AI boundary audit log rules

### Verification

```bash
# Onboarding flow
cd backend && python3 -m pytest tests/test_onboarding.py

# Task stream generation + completion
cd backend && python3 -m pytest tests/test_taskstream.py

# AI planner produces valid JSON schema
cd backend && python3 -m pytest tests/test_ai_planner.py -k "test_plan_schema_valid"

# Photo explanation (requires mock vision)
cd backend && python3 -m pytest tests/test_ai_planner.py -k "test_photo_explain"

# Flutter UI
cd apps/mobile && flutter test test/onboarding_flow_test.dart
cd apps/mobile && flutter test test/taskstream_test.dart
```

---

## Slice 3: Announcement & Deadline Reminder Engine

This is the free-tier acquisition anchor — the "公考雷达" capability that brings high-intent users into the product before they even start studying.

### Files

- Create `backend/app/models/announcement.py` (if not created in Slice 1)
- Create `backend/app/domains/announcements/router.py`
- Create `backend/app/domains/announcements/service.py`
- Create `backend/app/domains/announcements/scraper.py` — scheduled announcement fetching
- Create `backend/migrations/versions/0003_announcement_schema.py`
- Create `backend/tests/test_announcement_api.py`
- Create `backend/tests/test_announcement_scraper.py`
- Create `apps/mobile/lib/features/announcements/announcement_list_screen.dart`
- Create `apps/mobile/lib/features/announcements/deadline_badge.dart`
- Create `apps/mobile/test/announcement_flow_test.dart`
- Create `admin/src/app/announcements/page.tsx`
- Modify `docs/product/data-contract.md`

### Database Schema

```sql
CREATE TABLE announcements (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title           TEXT NOT NULL,
    exam_type       TEXT NOT NULL,          -- 'national' | 'provincial' | 'career' (事业编)
    region          TEXT,                    -- null for national, 省 for provincial
    year            INTEGER NOT NULL,
    -- Key dates
    announcement_date DATE,                  -- 公告发布日期
    registration_start DATE,                -- 报名开始
    registration_end DATE,                  -- 报名截止
    exam_date       DATE,                   -- 笔试日期
    result_date     DATE,                   -- 成绩公布
    interview_date  DATE,                   -- 面试期间
    -- Content
    source_url      TEXT,
    summary         TEXT,                    -- AI-generated or human-written key points
    positions_count INTEGER,                -- 招录岗位数
    headcount_total INTEGER,                -- 总招录人数
    -- Status
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Track which deadlines a user has been notified about
CREATE TABLE user_announcement_reminders (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    announcement_id UUID NOT NULL REFERENCES announcements(id),
    reminder_type   TEXT NOT NULL,          -- 'registration' | 'exam' | 'result' | 'interview'
    reminded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, announcement_id, reminder_type)
);
```

### API Surface

```
GET  /api/v1/announcements
  ?exam_type=national
  &region=广东
  &is_active=true
  &page=1

GET  /api/v1/announcements/{id}

GET  /api/v1/announcements/upcoming
  → returns next 5 deadlines across all active announcements (sorted by nearest)
  → each entry: { announcement_id, title, event: '报名截止', date: '2026-10-24', days_remaining: -3 }

GET  /api/v1/announcements/{id}/positions
  → link to positions in this announcement
```

### Data Source Strategy

Phase 1 (this slice): **Manual + Community-sourced**
- Seed with current cycle 国考/省考公告 (人工收集，每年 30–50 条核心公告)
- Admin interface for公告管理—运营团队手动录入
- Focus on quality over quantity: 国考 + 31省省考 ≈ 32 authoritative sources/year

Phase 2 (post-MVP, NOT in this slice): **Automated scraping**
- Monitor 国家公务员局 + 各省人社厅 for new公告
- AI-summarize announcement content
- This requires headless browser infrastructure and is explicitly deferred

### Notification Integration

The reminder engine needs a notification delivery mechanism. For MVP, use:
- **In-app badge/alert** (when user opens app, check upcoming deadlines)
- **Push notification** (via Firebase Cloud Messaging or APNs)

The foundation plan doesn't include push infrastructure. This slice adds it:

```
backend/app/services/push.py — push notification service (FCM/APNs abstraction)
ops/ — FCM credentials config
```

### Verification

```bash
# Announcement CRUD
cd backend && python3 -m pytest tests/test_announcement_api.py

# Upcoming deadline computation
cd backend && python3 -m pytest tests/test_announcement_api.py -k "test_upcoming_deadlines"

# Flutter announcement UI
cd apps/mobile && flutter test test/announcement_flow_test.dart
```

---

## Slice 4: Commercialization Layer (变现层)

### Files

- Create `backend/app/models/entitlement.py` — user subscription + feature flags
- Create `backend/migrations/versions/0004_entitlement_schema.py`
- Create `backend/app/domains/entitlements/router.py`
- Create `backend/app/domains/entitlements/service.py` — entitlement check + consumption counting
- Create `backend/app/domains/entitlements/middleware.py` — FastAPI middleware for feature gating
- Create `backend/app/domains/entitlements/tiers.py` — free/pro/pack definitions
- Create `backend/tests/test_entitlements.py`
- Create `backend/tests/test_feature_gating.py` — integration tests
- Create `apps/mobile/lib/features/paywall/paywall_screen.dart`
- Create `apps/mobile/lib/features/paywall/subscription_card.dart`
- Create `apps/mobile/lib/features/paywall/feature_comparison.dart`
- Create `apps/mobile/test/paywall_flow_test.dart`
- Create `admin/src/app/entitlements/page.tsx` — test console for entitlements
- Create `docs/product/pricing-model.md`

### Database Schema

```sql
CREATE TYPE subscription_tier AS ENUM ('free', 'ai_pro_monthly', 'ai_pro_yearly');

CREATE TYPE entitlement_pack AS ENUM (
    'interview_sprint',     -- 面试冲刺包
    'current_affairs',      -- 时政包
    'position_exclusive',   -- 岗位专属包
    'essay_tutoring'        -- 申论精批包
);

CREATE TABLE user_entitlements (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL UNIQUE REFERENCES users(id),
    tier            subscription_tier NOT NULL DEFAULT 'free',
    -- Subscription period (null for free tier)
    subscription_start  TIMESTAMPTZ,
    subscription_end    TIMESTAMPTZ,
    -- Consumption counters (reset daily for free tier)
    daily_ai_queries    INTEGER NOT NULL DEFAULT 0,
    daily_ai_quota      INTEGER NOT NULL DEFAULT 5,    -- free tier: 5/day
    daily_diagnostics   INTEGER NOT NULL DEFAULT 0,
    daily_diagnostic_quota INTEGER NOT NULL DEFAULT 1, -- free tier: 1/day
    -- Feature flags (computed from tier + packs)
    features            JSONB NOT NULL DEFAULT '{}',
    -- features structure:
    -- { "ai_explanation": true,
    --   "ai_unlimited": false,
    --   "essay_grading": false,
    --   "voice_coach": false,
    --   "position_matching": true,
    --   "announcement_reminders": true,
    --   "photo_explain": true,
    --   "task_planning": true,
    --   "interview_simulation": false }
    -- Audit
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE user_packs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    pack            entitlement_pack NOT NULL,
    purchased_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ,
    is_active       BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE entitlement_audit_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    action          TEXT NOT NULL,           -- 'feature_check' | 'quota_consumed' | 'tier_changed' | 'pack_purchased'
    feature         TEXT,                    -- which feature was checked/consumed
    allowed         BOOLEAN,
    metadata        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### Tier Definitions

| Tier | Price (MVP) | Key Features | Daily AI Quota |
|---|---|---|---|
| Free | ¥0 | Qbank browse, practice, wrong-book, position search, announcements + deadline reminders, 5 AI explanations/day, 1 diagnostic/start | 5 |
| AI Pro (monthly) | ¥29–39 | Unlimited AI explanations, task planning, position matching + risk, cold-start diagnosis, photo→explanation,申论粗批, cross-device sync | Unlimited |
| AI Pro (yearly) | ¥199–299 | Same as monthly, ~45% discount | Unlimited |
| Interview Sprint | ¥99–199 | 面试模拟 20 sessions + AI feedback | — |
| Current Affairs | ¥29–59 | Monthly热点包, updated weekly | — |
| Essay Tutoring | ¥49–99 | 10 篇精批 + 专家复核 | — |

### Feature Gating Middleware

The `entitlements/middleware.py` should:

1. Read user_id from auth context (foundation plan needs auth first — see gap below)
2. Look up `user_entitlements` for the user
3. Before each AI or premium endpoint:
   - Check feature flag (is this feature allowed for the tier?)
   - Check daily quota (has the user exceeded today's limit?)
   - If denied: return 402 Payment Required with `{ "code": "QUOTA_EXCEEDED" | "TIER_UPGRADE_REQUIRED", "tier": "ai_pro", "upgrade_url": "..." }`
   - If allowed: increment consumption counter, proceed
4. Log all checks to `entitlement_audit_logs`

Premium endpoints requiring gating:

| Endpoint | Free Tier | Pro Tier |
|---|---|---|
| POST /api/v1/onboarding/generate | 1-time (startup diagnostic) | Unlimited |
| GET /api/v1/ai/explain/{question_id} | 5/day | Unlimited |
| POST /api/v1/questions/photo-explain | 5/day | Unlimited |
| GET /api/v1/taskstream/today | ✅ | ✅ |
| POST /api/v1/taskstream/regenerate | ❌ | ✅ |
| GET /api/v1/positions/match | ✅ (basic) | ✅ (full risk) |
| POST /api/v1/ai/essay-grade | ❌ | ✅ |
| POST /api/v1/ai/interview-simulate | ❌ | ✅ (or pack) |

### Payment Integration (Scaffold Only)

This slice does NOT implement full payment provider integration. It scaffolds the entitlement system so that:

1. Free tier works for everyone immediately
2. AI Pro is feature-gated with a "升级到 Pro" screen
3. When payment is implemented (separate plan), the billing service just needs to:
   - Call `PATCH /api/v1/entitlements/tier` with the new tier
   - Set subscription_start/end
   - No changes to feature gating logic

The paywall screens in the Flutter app show pricing and trigger a `PURCHASE_INTENT` event that the future billing service handles.

### Verification

```bash
# Feature gating
cd backend && python3 -m pytest tests/test_feature_gating.py

# Entitlement CRUD + quota management
cd backend && python3 -m pytest tests/test_entitlements.py

# Paywall UI
cd apps/mobile && flutter test test/paywall_flow_test.dart
```

---

## Gap: Authentication

The foundation plan listed `backend` owner's responsibilities as including auth, but its 9 tasks create no auth artifact. This plan's Slices 2–4 all depend on `user_id` being present in requests.

### Recommended Auth Slice

Insert before or concurrent with Slice 1:

- Create `backend/app/models/user.py` — users table (email, password hash, profile)
- Create `backend/app/domains/auth/router.py` — register, login (email+password or SMS)
- Create `backend/app/domains/auth/deps.py` — FastAPI `Depends(get_current_user)` dependency
- Create `backend/app/domains/auth/jwt.py` — JWT token generation + verification
- Create `backend/migrations/versions/0001b_auth_schema.py` (between 0001 and 0002)

**Minimal for MVP:** Email + password authentication, no OAuth, no social login. JWT tokens, 7-day expiry. Password hashing with bcrypt.

**User table:**

```sql
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    nickname        TEXT,
    avatar_url      TEXT,
    -- Education profile (used by position matcher)
    education       TEXT,
    major           TEXT,
    graduation_year INTEGER,
    -- Audit
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Auth is a prerequisite for Slices 2–4. Consider adding it as an explicit task block at the start of Slice 1.

---

## Updated Architecture Diagram (All Slices)

```
                             ┌───────────────┐
                             │   Flutter App │
                             │  (iOS/Android)│
                             └───────┬───────┘
                                     │ HTTPS
                             ┌───────▼───────┐
                             │  API Gateway  │
                             │   (FastAPI)   │
                             └───────┬───────┘
                                     │
          ┌──────────┬───────────────┼──────────────┬──────────────┐
          ▼          ▼               ▼              ▼              ▼
   ┌──────────┐ ┌──────────┐ ┌────────────┐ ┌──────────┐ ┌──────────────┐
   │  Auth    │ │ Questions│ │  Positions │ │Announce. │ │ Entitlements │
   │  JWT     │ │ Practice │ │  Matching  │ │Reminders │ │ Free/Pro/Pack│
   └──────────┘ └────┬─────┘ └─────┬──────┘ └────┬─────┘ └──────┬───────┘
                      │              │             │              │
                      ▼              ▼             ▼              ▼
              ┌──────────────────────────────────────────────────────┐
              │              PostgreSQL + pgvector                   │
              │  Questions · Positions · Score History · Users      │
              │  Study Plans · Daily Tasks · Entitlements           │
              │  Announcements · AI Audit Logs                      │
              └──────────────────────────────────────────────────────┘
                                     │
                             ┌───────▼───────┐
                             │  AI Router    │
                             │  (model layer)│
                             └───────┬───────┘
                                     │
                 ┌───────────────────┼───────────────────┐
                 ▼                   ▼                   ▼
          ┌──────────┐        ┌──────────┐        ┌──────────┐
          │  Qwen3   │        │ Qwen-Omni│        │ CosyVoice│
          │  (text)  │        │ (vision) │        │  (TTS)   │
          └──────────┘        └──────────┘        └──────────┘
```

## Dependency Graph

```
Slice 0: Foundation (题库) ───────────────────────────────┐
                                    │                      │
              ┌─────────────────────┤                      │
              ▼                     ▼                      │
Slice 1: 选岗引擎           Auth (gap fill)               │
              │                     │                      │
              └─────────┬───────────┘                      │
                        ▼                                 │
                 Slice 2: 诊断+任务流+拍题 ◄────────────────┘
                        │
                        ▼
                 Slice 3: 公告提醒 (parallel with slice 2)
                        │
                        ▼
                 Slice 4: 变现层 (depends on slices 1-3
                           having gatable value)
```

## Risks

| Risk | Mitigation |
|---|---|
| Auth is missing from both foundation plan and this plan | Added explicit auth gap section; must be resolved before Slice 2 |
| 拍题讲解 depends on vision model (Qwen-Omni) that may have latency/cost issues | Mock vision fallback: if model unavailable, offer text-input explanation as fallback; measure latency in beta |
| Study plan generation by AI can produce low-quality plans | Implement `plan_json` schema validation + admin review dashboard for plans flagged as low-confidence |
| Position matcher's `user_current_score` is unreliable without a real mock exam | Use diagnostic quiz score as initial estimate; degrade gracefully with "not enough data" confidence level |
| 60K positions with 6,737 duplicate-code-in-year positions need disambiguation | Import logic uses `(year, position_code, department_full)` as unique key; admin review queue for ambiguous matches |
| Payment provider integration has compliance overhead (PBoC, ICP filing for in-app purchase) | Explicitly deferred to separate plan; this plan scaffolds entitlement check only |
| Free tier daily quota (5 AI queries) may be too restrictive or too generous | Configurable via env var; A/B test 3/5/10 during beta |
| Push notification infrastructure for anniversary reminders | MVP uses in-app badge only; push notifications added in post-MVP |
| Timing: 7月 start → 9月 beta → 11月国考冲刺 | Slices 1–2 must parallelize; auth and position import can start on day 1 |

## Retirement

- Retire `gwy.db` as a runtime dependency after import to product schema is verified.
- Retain `gwy.db` as an import source artifact in `scripts/import_sources/`.
- Retain `target-position-engine/` TS package as a reference implementation of the adapter interface; it is NOT linked into the new product.
- After this plan, the old RN project's `SqliteTargetPositionEngineAdapter` has no runtime use; the new product's Flutter app calls `GET /api/v1/positions/*`.

## Success Criteria

After Slices 0–4, the product should demonstrate the full report-defined MVP:

1. **选岗匹配**: User sets region + education → sees ranked positions with score gap and risk level
2. **冷启动诊断**: First launch wizard → 12-min quiz → personalized plan
3. **每日任务流**: App opens to task stream, not a feature grid — each task is a one-tap start
4. **拍题讲解**: Photo → AI extracts and explains → logs to mastery
5. **错题复盘**: Practice answers → wrong-book → auto-recommended in daily tasks
6. **公告提醒**: Active exam deadlines visible on home screen, registration/exam dates tracked
7. **变现路径**: Free tier provides real value (position matching, 5 AI/day); AI Pro gates unlimited use; sprint packs visible

And in business terms:

- Free tier: reduces organic CAC by providing pre-study acquisition value (position matching + announcements)
- AI Pro: the primary monetization driver (高频工具订阅)
- Sprint packs: upsell during冲刺期 (high willingness to pay)
- Position data and AI planning are the two moats that competitors cannot easily replicate without either the data or the orchestration layer
