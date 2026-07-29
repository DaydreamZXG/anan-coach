# 全局质量审计修复计划

基线 commit：`01752f1d7b8b606b6900160eebba0888d8def294`  
输入权威：最终 `audit/findings.jsonl`，共 15 条 confirmed finding  
性质：**proposal / 只读规划草案**，不是修复授权，也不代表发布或生产就绪  
生成时间：2026-07-28（Asia/Shanghai）

## 1. 范围、证据边界与实施治理

本文件只编排最终主台账中的 15 个 ID：

| Wave | IDs | 数量 |
|---|---|---:|
| Wave 0 | `ROOT-MIGRATION-001`、`ROOT-CONTRACT-001`、`ROOT-GATE-001`、`ROOT-TESTDATA-001` | 4 |
| Wave 1 | `BACKEND-COMMAND-001`、`ROOT-JOBS-001`、`BACKEND-REPLAY-001`、`ROOT-AUTH-001`、`ROOT-IDEMP-CLIENT-001`、`ROOT-REQUEST-001` | 6 |
| Wave 2 | `FLOW-MOCK-001`、`STATE-ANAN-001` | 2 |
| Wave 3 | `UI-CAPTURE-001`、`UI-PRACTICE-ANSWER-CARD-001` | 2 |
| Wave 4 | `UI-ESSAY-TITLE-001` | 1 |

状态必须明确区分：

- **当前已证实：** 台账中的复现、证据、严重度和 recommended wave。
- **目标：** 原始复现步骤不再复现，并通过对应回归。
- **本文件提案：** 文件 owner、依赖、兼容与回滚策略；进入修复前仍需冻结。
- **未知：** 生产容量、job lease 时长、告警阈值和真实环境现状。不得用未经测量的数字补齐。

实施前必须遵守：

1. 共享合同、Alembic revision、生成代码、`apps/wechat/package.json`、公共请求层和最终
   集成分别只有一个 owner。
2. 不允许两个并行工作包编辑同一文件或从同一个 Alembic head 各建一条 revision。
3. 每个工作包冻结 objective、non-goals、依赖、互斥文件、输入/输出合同、兼容策略、
   happy/failure/replay/race/recovery 回归和 stop condition 后才能实施。
4. 数据库变更优先 additive 与 forward-compatible；不手改 revision，不删除不可变事实，
   不以 destructive downgrade 作为常规回滚。
5. 修复后必须重跑 finding 的原始复现；不得用永久 Mock、skip、空 catch、`any` 或降低
   断言让门禁变绿。

## 2. 依赖与文件所有权总图

```text
Wave 0
  W0-MIGRATION ───────┬──> W0-TESTDATA
                      └──> W1-DB-CONTRACTS
  W0-CONTRACT ────────┐
  W0-GATE ────────────┴──> W0-WECHAT-INTEGRATION ──> W1-CLIENT-CORE

Wave 1
  W1-DB-CONTRACTS ────┬──> W1-BACKEND-COMMAND
                      ├──> W1-JOBS
                      └──> W1-BACKEND-REPLAY

  W1-CLIENT-CORE ─────┬──> W1-AUTH-CONSUMERS ──> W2-MOCK
                      ├──> W1-REQUEST-CONSUMER
                      └──> W1-IDEMP-CLIENT ─────> W2-ANAN

Wave 2
  W2-MOCK 与 W2-ANAN 在各自前置 owner 结束后可以并行。

Wave 3 / 4
  W0-GATE ──> W3-CAPTURE-RESPONSIVE
  W0-GATE ──> W3-PRACTICE-ANSWER-CARD
  W0-GATE ──> W4-ESSAY-TITLE
  三个 UI 包路径互斥时可并行；共享视觉集成与标准、小屏、长安卓、iPad 竖屏、iPad
  横屏截图回归由单一 owner 完成。
```

`W1-DB-CONTRACTS` 独占全部 Wave 1 Alembic revision。`W1-CLIENT-CORE` 独占
`apps/wechat/miniprogram/api/client.ts`、`api/auth.ts` 和 `auth/session.ts`；三个客户端
finding 的页面工作包只能消费其冻结接口，不能并行修改公共层。

## 3. Wave 0 — 恢复可信迁移、合同和测试门禁

### 3.1 Wave 0 目标与 IDs

Wave 0 只恢复后续修复所需的可信基础设施，不宣称业务流程已经修复。

| ID | 严重度 | 修复目标 |
|---|---:|---|
| `ROOT-MIGRATION-001` | P0 | audited baseline 可执行升级到唯一 artifact head |
| `ROOT-CONTRACT-001` | P2 | 生成类型、OpenAPI 鉴权元数据和只读 drift check 一致 |
| `ROOT-GATE-001` | P2 | 64 个注册路由使用同一覆盖口径并进入唯一合并门禁 |
| `ROOT-TESTDATA-001` | P2 | 52 条测试到达其原本业务断言 |

### 3.2 W0-MIGRATION — 单一 revision authority

- **ID：** `ROOT-MIGRATION-001`
- **建议修改文件（进入修复前冻结）：**
  - `backend/app/domains/mock_exam/repository.py`
  - 建议新增 `backend/app/domains/mock_exam/migration_compat.py`
  - `backend/app/core/config.py`
  - `backend/app/domains/operations/router.py`
  - `ops/release-db-compatibility.env`
  - `ops/env.example`
  - `ops/scripts/activate_release.sh`
  - `ops/scripts/rollback_release.sh`
  - `backend/tests/test_migrations.py`
  - `backend/tests/test_stage9_release_readiness.py`
  - `backend/tests/conftest.py`
