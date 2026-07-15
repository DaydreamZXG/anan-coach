# 官场生存刷题游戏实施计划

日期：2026-07-07  
父规格：`docs/aegis/specs/2026-07-07-bureaucratic-survival-architecture-plan.md`  
状态：可执行计划，等待选择执行方式  

## Goal

把现有“考公搭子”从 today/feed 任务流产品，改造成“开衙 -> 动态政务卷宗 -> 卡片批阅 -> 官阶/辖区结算”的官场生存刷题游戏。

本计划不从空仓重做。保留题库、申论、知识点、岗位、AI 审计、认证、iOS 工程和后端部署；重做产品体验层、首页 owner、执行链和进阶系统。

## Architecture

新 canonical owner：

- 后端首页编排：`backend/app/domains/docket/`
- 行测卡片执行：`backend/app/domains/review_session/`
- 官阶/辖区/御史台：`backend/app/domains/progression/`
- iOS 首页：`DocketFeature`
- iOS 卡片批阅：`ReviewCardsFeature`
- iOS 状态：拆出 `DocketStore`、`ReviewSessionStore`、`ProgressionStore`

旧路径处理：

- `/api/v1/taskstream/today` 不再直接服务客户端首页。
- `/api/v1/feed/today` 不再作为客户端首页主路径。
- `rank_system` 的表与逻辑先被 `progression` 包装接管，后续确认数据迁移后再退役。
- `AppState` 不继续承载新增业务，只保留临时路由和兼容桥，随后逐步拆空。

## Tech Stack

- iOS：SwiftUI / iOS 17+ / Xcode project 单 target。
- 后端：FastAPI / Python / SQLAlchemy / PostgreSQL。
- 测试：pytest + FastAPI TestClient + 本地临时 PostgreSQL；iOS 用 `xcodebuild` simulator build。
- AI：继续使用现有 OpenAI-compatible provider 层，但 AI 只生成幕僚话术、解释改写和叙事，不决定题目正确性。

## Baseline/Authority Refs

- `docs/aegis/specs/2026-07-07-bureaucratic-survival-architecture-plan.md`
- `docs/HANDOVER.md`
- `docs/aegis/specs/2026-07-04-genui-card-feed-spec.md`
- `backend/app/domains/taskstream/`
- `backend/app/domains/feed/`
- `backend/app/domains/practice/`
- `backend/app/domains/rank_system/`
- `apps/ios/Gongkao/Gongkao/AppModels.swift`
- `apps/ios/Gongkao/Gongkao/FeatureViews.swift`
- `apps/ios/Gongkao/Gongkao/StudyDeskView.swift`
- `apps/ios/Gongkao/Gongkao/TaskExecutionView.swift`

## Compatibility Boundary

- 无已上架客户端，不保留旧首页主路径兼容。
- 不删除真实数据库表、不清理生产学习记录、不执行破坏性迁移。
- 旧 API 可以短期保留给调试和内部验证，但 iOS 主路径必须切到 `/api/v1/dockets/open-court`。
- 题源、答案、判分、学习记录保持真实可追溯。
- 付费道具不能改写真实学习结果，不能代替用户答题。

## Verification

每个后端 slice 至少跑：

```bash
backend/.venv/bin/python -m pytest backend/tests/<slice_test>.py -q
backend/.venv/bin/python -m pytest backend/tests -q
```

每个 iOS slice 至少跑：

```bash
xcodebuild build \
  -project /Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao.xcodeproj \
  -scheme Gongkao \
  -destination 'platform=iOS Simulator,name=iPhone 17' \
  CODE_SIGNING_ALLOWED=NO
```

关键验收：

- 后端全量 pytest 绿色。
- iPhone build 成功。
- 首页不再读取 `/feed/today` 作为主路径。
- 首次开衙可拿到 10 张真实题源政务卡。
- 行测卡片答题入库，错题和题源可追溯。
- 分差不再在首页英雄位展示为 0。

## Plan Basis

Fact:

- 当前后端已有题库、知识点、practice、essay、rank、feed、taskstream、auth、memory 等领域。
- 当前 iOS 有 TodayView、StudyDeskView、TaskExecutionView、RankDetailView、FeedCardViews 等可复用组件。
- 当前后端测试不全绿，最近审计为 58 passed / 9 failed。
- 当前首页存在 today/feed 双 owner，iOS 存在 iPhone/iPad 双首页逻辑。

Assumption:

- App 尚未上架，因此可以 delete-first 退役旧客户端主路径。
- 现有 product schema 的题库资产可信，优先复用。
- 第一阶段以 iPhone 主循环为主，iPad 保留可用但不做桌面化体验。

Unknown:

- 生产库是否已有真实用户学习记录。计划不删除表或记录，因此该未知不阻塞实施。
- 申论 2000 题的结构化程度。Boss 战后置，不阻塞开衙与行测卡片主循环。

## BaselineUsageDraft

- Required baseline refs: 官场生存架构规格、交接文档、现有 taskstream/feed/practice/rank/iOS 文件。
- Delivered context refs: 当前审计结论、后端测试失败簇、用户拍板的五个产品决策。
- Acknowledged before plan refs: 旧首页双 owner、AppState 巨对象、XP 与分差主线冲突、商业产品不打补丁约束。
- Cited in plan refs: 本文档的 Goal、Architecture、Compatibility Boundary、Tasks。
- Missing refs: 无阻塞缺口。
- Decision: continue。

## Requirement Ready Check

- Requirement source refs: 用户明确要“有意思、吸引人”的官场生存刷题游戏；架构规格已把五个决策写入。
- Goals and scope refs: 开衙首页、动态政务卷宗、行测卡片批阅、官阶惩罚、iPhone 首发。
- User / scenario refs: 用户打开 App 问“今日有何政务”，幕僚派发真题急件，用户滑动批阅并获得官阶/辖区反馈。
- Requirement item refs: `/api/v1/dockets/open-court`、10 张真题政务卡、Tinder-like 批阅、准/驳印章、御史台、公平付费道具。
- Acceptance / verification criteria refs: 后端全绿、iOS build 成功、首页 owner 唯一、题源可追溯、旧主路径退场。
- Open blocker questions: 无。
- Decision: ready。

## Architecture Integrity Lens

- Invariant: 首页、执行链、进阶系统必须各有单一 owner；AI 不决定正确性；学习记录必须真实。
- Canonical owner / contract: `docket` 生成卷宗；`review_session` 处理卡片判决；`progression` 处理官阶/辖区。
- Responsibility overlap: `taskstream/feed` 和 `rank_system` 当前仍承载真实逻辑，必须迁移或包装，不继续扩写。
- Higher-level simplification: 用 `docket` 的封闭卡片词表取代 today/feed 双布局映射；用 `progression` 取代 scattered `RankService` 调用。
- Retirement / falsifier: 如果 iOS 主路径仍可从 feed/today 自动回旧布局，则退役失败。
- Verdict: proceed with owner split and staged retirement。

## Plan Pressure Test

- Owner / contract / retirement: 高压力，必须先建新 owner，再切主路径，最后退旧路径。
- Architecture integrity / higher-level path: 通过 `docket` 与 `progression` 新边界解决，不在旧文件里继续加分支。
- Verification scope: 后端 contract tests + iOS build + negative check。
- Task executability: 分阶段可执行，先测试修复和后端 contract，再上 iOS。
- Pressure result: proceed。

## Plan-Time Complexity Check

Complexity Budget:

- Artifact class: cross-module product architecture migration。
- Target files / artifacts: 后端 `domains` 新 owner、iOS Feature 文件、AppShell/AppModels、测试 schema、Aegis docs。
- Current pressure: high。`AppModels.swift` 883 行，`TaskExecutionView.swift` 948 行，`FeatureViews.swift` 914 行；后端测试红。
- Projected post-change pressure: high unless new owner files are added and old paths retired。
- Budget result: at-risk。
- Planned governance: 不在 AppModels/FeatureViews/TaskExecutionView 继续堆主逻辑；所有新增业务进入新 owner 文件。

Plan-Time Complexity Check:

- Target files: `backend/app/domains/docket/*`、`backend/app/domains/review_session/*`、`backend/app/domains/progression/*`、`apps/ios/Gongkao/Gongkao/Docket*.swift`、`ReviewCards*.swift`、`Progression*.swift`。
- Existing size / shape signals: 当前大文件和双 owner 已经造成维护风险。
- Owner fit: 新 owner 文件更适合，不在旧 today/feed/rank 增长。
- Add-in-place risk: over-budget if continuing in `AppModels.swift` or `FeatureViews.swift`。
- Better file boundary: 新 Feature + Store + Models 文件。
- Recommendation: add owner file, split task, retire old path after green verification。

## Files

Backend create:

- `backend/app/domains/docket/__init__.py`
- `backend/app/domains/docket/schemas.py`
- `backend/app/domains/docket/repository.py`
- `backend/app/domains/docket/service.py`
- `backend/app/domains/docket/router.py`
- `backend/app/domains/review_session/__init__.py`
- `backend/app/domains/review_session/schemas.py`
- `backend/app/domains/review_session/repository.py`
- `backend/app/domains/review_session/service.py`
- `backend/app/domains/review_session/router.py`
- `backend/app/domains/progression/__init__.py`
- `backend/app/domains/progression/schemas.py`
- `backend/app/domains/progression/repository.py`
- `backend/app/domains/progression/service.py`
- `backend/app/domains/progression/router.py`
- `backend/tests/test_docket_open_court.py`
- `backend/tests/test_review_session_verdict.py`
- `backend/tests/test_progression_contract.py`

Backend modify:

- `backend/app/api/router.py`
- `backend/app/domains/mock_exam/service.py`
- `backend/app/domains/practice/router.py`
- `backend/app/domains/essay/router.py`
- `backend/tests/sql/minimal_schema.sql`
- existing tests whose expectations encode retired fields or old target score formulas.

iOS create:

- `apps/ios/Gongkao/Gongkao/DocketModels.swift`
- `apps/ios/Gongkao/Gongkao/DocketStore.swift`
- `apps/ios/Gongkao/Gongkao/DocketView.swift`
- `apps/ios/Gongkao/Gongkao/ReviewCardsModels.swift`
- `apps/ios/Gongkao/Gongkao/ReviewCardsStore.swift`
- `apps/ios/Gongkao/Gongkao/ReviewCardsView.swift`
- `apps/ios/Gongkao/Gongkao/ProgressionStore.swift`
- `apps/ios/Gongkao/Gongkao/ShareReportView.swift`

iOS modify:

- `apps/ios/Gongkao/Gongkao/AppShellView.swift`
- `apps/ios/Gongkao/Gongkao/APIClient.swift`
- `apps/ios/Gongkao/Gongkao/AppModels.swift`
- `apps/ios/Gongkao/Gongkao/Gongkao.xcodeproj/project.pbxproj`

Docs modify:

- `docs/HANDOVER.md`
- `docs/aegis/INDEX.md`

## Tasks

### Task 1: Stabilize Existing Backend Tests Before Adding Docket

Files:

- Modify `backend/tests/sql/minimal_schema.sql`
- Modify `backend/app/domains/mock_exam/service.py`
- Modify `backend/tests/test_mock_exam_calibration.py`
- Modify route-level tests that use fake services without DB overrides

Why:

The current backend suite is red. Adding a new owner while baseline contracts are failing would hide regressions.

Repair Track:

- Root cause: rank/progression side effects were wired into practice/essay/mock paths without test schema and dependency boundaries.
- Canonical owner: until `progression` exists, `rank_system` remains the rank persistence owner.
- Minimal sufficient stable repair: make the existing rank side effects testable and fix the mock exam `session` variable shadowing.
- Compatibility boundary: no API behavior changes except tests accepting new explicit fields such as `moduleName`.
- Verification: full backend pytest green.

Retirement Track:

- Old owner/fallback: scattered rank calls stay only until `progression` task replaces them.
- Deletion trigger: after Task 5 introduces `progression`, practice/essay/mock no longer call `RankService` directly.

Steps:

1. Write test / contract check:
   - Keep `backend/tests/test_companion_external.py::test_companion_ask_is_quota_gated_and_audited` as proof that companion can read rank context.
   - Keep `backend/tests/test_mock_exam_calibration.py::test_weekly_mock_exam_full_loop_feeds_workbench_and_calibration` as proof that mock exam submission does not pass a record as a DB session.
   - Keep `backend/tests/test_practice_taskstream_routes.py::test_practice_answer_contract` as proof route tests do not accidentally hit an unconfigured production DB.

2. Verify RED:
   ```bash
   backend/.venv/bin/python -m pytest \
     backend/tests/test_companion_external.py::test_companion_ask_is_quota_gated_and_audited \
     backend/tests/test_mock_exam_calibration.py::test_weekly_mock_exam_full_loop_feeds_workbench_and_calibration \
     backend/tests/test_practice_taskstream_routes.py::test_practice_answer_contract \
     -q
   ```
   Expected before repair: failures mention missing `learner.official_ranks`, `MockExamSessionRecord has no attribute execute`, or `DATABASE_URL is required`.

3. Minimal code:
   - Add rank tables and nine config rows to `minimal_schema.sql`: `learner.official_ranks`, `learner.rank_events`, `product.official_rank_config`.
   - Rename the local `session` variable in `MockExamService.submit_mock` to `mock_session` and use the DB session parameter only for `RankRepository`.
   - In route tests using fakes, override `get_db_session` with an in-memory or fixture session when route dependencies require it; do not allow accidental production DB lookup.
   - Update strict expected dicts to include `moduleName` where the API now explicitly returns it.

4. Verify GREEN:
   ```bash
   backend/.venv/bin/python -m pytest backend/tests -q
   ```
   Expected after repair: all collected backend tests pass.