- **只读证据、禁止修改：**
  - `backend/migrations/versions/20260716_33_stage6_mock_reporting.py`
  - 已发布 migration history；本包不得改写其 checksum 或内容。
- **兼容方案：** 保留 revision 33 的历史调用不变；在
  `MockExamRepository.get_frozen_question_sources` 恢复一个明确标记、永久测试的 legacy
  调用分支，并把该分支所需的 revision-33 查询固化到 `migration_compat.py`。当前业务
  调用仍必须显式传入 `knowledge_release`，不得借兼容分支绕过 frozen-release 权威。
  如果隔离回放证明还需数据修复，只能从当前 head 新建单一 forward revision，并先重新
  冻结本包文件清单。
- **影响范围：** 全部 API/worker artifact activation、`/ready`、发布/回滚和后端测试
  数据库；不直接修改小程序交互。
- **回归：**
  - 在临时真实 PostgreSQL 执行 audited baseline → current head；
  - settings、artifact、ops、readiness 和 test fixture 的 revision 完全相等；
  - head 数据库上 API 及标准 worker 构造/启动 smoke；
  - shadow fixture 与真实 migration chain 的 schema/data fingerprint 对账；
  - `alembic heads` 始终只有一个 head。
- **回滚：**
  - cutover 前失败时保持旧 artifact 与数据库不变；
  - 数据库已经前进后，只允许回到明确兼容该 revision 的 artifact，或发布 forward
    compatibility 修复；
  - 禁止 `stamp` 掩盖未执行的 DDL/数据转换，禁止覆盖已发布 migration history。
- **并行约束：** 本包是唯一 migration/revision-authority owner；不得与任何修改
  Alembic、`activate_release.sh`、`rollback_release.sh` 或 `backend/tests/conftest.py`
  的包并行。
- **停止条件：** baseline replay 仍失败、出现第二 head、需要真实用户数据修补，或旧
  artifact 与目标 revision 的兼容性无法证明。

### 3.3 W0-CONTRACT — OpenAPI 与生成客户端合同

- **ID：** `ROOT-CONTRACT-001`
- **涉及文件：**
  - `apps/wechat/scripts/generate-api.mjs`
  - `apps/wechat/miniprogram/api/generated/types.gen.ts`
  - `apps/wechat/miniprogram/api/ilink-channel.ts`
  - `apps/wechat/miniprogram/api/math-ink.ts`
  - `backend/app/domains/auth/deps.py`：唯一 bearer security scheme 与受保护 operation
    的 OpenAPI 权威
  - 建议新增 `backend/tests/test_openapi_auth_contract.py`
  - `apps/wechat/tests/ilink-channel.test.ts`
  - 建议新增 `apps/wechat/tests/math-ink-contract.test.ts`
  - `apps/wechat/package.json`、`apps/wechat/README.md` 由
    W0-WECHAT-INTEGRATION 独占接入
- **影响范围：** 全部生成 API 类型消费者；动态重点为 iLink Bot Sheet 与 practice 的
  Math Ink 调用；同时影响 API inventory 的认证分类。当前 finding 证明门禁失效，不等于
  所有消费者都已发生运行时错误。
- **回归：**
  - `node scripts/generate-api.mjs --check` 退出 0；
  - 后端新增必填字段但未提交生成类型时唯一总门禁精确失败；
  - Hub Bot missing/null/full fixture 符合 OpenAPI 语义；
  - Math Ink timing 字段与生成 command 类型一致；
  - OpenAPI bearer operation 集合与代码中 225 个受保护路由对账。
- **回滚：** 后端 schema、OpenAPI artifact 和生成类型作为一个兼容单元回滚；不得只
  回滚生成文件。必要手写 projection 先提供显式兼容适配和 fixture contract test。
- **并行约束：** schema/source 修复可与 W0-GATE 并行；生成文件和 package script
  只由 W0-WECHAT-INTEGRATION 串行写入。
- **停止条件：** `backend/app/domains/auth/deps.py` 无法在不改变权限语义的前提下成为
  单一 OpenAPI 鉴权 owner、生成 diff 包含未登记的业务语义变化，或活跃 wrapper 需要
  改变产品行为。

### 3.4 W0-GATE — 单一路由和验证入口

- **ID：** `ROOT-GATE-001`
- **涉及文件：**
  - `apps/wechat/scripts/validate-structure.mjs`
  - `apps/wechat/scripts/validate-visual-coverage.mjs`
  - `apps/wechat/scripts/validate-interaction-coverage.mjs`
  - `apps/wechat/scripts/validate-navigation.mjs`
  - `apps/wechat/miniprogram/app/navigation/routes.manifest.json`
  - `.github/workflows/ci.yml`
  - `scripts/verify_wechat.sh`
  - `apps/wechat/package.json`
  - `apps/wechat/README.md`
  - `README.md`
- **影响范围：** 全部 64 个注册路由，其中 58 个业务路由、6 个 asset entry；当前漏检
  范围包括 22 个分包业务页面。22 是门禁漏检范围，不是 22 个独立页面 Bug。
- **回归：**
  - structure、visual、interaction 三条当前失败 validator 均退出 0；
  - 页面在主包/分包间迁移后，所有消费者仍返回同一注册集合；
  - 删除任一业务页四件套、视觉场景或交互合同后总门禁精确失败；
  - 6 个 asset entry 显式 N/A，不计入业务交互分母；
  - CI 日志明确出现 554 个 Vitest、9 个 cloud tests 及各 validator 的执行数量。
- **回滚：** validator/manifest/CI 接入独立回滚；不得删除或伪造页面文件迎合门禁。
  回滚后保留旧新集合差异证据，并恢复最后一个确实执行完整检查的入口。
- **并行约束：** validator 源可与 W0-MIGRATION、W0-CONTRACT 并行；所有
  `package.json`、CI、README 和最终 verify 入口由 W0-WECHAT-INTEGRATION 独占。
- **停止条件：** route manifest 的“全部注册路由”与“业务路由”语义仍未冻结，或总
  检查必须修改正式页面才能通过。

### 3.5 W0-TESTDATA — Canonical frozen-release factory

- **ID：** `ROOT-TESTDATA-001`
- **前置：** W0-MIGRATION 的 schema 与 revision contract 已冻结并可执行。
- **涉及文件：**
  - 建议新增 `backend/tests/factories/__init__.py`
  - 建议新增 `backend/tests/factories/frozen_release.py`
  - `backend/tests/test_planning_routes.py`
  - `backend/tests/test_xingce_learning_routes.py`
  - `audit/subagents/e/evidence/pytest-failure-clusters.csv` 中该根因覆盖的 22 个
    Git-tracked test files
- **影响范围：** 52 个 node ID；计划、行测 attempt/result/cause/media/settlement 的
  回归门禁。它是一个测试数据根因，不是 52 个线上问题。
- **回归：**
  - factory 自检 active vocabulary、release fingerprint、binding、snapshot 和
    delivery eligibility；
  - stale/invalid release 只能由显式 negative factory 构造；
  - 52 条测试均越过 seed/activation/binding 前置并到达原业务断言；
  - 禁止静默 fallback 到 legacy `product.questions`；
  - 原业务断言若继续失败，单独生成真实失败清单，不在 factory 中吞掉。
- **回滚：** 只回滚 factory 和测试调用；不修改生产代码适配旧 fixture，不保留永久
  Mock，不降低断言。
- **并行约束：** 依赖 W0-MIGRATION；22 个测试文件由一个 test-data owner 迁移，避免
  多个代理各自复制不同 seed。可与前端 validator/schema 包并行。
- **停止条件：** factory 需要生产代码 fallback、无法建立 canonical release
  fingerprint，或出现不能归因于 fixture 的新业务失败。

### 3.6 W0-WECHAT-INTEGRATION 与退出门禁

W0-WECHAT-INTEGRATION 独占：

- `apps/wechat/package.json`
- `apps/wechat/README.md`
- `apps/wechat/miniprogram/api/generated/types.gen.ts`
- `.github/workflows/ci.yml`
- `scripts/verify_wechat.sh`
- 根目录 `README.md`

它只在 W0-CONTRACT 与 W0-GATE 的源修复完成后，串行接入 `api:check`、route/structure/
visual/interaction、Vitest 和 cloud tests，并生成一次可审阅的类型 diff。

Wave 0 退出条件：

1. `ROOT-MIGRATION-001` 原始 baseline→head 复现通过且单 head；
2. `ROOT-CONTRACT-001` direct check 和鉴权集合对账通过；
3. `ROOT-GATE-001` 的唯一 verify 入口确实执行所有声明检查；
4. `ROOT-TESTDATA-001` 的 52 条测试到达原业务断言；
5. 剩余失败全部映射到已登记的后续 finding，不能存在未分类 failure。

Wave 0 通过仅表示后续修复有可信门禁，不表示全部视觉状态已真实渲染或产品可发布。

## 4. Wave 1 — 公共事务、异步、认证与请求根因

### 4.1 Wave 1 目标与 IDs

| ID | 严重度 | 修复目标 |
|---|---:|---|
| `BACKEND-COMMAND-001` | P1 | 同一逻辑答案只产生一次 verdict 与一次 progression |
| `ROOT-JOBS-001` | P1 | job 可恢复且每种 producer 有标准部署 consumer |
| `BACKEND-REPLAY-001` | P2 | 方法卡 exact replay 成功、内容 drift 拒绝 |
| `ROOT-AUTH-001` | P2 | 旧 401 不清新 session，认证恢复可完成 |
| `ROOT-IDEMP-CLIENT-001` | P2 | unknown outcome 保留并复用同一 operation attempt |
| `ROOT-REQUEST-001` | P2 | 迟到请求、卸载页面和账号切换不能提交旧状态 |

### 4.2 Wave 1 合同和共享 owner

修复前先冻结以下 proposed contracts：

- `BACKEND-COMMAND@v1`：command key、canonical payload fingerprint、same-key replay、
  conflict、单一 unit-of-work 和 emitted progression facts；
- `JOB-LEASE@v1`：job type、consumer、claim、lease expiry、recovery、fencing、终态和
  可观测指标；
- `METHOD-CARD-REPLAY@v1`：exact fingerprint replay 与 same-ID drift 的闭集语义；
- `AUTH-SESSION@v1`：token/session generation、401 owner、一次性回跳、账号隔离；
- `REQUEST-OWNER@v1`：latest generation、disposed、session owner、cancel semantics；
- `OPERATION-ATTEMPT@v1`：scope、canonical input fingerprint、stable idempotency key、
  owner session、unknown/succeeded/terminal 状态。

共享 owner：

- `W1-DB-CONTRACTS`：唯一 Alembic owner，串行承接 command、job lease 和 replay
  function/trigger 所需 migration。
- `W1-CLIENT-CORE`：唯一修改
  `apps/wechat/miniprogram/api/client.ts`、`api/auth.ts`、`auth/session.ts` 的 owner，
  输出冻结的 session-generation、cancel/latest 和 request error contract。

### 4.3 W1-BACKEND-COMMAND — Verdict 与 progression 单一事务