5. Commit:
   ```bash
   git status --short
   git add backend/tests/sql/minimal_schema.sql backend/app/domains/mock_exam/service.py backend/tests
   git commit -m "Stabilize backend contracts before docket work"
   ```
   Only stage files changed for this task.

### Task 2: Add Docket Backend Contract

Files:

- Create `backend/tests/test_docket_open_court.py`
- Create `backend/app/domains/docket/schemas.py`
- Create `backend/app/domains/docket/service.py`
- Create `backend/app/domains/docket/router.py`
- Modify `backend/app/api/router.py`

Why:

This establishes `/api/v1/dockets/open-court` as the new homepage owner before iOS work begins.

Impact/Compatibility:

- Adds a new API. Does not remove old APIs in this task.
- Docket cards must carry real `questionId` and `sourceGrounding`.

Steps:

1. Write test:
   - `test_open_court_returns_ten_traceable_daily_affairs`
   - `test_open_court_reuses_active_docket_for_same_day`
   - `test_open_court_includes_rank_and_court_state_snapshots`
   The JSON assertion must include `docketId`, `rankSnapshot`, `courtState`, `cards`, and at least one card with `type = daily_affair`, `track = xingce`, `interaction = swipe_verdict`, `sourceGrounding = product.questions`.

2. Verify RED:
   ```bash
   backend/.venv/bin/python -m pytest backend/tests/test_docket_open_court.py -q
   ```
   Expected before implementation: import or route not found errors.

3. Minimal code:
   - Define Pydantic schemas:
     - `DocketOpenCourtResponse`
     - `DocketCard`
     - `RankSnapshot`
     - `CourtStateSnapshot`
   - Implement `DocketService.open_court(user_id)` using existing `KnowledgeService.select_weighted_xingce_practice` to produce 10 cards.
   - Card titles use deterministic world mapping:
     - `资料分析 -> 州府钱粮急件`
     - `判断推理 -> 州府疑案`
     - `常识判断 -> 民情风俗奏报`
     - `言语理解与表达 -> 诏令释义`
     - `数量关系 -> 工程筹算`
   - Wire `POST /api/v1/dockets/open-court` into `backend/app/api/router.py`.

4. Verify GREEN:
   ```bash
   backend/.venv/bin/python -m pytest backend/tests/test_docket_open_court.py -q
   backend/.venv/bin/python -m pytest backend/tests -q
   ```

5. Commit:
   ```bash
   git status --short
   git add backend/app/domains/docket backend/app/api/router.py backend/tests/test_docket_open_court.py
   git commit -m "Add docket open-court contract"
   ```

### Task 3: Persist Dockets and Docket Cards

Files:

- Create `backend/app/domains/docket/repository.py`
- Modify `backend/app/domains/docket/service.py`
- Modify `backend/tests/sql/minimal_schema.sql`
- Modify `backend/tests/test_docket_open_court.py`

Why:

Open-court must be a real session, not a transient generated list. Same-day calls return the same active卷宗.

Impact/Compatibility:

- Adds `learner.dockets` and `learner.docket_cards` to test schema.
- Does not delete old data.

Steps:

1. Write test:
   - Assert first open-court call inserts one row into `learner.dockets`.
   - Assert it inserts ten rows into `learner.docket_cards`.
   - Assert second same-day open-court call returns same `docketId` and no duplicate cards.

2. Verify RED:
   ```bash
   backend/.venv/bin/python -m pytest backend/tests/test_docket_open_court.py -q
   ```
   Expected before persistence: table missing or duplicate-card assertions fail.

3. Minimal code:
   - Add schema:
     - `learner.dockets(docket_id uuid primary key, user_id uuid not null, docket_date date not null, status text not null, created_at timestamptz, updated_at timestamptz, unique(user_id, docket_date, status))`
     - `learner.docket_cards(card_id uuid primary key, docket_id uuid not null, question_id text, type text not null, track text not null, module_name text, world_label text, title text not null, narrative text not null, interaction text not null, source_grounding text not null, status text not null, sort_order integer not null, payload jsonb not null default '{}'::jsonb)`
   - Implement repository methods:
     - `get_active_docket_for_date`
     - `create_docket`
     - `list_cards`
     - `create_cards`
   - Keep UUIDs server-generated.

4. Verify GREEN:
   ```bash
   backend/.venv/bin/python -m pytest backend/tests/test_docket_open_court.py -q
   backend/.venv/bin/python -m pytest backend/tests -q
   ```

5. Commit:
   ```bash
   git status --short
   git add backend/app/domains/docket backend/tests/sql/minimal_schema.sql backend/tests/test_docket_open_court.py
   git commit -m "Persist daily dockets and cards"
   ```

### Task 4: Add Review Session Verdict API

Files:

- Create `backend/app/domains/review_session/schemas.py`
- Create `backend/app/domains/review_session/repository.py`
- Create `backend/app/domains/review_session/service.py`
- Create `backend/app/domains/review_session/router.py`
- Create `backend/tests/test_review_session_verdict.py`
- Modify `backend/app/api/router.py`
- Modify `backend/tests/sql/minimal_schema.sql`

Why:

The card UI needs a backend execution owner. Practice answer submission is too tied to old task IDs and old screen flow.

Impact/Compatibility:

- Adds a new verdict API.
- Reuses `product.questions` and existing answer correctness.
- Records every answer with docket/card identity.

Steps:

1. Write test:
   - Create a docket with one card.
   - POST selected answer to `/api/v1/review-sessions/{docketId}/cards/{cardId}/verdict`.
   - Assert response contains `correct`, `stampText` as `准` or `驳`, `xpGained` absent, `sourceGrounding`, and `companionLine`.
   - Assert `learner.verdicts` row exists.
   - Assert card status changes to `completed`.

2. Verify RED:
   ```bash
   backend/.venv/bin/python -m pytest backend/tests/test_review_session_verdict.py -q
   ```
   Expected before implementation: route not found.

3. Minimal code:
   - Add `learner.verdicts(verdict_id uuid primary key, user_id uuid not null, docket_id uuid not null, card_id uuid not null, question_id text not null, selected_answer text not null, correct boolean not null, elapsed_seconds integer, created_at timestamptz not null default now())`.
   - Implement deterministic `stampText`: `准` if correct else `驳`.
   - Implement deterministic companion line templates by correctness and module.
   - Do not call `RankService` here; progression event wiring is Task 5.

4. Verify GREEN:
   ```bash
   backend/.venv/bin/python -m pytest backend/tests/test_review_session_verdict.py -q
   backend/.venv/bin/python -m pytest backend/tests -q
   ```

5. Commit:
   ```bash
   git status --short
   git add backend/app/domains/review_session backend/app/api/router.py backend/tests/sql/minimal_schema.sql backend/tests/test_review_session_verdict.py
   git commit -m "Add review card verdict API"
   ```

### Task 5: Introduce Progression Owner and Remove Scattered Rank Calls

Files:

- Create `backend/app/domains/progression/schemas.py`
- Create `backend/app/domains/progression/repository.py`
- Create `backend/app/domains/progression/service.py`
- Create `backend/app/domains/progression/router.py`
- Create `backend/tests/test_progression_contract.py`
- Modify `backend/app/domains/review_session/service.py`
- Modify `backend/app/domains/practice/router.py`
- Modify `backend/app/domains/essay/router.py`
- Modify `backend/app/domains/mock_exam/service.py`

Why:

官阶/民心/御史台是产品主系统，不能散落在 practice、essay、mock 的路由里。

Impact/Compatibility:

- `rank_system` persistence can remain underneath, but new code calls `ProgressionService` only.
- Practice/essay/mock stop directly instantiating `RankService`.

Steps:

1. Write test:
   - `test_review_verdict_awards_governance_progress`
   - `test_wrong_answer_can_reduce_court_stability_without_ui_lie`
   - `test_practice_essay_mock_do_not_instantiate_rank_service_directly` using import/text scan assertions for disallowed direct calls in routers.

2. Verify RED:
   ```bash
   backend/.venv/bin/python -m pytest backend/tests/test_progression_contract.py -q
   ```
   Expected before implementation: missing progression module or direct-call scan fails.

3. Minimal code:
   - Define `ProgressionEventResult` with `meritDelta`, `fameDelta`, `courtRiskDelta`, `rankChanged`, `lockedPrivileges`, `narrative`.
   - Implement `record_verdict`, `record_essay_review`, `record_mock_exam`.
   - Move rank XP/fame updates behind `ProgressionService`.
   - Replace direct `RankService(...)` calls in practice/essay/mock paths.
   - Add `GET /api/v1/progression/profile` and `GET /api/v1/progression/court-state`.

4. Verify GREEN:
   ```bash
   backend/.venv/bin/python -m pytest backend/tests/test_progression_contract.py -q
   backend/.venv/bin/python -m pytest backend/tests -q
   ```

5. Commit:
   ```bash
   git status --short
   git add backend/app/domains/progression backend/app/domains/review_session backend/app/domains/practice/router.py backend/app/domains/essay/router.py backend/app/domains/mock_exam/service.py backend/tests/test_progression_contract.py
   git commit -m "Introduce progression owner for rank and court state"
   ```

### Task 6: Add iOS Docket Models and Store

Files:

- Create `apps/ios/Gongkao/Gongkao/DocketModels.swift`
- Create `apps/ios/Gongkao/Gongkao/DocketStore.swift`
- Modify `apps/ios/Gongkao/Gongkao/APIClient.swift`
- Modify `apps/ios/Gongkao/Gongkao/Gongkao.xcodeproj/project.pbxproj`

Why:

iOS needs a clean client boundary before UI migration. Do not add docket state into `AppModels.swift`.

Impact/Compatibility:

- No visible UI change yet.
- New model must decode backend JSON contract exactly.

Steps:

1. Write test/compile target:
   - Add Swift models with non-optional required fields for `docketId`, `rankSnapshot`, `courtState`, `cards`.
   - Ensure `DocketStore.openCourt()` calls `POST /api/v1/dockets/open-court`.

2. Verify RED:
   ```bash
   xcodebuild build \
     -project /Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao.xcodeproj \
     -scheme Gongkao \
     -destination 'platform=iOS Simulator,name=iPhone 17' \
     CODE_SIGNING_ALLOWED=NO
   ```
   Expected before pbxproj wiring: new types are not compiled or references fail.

3. Minimal code:
   - Add `DocketResponse`, `DocketCard`, `DocketRankSnapshot`, `DocketCourtState`.
   - Add `@MainActor final class DocketStore: ObservableObject`.
   - Store state: `isOpening`, `docket`, `errorMessage`.
   - Do not touch old `TodayView` yet.

4. Verify GREEN:
   ```bash
   xcodebuild build \
     -project /Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao.xcodeproj \
     -scheme Gongkao \
     -destination 'platform=iOS Simulator,name=iPhone 17' \
     CODE_SIGNING_ALLOWED=NO
   ```
   Expected: `BUILD SUCCEEDED`.

5. Commit:
   ```bash
   git status --short
   git add apps/ios/Gongkao/Gongkao/DocketModels.swift apps/ios/Gongkao/Gongkao/DocketStore.swift apps/ios/Gongkao/Gongkao/APIClient.swift apps/ios/Gongkao/Gongkao.xcodeproj/project.pbxproj
   git commit -m "Add iOS docket client boundary"
   ```

### Task 7: Build iPhone Open-Court Home

Files:

- Create `apps/ios/Gongkao/Gongkao/DocketView.swift`
- Modify `apps/ios/Gongkao/Gongkao/AppShellView.swift`
- Modify `apps/ios/Gongkao/Gongkao/AppModels.swift` only to stop feeding old today into the iPhone home route

Why:

The first screen must become “开衙”， not old 今日 task list.

Impact/Compatibility:

- iPhone `.today` route uses `DocketView`.
- iPad can keep existing shell temporarily but must not influence iPhone main loop.

Steps:

1. Write UI compile check:
   - `DocketView` has states: loading, auth required, open court CTA, docket loaded.
   - Hero displays rank, court risk, and one companion line.
   - It does not render `GoalStripView` or old `FeedCardList`.

2. Verify RED:
   ```bash
   xcodebuild build \
     -project /Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao.xcodeproj \
     -scheme Gongkao \
     -destination 'platform=iOS Simulator,name=iPhone 17' \
     CODE_SIGNING_ALLOWED=NO
   ```
   Expected before wiring: references fail or old route still compiles without new view.

3. Minimal code:
   - Add `DocketView(store:)`.
   - In `AppShellView`, create `@StateObject private var docketStore = DocketStore()`.
   - Route iPhone today tab to `DocketView`.
   - Keep old `TodayView` file but remove it from main iPhone path.

4. Verify GREEN:
   ```bash
   xcodebuild build \
     -project /Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao.xcodeproj \
     -scheme Gongkao \
     -destination 'platform=iOS Simulator,name=iPhone 17' \
     CODE_SIGNING_ALLOWED=NO
   ```

5. Commit:
   ```bash
   git status --short
   git add apps/ios/Gongkao/Gongkao/DocketView.swift apps/ios/Gongkao/Gongkao/AppShellView.swift apps/ios/Gongkao/Gongkao/AppModels.swift apps/ios/Gongkao/Gongkao.xcodeproj/project.pbxproj
   git commit -m "Make open court the iPhone home"
   ```

### Task 8: Build Review Cards UI and Wire Verdict Submission

Files:

- Create `apps/ios/Gongkao/Gongkao/ReviewCardsModels.swift`
- Create `apps/ios/Gongkao/Gongkao/ReviewCardsStore.swift`
- Create `apps/ios/Gongkao/Gongkao/ReviewCardsView.swift`
- Modify `apps/ios/Gongkao/Gongkao/DocketView.swift`
- Modify `apps/ios/Gongkao/Gongkao/Gongkao.xcodeproj/project.pbxproj`

Why:

This is the core fun loop: one card, one verdict, one stamp, one emotional beat.

Impact/Compatibility:

- Does not reuse old `TaskExecutionView` for the primary iPhone loop.
- Old TaskExecutionView remains for temporary internal access until retirement task.

Steps:

1. Write compile/UI contract:
   - `ReviewCardsView` accepts `[DocketCard]`.
   - It renders one active card.
   - It supports tap answer buttons first; swipe gesture can be layered after correctness is stable.
   - It shows `准` or `驳` stamp from server response.

2. Verify RED:
   ```bash
   xcodebuild build \
     -project /Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao.xcodeproj \
     -scheme Gongkao \
     -destination 'platform=iOS Simulator,name=iPhone 17' \
     CODE_SIGNING_ALLOWED=NO
   ```

3. Minimal code:
   - Add `ReviewVerdictRequest` and `ReviewVerdictResponse`.
   - Add `ReviewCardsStore.submitVerdict`.
   - Add card stack with stable dimensions and no nested cards.
   - Add stamp overlay and haptic feedback.
   - Add next-card transition after verdict.

4. Verify GREEN:
   ```bash
   xcodebuild build \
     -project /Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao.xcodeproj \
     -scheme Gongkao \
     -destination 'platform=iOS Simulator,name=iPhone 17' \
     CODE_SIGNING_ALLOWED=NO
   ```

5. Commit:
   ```bash
   git status --short
   git add apps/ios/Gongkao/Gongkao/ReviewCardsModels.swift apps/ios/Gongkao/Gongkao/ReviewCardsStore.swift apps/ios/Gongkao/Gongkao/ReviewCardsView.swift apps/ios/Gongkao/Gongkao/DocketView.swift apps/ios/Gongkao/Gongkao.xcodeproj/project.pbxproj
   git commit -m "Add iPhone review cards loop"
   ```

### Task 9: Add Share Report Artifact

Files:

- Create `apps/ios/Gongkao/Gongkao/ShareReportView.swift`
- Modify `apps/ios/Gongkao/Gongkao/ReviewCardsView.swift`
- Optionally create `backend/app/domains/docket/share.py` if backend needs share metadata

Why:

Attraction requires screenshots and shareable moments. The first version generates local share images after 10 cards.

Impact/Compatibility:

- No server-side image generation.
- Share image must include题源 count, 准/驳 count, 官阶, 幕僚评语, date.

Steps:

1. Write UI compile check:
   - `ShareReportView` renders from a deterministic `ShareReportPayload`.
   - It can be presented after all docket cards are completed.

2. Verify RED:
   ```bash
   xcodebuild build \
     -project /Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao.xcodeproj \
     -scheme Gongkao \
     -destination 'platform=iOS Simulator,name=iPhone 17' \
     CODE_SIGNING_ALLOWED=NO
   ```

3. Minimal code:
   - Add local SwiftUI share card view.
   - Render the view to `UIImage`.
   - Present system share sheet.
   - Do not include private user identifiers.

4. Verify GREEN:
   ```bash
   xcodebuild build \
     -project /Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao.xcodeproj \
     -scheme Gongkao \
     -destination 'platform=iOS Simulator,name=iPhone 17' \
     CODE_SIGNING_ALLOWED=NO
   ```

5. Commit:
   ```bash
   git status --short
   git add apps/ios/Gongkao/Gongkao/ShareReportView.swift apps/ios/Gongkao/Gongkao/ReviewCardsView.swift apps/ios/Gongkao/Gongkao.xcodeproj/project.pbxproj
   git commit -m "Add shareable court report"
   ```

### Task 10: Retire Old iPhone Today/Feed Main Path

Files:

- Modify `apps/ios/Gongkao/Gongkao/AppShellView.swift`
- Modify `apps/ios/Gongkao/Gongkao/AppModels.swift`
- Modify `apps/ios/Gongkao/Gongkao/FeatureViews.swift`
- Modify `apps/ios/Gongkao/Gongkao/FeedCardViews.swift` only if removing main path references
- Modify `backend/app/domains/feed/router.py` docs/comments if route becomes internal/debug only
- Modify `docs/HANDOVER.md`

Why:

Keeping old UI as a silent alternate home keeps the product split-brained.

Impact/Compatibility:

- Old files may stay in tree if used by iPad/debug, but iPhone home cannot route there.
- No automatic old-layout recovery.

Steps:

1. Write negative check:
   ```bash
   rg -n "FeedCardList\\(|TodayView\\(|/api/v1/feed/today|/api/v1/taskstream/today" apps/ios/Gongkao/Gongkao
   ```
   Expected after retirement: no iPhone shell main-path references; debug-only references must be clearly named.