- **ID：** `BACKEND-COMMAND-001`（P1）
- **涉及文件：**
  - `backend/app/domains/review_session/router.py`
  - `backend/app/domains/review_session/schemas.py`
  - `backend/app/domains/review_session/service.py`
  - `backend/app/domains/review_session/repository.py`
  - `backend/app/domains/progression/service.py`
  - `backend/app/domains/rank_system/service.py`
  - `backend/app/domains/rank_system/repository.py`
  - `backend/tests/test_review_session_verdict.py`
  - `backend/tests/test_progression_contract.py`
  - command/outbox schema 由 W1-DB-CONTRACTS 独占
- **影响范围：** 动态确认的
  `/api/v1/review-sessions/*/verdict`、`learner.verdicts`、`official_ranks`、
  `rank_events`；`/api/v1/practice/tasks/*/answers` 仅作为同构回归范围，未确认前不
  自动扩张生产修改。
- **回归：**
  - 相同 command replay 后 verdict=1、`xp_gain`=1、XP 只增加一次；
  - same-key/different-payload 稳定 conflict；
  - 并发相同 submit 只提交一次；
  - verdict、XP、rank event、promotion 每个 durable step 后故障注入仍满足不变量；
  - commit 后响应丢失，客户端使用同 key 可拿到原结果。
- **回滚：**
  - command/outbox 事实只做 additive migration，不在回滚时删除；
  - 服务端先兼容旧客户端，再启用新 command 合同；只有兼容窗口结束后才能要求 key；
  - 旧应用只有在可安全读取新 schema 且不会重新发放 side effect 时才可回切。
- **并行约束：** 领域服务可与客户端 Wave 1 包并行；不得创建 migration，DDL 由
  W1-DB-CONTRACTS 串行。review/progression/rank 文件由一个 owner 处理，避免拆散
  unit-of-work。
- **停止条件：** command 的 canonical write/emitted-fact 集合未冻结，或无法证明
  rollback artifact 不会重复发放 XP。

### 4.4 W1-JOBS — Registry、lease recovery 与部署拓扑

- **ID：** `ROOT-JOBS-001`（P1）
- **涉及文件：**
  - `backend/app/platform/jobs.py`
  - `backend/app/domains/data_rights/worker.py`
  - `backend/app/domains/data_rights/repository.py`
  - `backend/app/domains/ilink_channel/repository.py`
  - `backend/app/workers/ilink_channel.py`
  - `backend/app/domains/xingce_learning/cause_agent/service.py`
  - `backend/app/domains/xingce_learning/cause_agent/repository.py`
  - `backend/app/domains/xingce_learning/cause_agent/worker.py`
  - `backend/app/workers/xingce_cause_agent.py`
  - `backend/app/domains/operations/router.py`
  - `ops/systemd/` 中新增 cause-agent unit
  - `ops/scripts/activate_release.sh`
  - lease/schema migration 由 W1-DB-CONTRACTS 独占
- **影响范围：** privacy export/delete、iLink object deletion、行测 reflection/
  cause-analysis，以及标准 release topology/readiness。
- **回归：**
  - claim 后、外部调用后、completion 前分别 kill/restart，最终只收敛一次；
  - 7 天 stale data-rights claim 与 stale iLink `deleting` 均可重领；
  - registry 对每个 producer 证明 exactly-one deployed consumer；
  - cause job 在精确 release topology 下到达终态；
  - queue age/running age/readiness 按 job type 可观察。
- **回滚：**
  - 新 worker 上线前必须有 fencing；回滚时先停止新 unit，再切换旧 artifact；
  - queued/running job 不删除、不手工改终态，保留给兼容 worker 重领；
  - lease schema additive，回滚不 drop 列、索引或审计事实。
- **并行约束：** 领域 worker 可与客户端包并行；migration 由 W1-DB-CONTRACTS，
  `activate_release.sh` 与 systemd integration 由一个 release-runtime owner 串行。
- **停止条件：** 无法证明 consumer 单实例/幂等、需要触碰真实 queued job，或 lease
  时长/容量必须依赖未测量的生产数据。

### 4.5 W1-BACKEND-REPLAY — 方法卡 exact replay

- **ID：** `BACKEND-REPLAY-001`（P2）
- **涉及文件：**
  - `backend/app/domains/method_cards/repository.py`
  - `backend/app/domains/method_cards/importer.py`
  - `scripts/import_method_cards.py`
  - `backend/tests/test_knowledge_release_immutability.py`
  - `backend/tests/test_companion_external.py`
  - replacement function/trigger migration 由 W1-DB-CONTRACTS 独占
- **影响范围：** MethodCardRepository、方法卡导入、knowledge-map publisher 和
  Companion shared-card seed/refresh；不直接修改小程序页面。
- **回归：**
  - exact replay 返回同一逻辑结果且 timestamps 不变；
  - same ID changed payload 失败；
  - 并发 exact replay 收敛；
  - publisher 上次 commit 后响应丢失，重试成功；
  - active 与 retired release 的不可变语义不被削弱。
- **回滚：**
  - 仅用 forward migration 替换 function/trigger，不回改已应用历史 revision；
  - 应用回滚必须兼容新 replay procedure；
  - 不删除 release、method card 或 fingerprint 事实。
- **并行约束：** importer/repository 可与客户端包并行；数据库 DDL 与 command/job
  migration 由 W1-DB-CONTRACTS 串行排序。
- **停止条件：** canonical fingerprint 规则不唯一，或 exact replay 无法与 drift
  机械区分。

### 4.6 W1-CLIENT-CORE 与 W1-AUTH-CONSUMERS

- **ID：** `ROOT-AUTH-001`（P2）
- **公共层涉及文件（W1-CLIENT-CORE 独占）：**
  - `apps/wechat/miniprogram/api/client.ts`
  - `apps/wechat/miniprogram/api/auth.ts`
  - `apps/wechat/miniprogram/auth/session.ts`
- **页面涉及文件（W1-AUTH-CONSUMERS 独占）：**
  - `apps/wechat/miniprogram/pages/essay-task/index.ts`
  - `apps/wechat/miniprogram/pages/mock-exam/index.ts`
  - `apps/wechat/miniprogram/pages/mock-exam/pending-store.ts`
  - `apps/wechat/miniprogram/pages/mock-exam/runtime.ts`
  - `apps/wechat/tests/api-client.test.ts`
  - `apps/wechat/tests/auth-session.test.ts`
  - `apps/wechat/tests/essay-task-page-lifecycle.test.ts`
  - `apps/wechat/tests/stage2-mock-exam.test.ts`
- **影响范围：** 动态确认 essay-task、mock-exam；其他认证页面仅为同构回归候选，未
  逐页确认不得计为已修复。
- **回归：**
  - old-token 401 在 new login 后返回时不能清除新 session；
  - same-token 401 只失效和导航一次；
  - essay-task：401 → 登录 → 一次性回跳 → 恢复 pending review；
  - mock-exam：401 → 停表/保留 pending → 登录 → 重读服务端 session；
  - 连续 retry 不发送旧 bearer，账号切换不读取旧账号 pending。
- **回滚：**
  - session/return/pending local state 带 owner 与 schema version；
  - 公共层和两个确认页面作为兼容单元回滚；
  - 回滚不得恢复“旧请求清除新 session”；若旧 UI 不兼容，应保持安全 auth-required
    状态而不是清空用户数据。
- **并行约束：** W1-CLIENT-CORE 先串行完成；essay 与 mock consumer 在公共合同冻结后
  可按互斥文件并行。不得与 W1-REQUEST-CONSUMER 或 W1-IDEMP-CLIENT 修改公共层，也
  不得与 W2-MOCK 同时编辑 mock 页面。
- **停止条件：** session generation owner、一次性回跳或账号隔离合同未冻结。

### 4.7 W1-REQUEST-CONSUMER — Latest request ownership

- **ID：** `ROOT-REQUEST-001`（P2）
- **前置：** W1-CLIENT-CORE 已输出 cancel/latest/session-owner contract。
- **涉及文件：**
  - `apps/wechat/miniprogram/pages/report-center/index.ts`
  - 对应正式 Vitest 回归
  - `apps/wechat/miniprogram/api/client.ts` 只由 W1-CLIENT-CORE 修改，本包只消费
- **影响范围：** 动态确认 report-center；其他结构相似页面只进入回归采样，不自动
  声称受影响或完成。
- **回归：**
  - 第二请求先返回、第一请求后返回时保留第二结果；
  - onUnload 后响应不得 `setData`；
  - 账号切换后旧响应不得提交；
  - silent refresh 失败保留可用旧数据并显示刷新错误；
  - 多接口聚合不得混合不同 generation。
- **回滚：**
  - page-loader/request-owner 状态带版本，可单页回滚；
  - 回滚不能重新允许 disposed 或旧 session 请求提交；必要时退化为明确刷新错误。
- **并行约束：** W1-CLIENT-CORE 完成后可与 W1-AUTH-CONSUMERS、W1-IDEMP-CLIENT
  并行，但不得编辑公共 client/auth/session 文件。
- **停止条件：** 页面存在必须保留的并发 merge 语义却无法定义 generation owner。

### 4.8 W1-IDEMP-CLIENT — Unknown-outcome operation attempt

- **ID：** `ROOT-IDEMP-CLIENT-001`（P2）
- **前置：** W1-CLIENT-CORE 的 session owner 与 error classification 已冻结。
- **涉及文件：**
  - `apps/wechat/miniprogram/platform/command-id.ts`
  - 建议新增 `apps/wechat/miniprogram/platform/operation-attempt-store.ts`
  - `apps/wechat/miniprogram/pages/anan/index.ts`
  - `apps/wechat/miniprogram/pages/day-adjustment/index.ts`
  - `apps/wechat/miniprogram/pages/notification-settings/index.ts`
  - 建议新增 `apps/wechat/tests/operation-attempt-store.test.ts`
  - 上述三个页面各自现有测试中的正式回归
  - `apps/wechat/miniprogram/api/client.ts` 只由 W1-CLIENT-CORE 修改，本包只消费
- **影响范围：** 动态确认的岸岸提问、今日容量确认、督学提醒偏好保存；不把其他使用
  command ID 的页面自动加入 finding。
- **回归：**
  - commit succeeded + response lost + same input retry 复用同一 key；
  - Companion provider billing 与 quota 只增加一次；
  - capacity/preference version 只增加一次并返回原结果；
  - 用户修改输入或明确取消后才生成新 key；
  - app 重启、页面返回后恢复 pending attempt；
  - 账号切换不能读取或 replay 另一账号 pending。
- **回滚：**
  - operation-attempt local state 带 schema version 与 owner；
  - 回滚客户端停止创建新格式，但不清除结果未知的 pending；
  - 服务端兼容窗口内继续接受新旧格式；
  - 不以“每次 retry 生成新 key”作为降级。
- **并行约束：** `platform/command-id.ts` 和公共 attempt store 只有一个 owner；合同
  冻结后，三个页面可按互斥文件实现。`pages/anan/index.ts` 完成前不得启动 W2-ANAN。
- **停止条件：** canonical input fingerprint 或确定性拒绝/unknown 的分类不能冻结，
  或恢复需要跨账号共享 pending。

### 4.9 Wave 1 集成、回归与回滚门禁

最终 integration owner 负责：