2. Verify RED:
   Run the command before edits and capture current references.

3. Minimal code:
   - Remove feed/today loading from initial iPhone home flow.
   - Keep `taskstream` backend route available for internal transition only.
   - Update `docs/HANDOVER.md` to mark `docket` as homepage owner.

4. Verify GREEN:
   ```bash
   rg -n "FeedCardList\\(|TodayView\\(|/api/v1/feed/today|/api/v1/taskstream/today" apps/ios/Gongkao/Gongkao
   xcodebuild build \
     -project /Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao.xcodeproj \
     -scheme Gongkao \
     -destination 'platform=iOS Simulator,name=iPhone 17' \
     CODE_SIGNING_ALLOWED=NO
   backend/.venv/bin/python -m pytest backend/tests -q
   ```
   Expected: references only in debug/internal contexts; build and tests pass.

5. Commit:
   ```bash
   git status --short
   git add apps/ios/Gongkao/Gongkao docs/HANDOVER.md backend/app/domains/feed/router.py
   git commit -m "Retire old today feed from iPhone home"
   ```

### Task 11: Production Readiness Gate

Files:

- Modify `backend/pyproject.toml`
- Add migration tooling files only after confirming package choice
- Add CI scripts under existing project convention
- Modify `README.md`
- Modify `backend/README.md`

Why:

This is a commercial product. The new core should not ship via untracked manual habits only.

Impact/Compatibility:

- Does not deploy.
- Does not mutate production DB.

Steps:

1. Write check:
   - `backend/.venv/bin/python -m pytest backend/tests -q`
   - iOS simulator build command
   - `rg -n "dockets/open-court" backend apps/ios/Gongkao/Gongkao`

2. Verify RED:
   - Run checks before readiness changes and record missing migration/CI/docs gaps.

3. Minimal code:
   - Add Alembic only if selected and installed in backend dependencies.
   - Add documented local commands for backend and iOS verification.
   - Add a staging-first deploy note; no direct production deploy in this task.

4. Verify GREEN:
   ```bash
   backend/.venv/bin/python -m pytest backend/tests -q
   xcodebuild build \
     -project /Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao.xcodeproj \
     -scheme Gongkao \
     -destination 'platform=iOS Simulator,name=iPhone 17' \
     CODE_SIGNING_ALLOWED=NO
   ```

5. Commit:
   ```bash
   git status --short
   git add backend/pyproject.toml README.md backend/README.md
   git commit -m "Document production readiness gates"
   ```

## Risks

- Backend tests are currently red; Task 1 must complete before new work.
- `AppModels.swift` is already overburdened; any new state added there expands the core maintenance problem.
- `taskstream/feed` may still contain useful scheduling logic; migrate logic, not product shape.
- Punishment mechanics can feel hostile; implementation must include warnings, recovery, and no paid true-result mutation.
- AI personality can become inaccurate or too performative; correctness remains deterministic.

## Retirement

Delete-first candidates after new owner is verified:

- iPhone route to `TodayView`.
- iPhone route to `FeedCardList` as homepage.
- Direct `RankService` calls from practice/essay/mock routes.
- Any frontend display path that turns unavailable score gap into `0.0`.

Confirmation-first candidates:

- Dropping old DB tables.
- Deleting production learning records.
- Migrating `learner.official_ranks` into a new table and removing the old one.

Verification for retirement:

```bash
rg -n "TodayView\\(|FeedCardList\\(|/api/v1/feed/today|/api/v1/taskstream/today|RankService\\(" apps backend/app
backend/.venv/bin/python -m pytest backend/tests -q
xcodebuild build \
  -project /Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao.xcodeproj \
  -scheme Gongkao \
  -destination 'platform=iOS Simulator,name=iPhone 17' \
  CODE_SIGNING_ALLOWED=NO
```

Expected result after retirement: old references are absent from main path or explicitly marked internal/debug; tests and build pass.

## Self-Review

- Spec coverage: covers owner reset, dockets endpoint, iPhone focus, card loop, progression, share artifacts, retirement.
- Placeholder scan: no placeholder tasks; each task has files, why, impact, commands, expected result, commit boundary.
- Type consistency: endpoint paths use `/api/v1/dockets/open-court`; verdict path uses `/api/v1/review-sessions/{docketId}/cards/{cardId}/verdict`.
- Compatibility: no old client compatibility obligation; no production data deletion without scoped confirmation.
- Complexity: new owner files prevent further growth in AppModels/FeatureViews/TaskExecutionView.
- Architecture integrity: taskstream/feed/rank old owners have explicit retirement tracks.
- Verification: backend pytest and iOS xcodebuild commands are specified per slice.
- Decision hygiene: product decisions from parent spec preserved.