1. 重跑当前 6 条 Wave 1 finding 的原始复现；
2. 在真实临时 PostgreSQL 上执行 command replay、并发、故障注入和 worker stale recovery；
3. 验证 audited baseline→最新 head 仍可执行且只有一个 head；
4. 运行小程序 Vitest，覆盖 401 竞态、迟到响应、unknown outcome、重启恢复与账号隔离；
5. 对账 producer registry、systemd/runtime units、readiness 与 queue-age metrics；
6. 在隔离的非生产环境演练一次 response loss 和一次 worker crash/restart。

统一回滚约束：

- 数据库事实只 forward repair，不删除 command、lease、release 或 job 历史；
- 客户端 local state 必须版本化并绑定账号；
- 共享 API/client 和页面消费者按合同兼容顺序回滚；
- 任何回滚不能重新引入重复 XP、跨账号 session 清除、重复 provider 计额或孤儿 job。

## 5. Wave 2 — 页面状态机收敛

### 5.1 Wave 2 IDs

| ID | 严重度 | 前置 |
|---|---:|---|
| `FLOW-MOCK-001` | P2 | W1-CLIENT-CORE 与 W1-AUTH-CONSUMERS 完成 |
| `STATE-ANAN-001` | P2 | W1-IDEMP-CLIENT 完成 |

### 5.2 W2-MOCK — 服务端终态优先

- **ID：** `FLOW-MOCK-001`
- **涉及文件：**
  - `apps/wechat/miniprogram/pages/mock-exam/index.ts`
  - `apps/wechat/miniprogram/pages/mock-exam/runtime.ts`
  - 必要时同目录 `index.wxml` / `index.wxss`
  - `apps/wechat/tests/stage2-mock-exam.test.ts`
  - `tools/audit/b/mock-terminal-conflict.audit.test.ts` 的复现转正式回归
- **影响范围：** mock-exam 交卷 conflict/resolve 分支。
- **回归：**
  - 409 → latest submitted；
  - 409 → latest reviewed；
  - 终态统一停表、锁答题入口、清 pending、更新 session；
  - 报告或报告生成中入口可达；
  - 返回/重进仍保持服务端终态。
- **回滚：** 客户端状态机可独立回滚，但必须继续理解 pending schema；不得清除已提交
  session、重开答题或修改服务端事实。
- **并行约束：** 可与 W2-ANAN 并行；不得与任何仍编辑 mock page/runtime 的
  W1-AUTH-CONSUMERS 并行。
- **停止条件：** 后端 submitted/reviewed 的终态合同不稳定，或报告入口需要新增未冻结
  的后端语义。

### 5.3 W2-ANAN — 多来源 tagged state

- **ID：** `STATE-ANAN-001`
- **涉及文件：**
  - `apps/wechat/miniprogram/pages/anan/index.ts`
  - `apps/wechat/miniprogram/features/companion/presentation.ts`
  - 必要时页面 `index.wxml` / `index.wxss`
  - `apps/wechat/tests/anan-runtime.test.ts`
  - `tools/audit/d/state-contract.audit.test.ts` 的复现转正式回归
- **影响范围：** 岸岸首页 profile + today plan 聚合 projection。
- **回归：**
  - profile 成功 / today 失败；
  - profile 失败 / today 成功；
  - 真正无目标/空任务与请求失败显示不同；
  - stale cache + refresh failure 保留旧数据并明确刷新错误；
  - partial context 可安全提问时，发送上下文明示缺失来源。
- **回滚：** 只回滚 projection/UI state；不删除 operation attempt 或服务端数据。旧
  页面若不理解 tagged state，兼容层退化为明确 error，不能退化为伪 empty。
- **并行约束：** 可与 W2-MOCK 并行；必须等待 W1-IDEMP-CLIENT 释放
  `pages/anan/index.ts`。
- **停止条件：** profile/today 的 source ownership 无法区分，或 partial context 是否
  可发送需要新的产品决策。

### 5.4 Wave 2 退出条件

- 两条原始动态复现均通过；
- 正式 Vitest 覆盖正常、失败、返回、重进和 stale 数据；
- 真实 DevTools 渲染若仍被环境阻塞，只能报告状态逻辑验证通过，不能声称 UI 验收；
- 不把同构但未动态确认的页面自动加入完成范围。

## 6. Wave 3 — 页面模板与响应式错误恢复

### W3-CAPTURE-RESPONSIVE — 拍照错误态底部控制区

- **ID：** `UI-CAPTURE-001`（P2）
- **涉及文件：**
  - `apps/wechat/miniprogram/pages/capture-import/index.wxml`
  - `apps/wechat/miniprogram/pages/capture-import/index.wxss`
  - 对应 capture-import 视觉场景、交互合同与正式回归测试
- **影响范围：** 拍照搜题的相机初始化失败态；已在 iPhone 5 `320×568` 与 HUAWEI
  nova 14 Ultra `363×817` 以及 iPad 模拟器竖屏 `768×1024`、横屏 `1024×768`
  真实复现。当前证据不扩张到其他页面。
- **修复目标：** 将错误反馈纳入可计算布局或预留错误槽；按可用 windowHeight 与
  safe-area 约束取景区、相册、快门和隐私说明，保证它们不重叠且可达。
- **回归：**
  - 在小屏、长安卓及 iPad 横竖屏四个原始复现档位重放 camera init error；
  - 错误文案完整位于容器内；
  - 错误条与相册、`.camera-shutter` 几何交集为 0；
  - 快门 bottom 不超过 safe area，顶部“微信发图”和相册路径均可点击；
  - 标准屏回归正常相机、权限拒绝、相册、共享材料和裁剪状态。
- **回滚：** 页面 WXML/WXSS 与视觉合同作为一个兼容单元回滚；不得以隐藏错误、缩小
  点击区、删除隐私说明或继续 `overflow:hidden` 吞掉内容作为降级。
- **并行约束：** 可与不触碰 capture-import 的后端 Wave 1/2 工作包并行；不得与
  W0-GATE 的视觉场景 owner 或其他 capture-import 页面 owner 同时编辑同一文件。
- **停止条件：** 修复需要改变相机授权产品流程、真实设备权限合同或上传语义时，先
  冻结新合同，不在样式包内顺带改变行为。

### W3-PRACTICE-ANSWER-CARD — iPad 竖屏答题卡边界

- **ID：** `UI-PRACTICE-ANSWER-CARD-001`（P2）
- **涉及文件：**
  - `apps/wechat/miniprogram/pages/practice/index.wxss`
  - 只有语义结构确需调整时才涉及
    `apps/wechat/miniprogram/pages/practice/index.wxml`
  - `apps/wechat/e2e/visual-scenarios.mjs` 中现有 answer-card 场景
  - `apps/wechat/tests/tablet-layout.test.ts`
  - 对应 practice 页面正式视觉回归
- **影响范围：** `/pages/practice/index` 的答题卡状态。微信开发者工具 iPad 竖屏
  `768×1024` 中 `.answer-card__sheet` 右边界为 840px、主操作右边界为 820px，均越出
  768px 窗口并发生可见裁切；同一状态在 `1024×768` 横屏没有水平溢出。
- **修复目标：** 让答题卡在断点切换时只有一套水平定位 owner，清除继承的
  `left` 与 `right/transform` 组合造成的偏移；弹层和主操作必须受 viewport/safe-area
  约束，不靠裁切掩盖越界。
- **回归：**
  - 在原始 `768×1024` 竖屏重放 `c09-xingce-answer-card`；
  - `.answer-card__sheet` 与 `.answer-card__cta` 的左右边界均在窗口内；
  - 图例、关闭入口、题号、提示和完整 CTA 可见且可点击；
  - `1024×768` 横屏继续无溢出；
  - 回归 `320×568`、`363×817`、`390×844`，并验证滚动、关闭、切题和继续答题。
- **回滚：** 仅回滚 practice 答题卡响应式样式与其视觉合同；不得以缩小点击区域、
  隐藏图例或全局 `overflow:hidden` 作为降级。
- **并行约束：** 可与 W3-CAPTURE-RESPONSIVE 并行，因为正式页面路径互斥；视觉场景
  与最终截图由唯一 UI integration owner 串行集成。不得与其他 practice 页面 owner
  同时修改 `index.wxss`。
- **停止条件：** 修复需要改变答题卡业务状态、题目切换语义或提交行为时，先拆分并
  冻结新的业务合同，不在响应式样式包内顺带修改。

Wave 3 当前有两条真实渲染 finding。共享导航、品牌色、控件和状态视图没有足够证据
形成其他正式问题；控制台 WXSS selector 警告仍在 hypotheses，不能借本波次顺手扩大
修复范围。

## 7. Wave 4 — 页面特有排版打磨

### W4-ESSAY-TITLE — 评分依据标题孤行

- **ID：** `UI-ESSAY-TITLE-001`（P3）
- **涉及文件：**
  - `apps/wechat/miniprogram/pages/essay-evidence/index.wxml`
  - 若必须调整共享样式：
    `apps/wechat/miniprogram/features/essay-workbench/page-shell.wxss`
  - essay-workbench 相关视觉回归
- **影响范围：** 已确认仅为 essay-evidence 固定标题；320、363、390px 三档真实截图
  均出现末字“楚”单独成行。共享样式只是候选根因，其他页面未确认同一缺陷。
- **修复目标：** 优先以语义断句或该页标题参数解决单字孤行；只有回归所有消费者后
  才能修改共享 `.workbench-title`。
- **回归：**
  - f12 ready 态在 320、363、390px 不出现单字行且无横向溢出；
  - 回归 f05/f06/f07/f08/f10/f11 等共享 essay-workbench 标题；
  - 默认字号之外至少补系统放大字体与长文案回归，不能硬编码设备换行版本。
- **回滚：** 若共享样式引入其他页面回归，回滚共享变更并保留页面级文案/样式方案；
  不改变申论评分业务数据或页面状态机。
- **并行约束：** 可与 W3-CAPTURE-RESPONSIVE 并行，前提是两个 owner 的路径不重叠；
  若选择共享 `page-shell.wxss`，该文件必须由唯一 UI integration owner 修改。
- **停止条件：** 方案依赖设备名单硬编码、截断标题或降低可访问字号时停止。

## 8. 第一修复波次的明确范围

第一修复波次只包含：

1. `ROOT-MIGRATION-001` 的单一 revision authority 与 audited baseline→head；
2. `ROOT-CONTRACT-001` 的 OpenAPI/生成类型只读 drift gate；
3. `ROOT-GATE-001` 的统一路由集合和唯一 verify 入口；
4. `ROOT-TESTDATA-001` 的 canonical frozen-release factory 与 52 条测试断言到达率。

它不包含 Wave 1–4 的业务/UI 修复、不包含任何尚未进入主台账的 hypothesis，也不接触生产
数据库、真实用户数据或真实支付。Wave 0 完成只能表述为“后续修复具有可信门禁”。

## 9. 已确认的审计后产品项

用户于 2026-07-28 确认将以下产品项纳入本轮审计修复之后的统一开发规划，详见
[product-followups.md](product-followups.md)：

- `PF-001`：低成本答后纠偏 MVP。
- `PF-002`：做题页动作层级重排，以及“长期收藏”与标记的“任务操作态/长期证据态”
  分离。

两项都不是现有缺陷，不进入 findings、严重度数量或 Wave 0 完成口径。

### 9.1 PF-001 排期边界

- 它不改变当前 Wave 0 的文件边界和退出条件。
- 它依赖 Wave 0 集成通过，以及 Wave 1 `ROOT-JOBS-001` 中 cause-agent consumer、
  陈旧任务恢复和可观测性收口。
- 与相关修复统一排期时，必须先冻结独立 release unit；现有修复包不得顺手改写作答、
  错因、长期画像或掌握状态。
- 首版范围固定为答后、每次训练一个主要纠偏、一个可执行动作和一道即时变式；实时陪练、
  完整思维链还原和 SwiftUI/iOS 均不在首版范围。

### 9.2 PF-002 排期边界

- **Wave 0 之后、Wave 3 之前：** 先执行 `PF-002A`，分别冻结本次标记状态、长期保留的
  标记动作证据和收藏状态，包括 owner、接口/事件、版本、保留删除、找回入口、幂等和
  多设备语义；未冻结前不能派发界面实现。
- **后端合同包：** `PF-002B` 在 migration/OpenAPI 门禁稳定后实施收藏持久化、账号隔离、
  幂等收藏/取消和查询能力；这是 `PF-002C` 的前置，不能只做一个视觉上可点击的入口。
- **Wave 3 界面包：** `PF-002C` 进入共享做题模板修复：顶部保留返回、答题卡/进度、
  计时和岸岸；材料栏只负责材料；题目栏放收藏、标记和更多；无材料页面采用同一结构。
- **Wave 3 验收包：** `PF-002D` 使用微信开发者工具验证有/无材料、手机三档及平板
  768×1024 竖屏、1024×768 横屏，并覆盖状态恢复、离线、登录过期和重复点击。
- **并行约束：** `PF-002C` 与 `PF-001C` 以及任何做题页答题卡/共享 TaskShell 修改不得
  并行改同一文件；由唯一 UI integration owner 串行或合并为同一已冻结 release unit。
- **涉及文件（预估，实施前复核）：**
  `apps/wechat/miniprogram/task-runtime/task-shell/**`、
  `apps/wechat/miniprogram/pages/practice/**`、题目收藏 API/schema/repository、
  additive Alembic migration 和生成的 OpenAPI 类型。
- **建议回归：** 收藏/标记独立状态、`marked/unmarked` 事件时序与证据保留、仅凭标记
  不生成确定错因、幂等写入、响应丢失重试、跨账号隔离、退出重进、岸岸提交前防泄题、
  更多工具可达性、横竖屏切换与安全区。
- **回滚：** 保留 additive 收藏事实和兼容 API；可回退界面编排，但不得把收藏映射回
  `marked`、清空收藏或回改已应用 migration。

## 10. 实施后残余波次补充

本节是最终五档真实渲染后的增量规划，不改写前述原始 15 条 finding、Wave 0 首波范围
或历史提案。原始 Wave 0–4 的实际收口状态见 `audit/repair-completion.md`。

### Residual Wave R1 — shared responsive physical metrics

- **ID：** `ROOT-RESPONSIVE-METRICS-001`（P2）
- **证据：**
  - `320×568` 下 22/26 个登记主操作低于 44px；
  - `363×817` 下 5/26 个低于 44px；
  - long `h08-capture-crop` 确认按钮超出 viewport `16.171875px`；
  - `390×844`、`768×1024`、`1024×768` 是通过对照。
- **涉及文件：**
  - `apps/wechat/miniprogram/design-system/tokens/index.wxss`
  - `apps/wechat/miniprogram/design-system/stage9/screen.wxss`
  - `apps/wechat/miniprogram/features/positions/page-shell.wxss`
  - `apps/wechat/miniprogram/pages/capture-import/index.wxss`
  - 台账列出的页面专用按钮样式与正式视觉合同
- **影响页面：** 7 条路由、22 个唯一业务场景；触控不足与 crop 纵向越界按同一
  `--rpx` 物理/纵向指标误用根因去重。
- **建议拆包：**
  1. `R1-TOKEN`：建立不随手机宽度缩小的 44px 物理触控 token，公共按钮统一消费；
  2. `R1-PAGE-CONSUMERS`：页面专用按钮迁移到物理 floor 或独立透明热区；
  3. `R1-CROP`：改为弹性可用高度并显式计入 safe area，不以宽度相对值硬减
     `100vh`；
  4. `R1-GATE`：五档运行时几何成为强制门禁。
- **建议测试：**
  - small 与 long 的全部 26 个 primary selector 高度均 `>=44px`；
  - long h08 主操作 `bottom <= viewportHeight` 且完整命中高度至少 44px；
  - 22 个唯一场景逐图复核；
  - 标准手机、平板竖屏、平板横屏全量回归，无异常放大、横溢或 safe-area 冲突；
  - 真实触发 c14 选择工具条，避免沿用固定 `toolbarTop` fixture 证明产品布局。
- **回滚：**
  - `R1-TOKEN` 与 `R1-CROP` 分为独立提交；
  - 若公共 token 影响过广，回滚 token consumer，但保留审计合同；
  - 不触碰 API、状态管理、数据库或用户数据。
- **并行：** `R1-TOKEN/PAGE-CONSUMERS` 与 `R1-CROP` 可由路径互斥 owner 并行；
  `R1-GATE` 和五档最终集成由单一 UI integration owner 串行。
- **停止条件：** 需要改变业务流程、相机权限/上传语义、依赖版本或项目配置时停止并
  重新冻结范围。

本轮只记录该波次，不执行修复。
