# 全局审计问题台账

审计 commit：`01752f1d7b8b606b6900160eebba0888d8def294`  
性质：只读；已按公共根因去重，hypotheses 与审计阻塞项不计入本表。

## 数量

- P0：1
- P1：2
- P2：11
- P3：1
- 合计：15

## 索引

| ID | 严重度 | 置信度 | 类别 | 功能域 | 修复波次 |
|---|---:|---:|---|---|---|
| `ROOT-MIGRATION-001` | P0 | high | migration / release-governance / startup-readiness | 部署、FastAPI/worker 启动、PostgreSQL schema compatibility | Wave 0 |
| `BACKEND-COMMAND-001` | P1 | high | idempotency / transaction-boundary / progression-consistency | legacy review session、practice answer、等级进度 | Wave 1 |
| `ROOT-JOBS-001` | P1 | high | background-job / lease-recovery / deployment-topology | 隐私导出/注销、iLink 媒体删除、行测错因分析 | Wave 1 |
| `BACKEND-REPLAY-001` | P2 | high | content-release / immutable-data / exact-replay | 方法卡、知识图谱发布、岸岸知识 | Wave 1 |
| `FLOW-MOCK-001` | P2 | high | state-machine / conflict-resolution / server-terminal-state | 模考 | Wave 2 |
| `ROOT-AUTH-001` | P2 | high | authentication / session-state / recovery-protocol | 全部登录后功能；动态确认模考与申论 AI 批改 | Wave 1 |
| `ROOT-CONTRACT-001` | P2 | high | OpenAPI / generated-contract / quality-gate | FastAPI → 微信小程序接口合同 | Wave 0 |
| `ROOT-GATE-001` | P2 | high | quality-gate / route-inventory / coverage-governance | 微信小程序全部注册路由与覆盖门禁 | Wave 0 |
| `ROOT-IDEMP-CLIENT-001` | P2 | high | idempotency / unknown-outcome / duplicate-write | AI 岸岸、今日计划、督学设置 | Wave 1 |
| `ROOT-REQUEST-001` | P2 | high | request-lifecycle / cancellation / stale-state-race | 报告中心及其他 onShow 聚合读取页 | Wave 1 |
| `ROOT-TESTDATA-001` | P2 | high | test-infrastructure / frozen-content-contract / regression-governance | 计划、刷题、结果、错因、媒体、结算及行测验收 | Wave 0 |
| `STATE-ANAN-001` | P2 | high | loading-empty-error-success conflation | AI 岸岸 | Wave 2 |
| `UI-CAPTURE-001` | P2 | high | UI/UX-responsive-layout | 拍照搜题 | Wave 3 |
| `UI-PRACTICE-ANSWER-CARD-001` | P2 | high | UI/UX-responsive-layout | 刷题 | Wave 3 |
| `UI-ESSAY-TITLE-001` | P3 | high | UI/UX-typography | AI 批改 / 申论评分依据 | Wave 4 |

## ROOT-MIGRATION-001 — revision 声明为 _68/_69/_70 三套

- severity：`P0`
- confidence：`high`
- category：migration / release-governance / startup-readiness
- 功能域：部署、FastAPI/worker 启动、PostgreSQL schema compatibility
- 页面或业务链路：artifact activation → Alembic baseline-to-head → /ready → runtime cutover
- 状态：`confirmed`
- 推荐波次：Wave 0

### 前置条件

1. 使用当前 artifact 和仓库定义的 audited legacy baseline
2. 本地临时 PostgreSQL；不访问生产

### 复现步骤

1. 运行 cd backend && .venv/bin/alembic heads，确认单 head 20260728_70
2. 比较 Settings 默认 _68、ops/env.example _69、artifact contract _70
3. 运行 test_wechat_identity_migration_upgrades_from_audited_baseline
4. 观察升级在 revision 20260716_33 调用当前 MockExamRepository 时 TypeError

### 期望 / 实际 / 影响

- 期望行为：所有 artifact/runtime/ops/readiness 权威解析到同一 head，audited baseline 能执行每个迁移并启动当前应用。
- 实际行为：revision 声明为 _68/_69/_70 三套；激活脚本会拒绝不等于 artifact head 的配置；真实历史回放在 revision 33 因历史 migration import 可变当前应用接口而停止。
- 用户影响：使用仓库记录的 baseline 的环境无法升级到当前 head，release 可在 cutover 前被阻断，或 /ready 因陈旧 runtime revision 拒绝已正确迁移的数据库。属于可复现的启动/发布阻塞；本轮未声称生产当前已处于该状态。

### 证据

- `audit/subagents/e/migration-audit.md` — single-head topology and baseline-to-head execution failure
- `audit/subagents/e/evidence/failure-release-and-migration-targeted.log` — upgrade stops at revision 33 with mutable repository signature
- `audit/subagents/e/evidence/revision-drift.txt` — tracked revision authorities disagree
- `audit/subagents/e/evidence/migrations-import-runtime-code.txt` — historical migrations import mutable app modules
- `audit/baseline/results.tsv` — heads/history pass while executable migration pytest fails

### 根因与范围

- 推定根因：数据库兼容 revision 被手工复制到多处；历史迁移依赖可变 app.* 接口；普通 pytest 使用 shadow DDL 而非真实 Alembic chain，拓扑与可执行性没有统一权威。
- 受影响页面或模块：

  - 全部 API 与 worker artifact activation
  - /ready
  - backend/migrations/versions/20260716_33_stage6_mock_reporting.py
  - backend/app/core/config.py
  - ops/release-db-compatibility.env
  - ops/scripts/activate_release.sh
  - backend/tests database fixture

### 推荐修复方向

1. 由 artifact Alembic head 生成唯一兼容权威并供 settings/ops/readiness/tests 消费
2. 历史 migration 的 transform 固化在 migration 内或永久版本化兼容模块
3. CI/release preflight 必须运行 audited-baseline → head PostgreSQL 回放与应用 smoke

### 推荐回归测试

1. audited baseline → head
2. settings/artifact/ops/readiness revision 完全相等
3. head 数据库上的 API/worker startup smoke
4. shadow fixture 与真实链的 schema/data fingerprint 对账

- 去重说明：把 revision 漂移、shadow schema 和 revision 33 失败合成一个迁移/发布权威根因。
- 证据边界：本地 audited baseline 确认；未检查生产数据库当前 revision。

## BACKEND-COMMAND-001 — 两次响应返回相同 verdictId，数据库只有 1 条 verdict，却得到 XP=20 和 2 条 xp_gain

- severity：`P1`
- confidence：`high`
- category：idempotency / transaction-boundary / progression-consistency
- 功能域：legacy review session、practice answer、等级进度
- 页面或业务链路：submit verdict → persist answer → award XP → rank event/promotion
- 状态：`confirmed`
- 推荐波次：Wave 1

### 前置条件

1. 已认证用户拥有可作答 review card，同一逻辑答案发生网络重试

### 复现步骤

1. 对同一 /api/v1/review-sessions/{docket}/cards/{card}/verdict 连续 POST 相同 body
2. 比较两次响应的 verdictId
3. 查询 learner.verdicts、official_ranks 与 rank_events

### 期望 / 实际 / 影响

- 期望行为：同一 command 重试返回原结果且不重复副作用；答案和进度原子提交，或以同一 command key 通过幂等 outbox 只发布一次。
- 实际行为：两次响应返回相同 verdictId，数据库只有 1 条 verdict，却得到 XP=20 和 2 条 xp_gain；verdict、docket-card、active date、XP、rank event、promotion 由不同层分别 commit。
- 用户影响：普通网络重试会产生错误等级结果并重复发放 XP；中途失败还可留下答案已保存但进度未写，或进度被重放而没有新的逻辑答案。

### 证据

- `tools/audit/e/test_confirmed_backend_risks.py` — same verdict replay preserves one verdict but awards XP twice
- `audit/subagents/e/evidence/confirmed-risk-probes.log` — duplicate-XP probe passes on ephemeral PostgreSQL
- `audit/subagents/e/evidence/domain-files-with-explicit-commits.txt` — repository/service commit ownership inventory
- `backend/app/domains/review_session/service.py` — verdict and progression orchestration
- `backend/app/domains/review_session/repository.py` — verdict repository commit boundary

### 根因与范围

- 推定根因：后端命令没有 immutable command identity；use-case transaction ownership 下沉到多个 repository/service commit，重放与部分失败无法共享一个原子边界。
- 受影响页面或模块：

  - 动态确认：/api/v1/review-sessions/*/verdict
  - learner.verdicts / official_ranks / rank_events
  - 同构待回归：/api/v1/practice/tasks/*/answers

### 推荐修复方向

1. 要求不可变 command key/record 并对 same-key/different-payload fail closed
2. 把 verdict 与 progression 收敛到单一 unit-of-work
3. 如需异步发布，以 answer command key 驱动幂等 outbox/rank consumer

### 推荐回归测试

1. 相同 command 重放后 verdict=1、xp_gain=1、XP 只增加一次
2. same-key/different-payload conflict
3. 并发重复 submit
4. 在每个 durable step 后注入失败并验证答案/进度不变量

- 去重说明：只把动态确认的 verdict/XP 作为实际行为；practice answer 是同构回归范围，不冒充已动态复现。
- 证据边界：本地临时 PostgreSQL 动态确认；未访问生产。

## ROOT-JOBS-001 — 7 天后 data-rights job 仍为 running 且带旧 claim token

- severity：`P1`
- confidence：`high`
- category：background-job / lease-recovery / deployment-topology
- 功能域：隐私导出/注销、iLink 媒体删除、行测错因分析
- 页面或业务链路：enqueue → claim → durable/external work → complete/retry
- 状态：`confirmed`
- 推荐波次：Wave 1

### 前置条件

1. worker 在 claim commit 后退出，或按仓库标准 release topology 部署

### 复现步骤

1. 在临时 PostgreSQL enqueue 并 claim data-rights job，commit running
2. 推进审计时间 7 天并再次 process_next，读取 job 状态
3. 对账 CauseAgent producer/entrypoint 与 ops/systemd、activate_release runtime_units
4. 检查 iLink deleting claim 的后续可领取状态集合

### 期望 / 实际 / 影响

- 期望行为：每个 produced job type 都有部署 consumer、lease 过期、轮询/启动自动恢复和 readiness/metrics owner。
- 实际行为：7 天后 data-rights job 仍为 running 且带旧 claim token；iLink deleting 可在 crash 后不可见；cause-agent 会 enqueue 且有 Python entrypoint，但标准 systemd/activation 不启动它，entrypoint 也未调用已有 recover_stale_claims。
- 用户影响：隐私导出/删除或对象清理可能永久卡住；行测错因分析/reflection 在标准部署中可一直 queued，核心学习链路无法完成。

### 证据

- `tools/audit/e/test_confirmed_backend_risks.py` — seven-day stale data-rights claim remains running
- `audit/subagents/e/evidence/confirmed-risk-probes.log` — stale-claim probe passes
- `audit/subagents/e/report.md` — producer/consumer/recovery topology inventory
- `ops/scripts/activate_release.sh` — standard runtime units omit cause-agent worker
- `backend/app/domains/ilink_channel/repository.py` — deletion claims move to deleting while future claims select pending/failed
- `backend/app/workers/ilink_channel.py` — iLink worker claim/delete sequence
- `backend/app/workers/xingce_cause_agent.py` — cause worker entrypoint exists but does not invoke stale recovery
- `ops/systemd` — repository standard units contain no cause-agent service

### 根因与范围

- 推定根因：没有 build-time job registry 将 producer、consumer unit、lease duration、stale recovery、readiness 和告警绑定为一个可执行合同。
- 受影响页面或模块：

  - DataRightsWorker
  - ILinkWorker/object deletion
  - CauseAgentWorker
  - privacy pages
  - reflection/cause-analysis
  - ops/systemd and activate_release

### 推荐修复方向

1. 建立 job-type registry 与部署断言
2. 使用 lease_expires_at 并在每次 claim/start 前恢复 stale claim
3. 把 cause-agent unit 纳入标准 topology 与 readiness/queue-age metrics

### 推荐回归测试

1. claim 后、外部调用后、completion 前 kill 并重启，最终只收敛一次
2. deployment manifest 对每个 producer 断言一个 active consumer
3. stale deleting iLink object 可回收
4. cause job 在精确 release topology 下到达终态

- 去重说明：三个 domain 症状合并为统一 job registry/lease/deployment 根因。
- 证据边界：仓库 topology 和本地 claim 动态确认；未声称生产已有卡单。

## BACKEND-REPLAY-001 — BEFORE INSERT trigger 在 ON CONFLICT 的等值判断前抛出 immutable 错误

- severity：`P2`
- confidence：`high`
- category：content-release / immutable-data / exact-replay
- 功能域：方法卡、知识图谱发布、岸岸知识
- 页面或业务链路：content import/republication → immutable release → retry
- 状态：`confirmed`
- 推荐波次：Wave 1

### 前置条件

1. 方法卡已经被 active 或 retired knowledge-map release 引用

### 复现步骤

1. 插入方法卡并创建/激活引用它的 release
2. 用完全相同的逻辑内容再次调用 MethodCardRepository.upsert_cards
3. 运行 targeted immutability test 并观察 BEFORE INSERT trigger

### 期望 / 实际 / 影响

- 期望行为：精确重放成功且不修改 updated_at；相同 ID 的内容漂移才被拒绝。
- 实际行为：BEFORE INSERT trigger 在 ON CONFLICT 的等值判断前抛出 immutable 错误；即使 ON CONFLICT DO NOTHING 也无法精确重放。
- 用户影响：发布后的安全重试和重复导入失败，自动发布/恢复流程非幂等，内容 rollout 可被卡住。

### 证据

- `audit/subagents/e/evidence/failure-release-and-migration-targeted.log` — exact replay is rejected
- `audit/subagents/e/evidence/pytest-failure-clusters.csv` — two tracked failures map to this root
- `backend/migrations/versions/20260727_66_knowledge_release_immutability.py` — BEFORE INSERT immutability trigger

### 根因与范围

- 推定根因：读侧不可变约束放在表级 INSERT trigger，而写侧仍把 replay 表达为 INSERT ... ON CONFLICT。
- 受影响页面或模块：

  - MethodCardRepository
  - scripts/import_method_cards.py
  - knowledge-map publisher
  - Companion shared-card seed/refresh

### 推荐修复方向

1. 定义版本化 command/replay 协议
2. 先解析现存 canonical fingerprint，或通过 stored procedure 对 exact replay 返回成功、对 drift 拒绝

### 推荐回归测试

1. exact replay 保持 timestamps
2. changed payload 失败
3. 并发 exact replay 收敛
4. publisher 在上次 commit 后重试成功

- 去重说明：两个 baseline 测试症状合并为发布后 exact-replay 根因；现有证据只到发布/恢复阻断，未证明当前核心用户任务完全不可用，因此总审计按 P2 收敛。
- 证据边界：本地 PostgreSQL 动态确认。

## FLOW-MOCK-001 — 本地清 pending 后抛普通 Error

- severity：`P2`
- confidence：`high`
- category：state-machine / conflict-resolution / server-terminal-state
- 功能域：模考
- 页面或业务链路：mock-exam submit → 409 resolve → latest submitted/report
- 状态：`confirmed`
- 推荐波次：Wave 2

### 前置条件

1. 本地打开 assigned 模考并拥有编辑 lease
2. 另一设备或先前请求已经使服务端会话进入 submitted

### 复现步骤

1. 当前设备点击交卷并让 submit 返回 mock_submission_conflict 409
2. 完成 conflict/resolve，让 GET session 返回完整且 status=submitted 的最新会话
3. 检查页面 status、interactionLocked、timer、答题卡和报告入口

### 期望 / 实际 / 影响

- 期望行为：以服务端终态为准，停止计时、锁定作答、清除 pending，并进入已交卷或报告生成状态。
- 实际行为：本地清 pending 后抛普通 Error；外层 catch 解除 interaction lock，status 保持 ready，WXML 继续开放答题卡、草稿、工具和 footer。
- 用户影响：用户在当前页无法确认交卷成功或进入报告，并继续看到可编辑的错误状态；退出重进可恢复，因此有替代路径。

### 证据

- `tools/audit/b/mock-terminal-conflict.audit.test.ts` — server submitted while local page remains ready/unlocked
- `audit/subagents/b/evidence/test-runs.md` — mock terminal conflict reproduction passes
- `audit/subagents/b/evidence/source-evidence.md` — exact submit/conflict/catch branches

### 根因与范围

- 推定根因：submitWithLease 已识别 latest session 终止，却把受控终态转换成普通异常；外层分支没有用 latest session 收敛 runtime。
- 受影响页面或模块：

  - pages/mock-exam/index 的交卷冲突分支
  - mock runtime terminal-state projection

### 推荐修复方向

1. 把 latest.status != assigned 建模为成功的终态恢复结果
2. 统一停表、锁交互、更新 session、清 pending，并提供报告或报告生成中入口

### 推荐回归测试

1. 409 → latest submitted
2. 409 → latest reviewed
3. 终态后 timer 停止、作答入口禁用、pending 清理、报告路由可达

- 去重说明：不并入 idempotency；这里服务端终态已经读取成功，缺陷是本地状态收敛。
- 证据边界：动态隔离测试确认；未连接真实后端。

## ROOT-AUTH-001 — 全局 handler 无 owner 信息地清除当前 storage

- severity：`P2`
- confidence：`high`
- category：authentication / session-state / recovery-protocol
- 功能域：全部登录后功能；动态确认模考与申论 AI 批改
- 页面或业务链路：认证 API 401 → 全局 unauthorized handler → 页面 error/retry → 登录恢复
- 状态：`confirmed`
- 推荐波次：Wave 1

### 前置条件

1. App 已安装 installUnauthorizedReset()
2. 场景一：页面以格式有效但服务端失效的 token 发请求
3. 场景二：旧 token 请求仍在途时已写入新 session

### 复现步骤

1. essay-task：用过期 token 和合法路由参数打开页面，让 workspace 返回 401，再点重新加载
2. mock-exam：用过期 token 打开页面，让首个恢复请求返回 401，再点重新恢复
3. 公共层竞态：保持 old-token 请求未完成，写入 new-token session，再让旧请求返回 401

### 期望 / 实际 / 影响

- 期望行为：401 只使仍拥有该请求的 session 失效；当前页面原子进入 auth_required、旧 client/timer/poll 失效并提供一次性安全登录回跳；新 session 不受旧请求影响。
- 实际行为：全局 handler 无 owner 信息地清除当前 storage；essay-task retry 因 session 已空而静默返回；mock-exam retry 复用捕获的 expired bearer；旧请求的迟到 401 还能删除刚写入的新 session。
- 用户影响：用户在模考或 AI 批改中看到可点击但无响应的恢复动作或重复 401；刚完成的新登录也可能被旧请求再次清除，只能手工退出、重登和重新进入任务。

### 证据

- `tools/audit/b/session-expiry.audit.test.ts` — essay retry inert and mock retry reuses expired token
- `audit/subagents/b/evidence/test-runs.md` — 2 session-expiry defect assertions passed
- `audit/subagents/b/evidence/source-evidence.md` — exact client/page branches and correct-control pages
- `tools/audit/d/state-contract.audit.test.ts` — delayed old-token 401 erases new session
- `audit/subagents/d/evidence/test-run.txt` — D common-layer reproduction and cross-run of B tests
- `apps/wechat/miniprogram/api/client.ts` — 401 invokes parameterless global handler
- `apps/wechat/miniprogram/api/auth.ts` — handler unconditionally clears session

### 根因与范围

- 推定根因：unauthorized handler 合同只有无参数的清 storage 副作用，没有 request token/user/session generation，也没有统一认证事件、client 失效、页面状态转换或登录回跳协议；每个页面自行处理造成分叉。
- 受影响页面或模块：

  - 动态确认：pages/essay-task/index
  - 动态确认：pages/mock-exam/index
  - 公共层：api/client.ts、api/auth.ts、auth/session.ts
  - 潜在范围：其他未显式处理 401 的认证页面（未逐页确认）

### 推荐修复方向

1. 401 handler 携带 originating token/session generation，只清除仍匹配的当前 session
2. 建立唯一 session-expired 事件与 auth_required 页面状态合同
3. 页面 runtime 收到事件后停止 timer/poll、失效旧 client 并保留账号隔离的安全 pending command
4. 登录回跳绑定账号、一次性且有 TTL

### 推荐回归测试

1. old 401 after new login 不得清 new session
2. same-token 401 正常失效并只导航一次
3. essay-task 401 → 登录 → 回跳 → 恢复 pending review
4. mock-exam 401 → 停表/保存 pending → 登录 → 重读服务端 session
5. 连续 retry 不得发送旧 bearer；账号切换不得读旧账号 pending

- 去重说明：B 的两个页面症状和 D 的公共层竞态只计一条；D 对 B 测试的复跑不重复计测试覆盖。
- 证据边界：已动态确认两个页面和一个公共层竞态；其他认证页面只是潜在影响范围。

## ROOT-CONTRACT-001 — direct check 明确报告 generated types stale

- severity：`P2`
- confidence：`high`
- category：OpenAPI / generated-contract / quality-gate
- 功能域：FastAPI → 微信小程序接口合同
- 页面或业务链路：后端 OpenAPI 导出 → @hey-api 类型生成 → 小程序 typecheck/调用
- 状态：`confirmed`
- 推荐波次：Wave 0

### 前置条件

1. 检出 commit 01752f1d7b8b606b6900160eebba0888d8def294
2. backend venv 与 apps/wechat openapi-ts 可用

### 复现步骤

1. 在 apps/wechat 运行 node scripts/generate-api.mjs --check
2. 运行 npm run api:check
3. 比较 apps/wechat/miniprogram/api/generated/types.gen.ts 与 audit/api-contract/generated/types.gen.ts
4. 检查 package.json 的 check 是否调用 --check

### 期望 / 实际 / 影响

- 期望行为：提交的生成类型与当前 FastAPI OpenAPI 完全一致；README 中的 api:check 可执行且是总质量门禁的一部分；活跃调用不以手写合同绕开唯一类型权威。
- 实际行为：direct check 明确报告 generated types stale；两份类型相差 502 insertions 和 1 deletion（579 行 diff）；npm run api:check 不存在，正常 check 不调用脚本已实现的 --check；Hub Bot 与 Math Ink 活跃路径使用手写合同。；此外当前 OpenAPI 没有 components.securitySchemes，244 个 operation 中 0 个声明 security，但后端依赖清点确认 246 个路由中 225 个具有传递 bearer 依赖。
- 用户影响：小程序 typecheck 可以在生成合同过期或活跃路径绕过生成类型时通过，前后端字段/操作漂移不能在合并前稳定暴露，问题会推迟到人工联调或运行时。 生成合同还不能表达哪些接口必须认证，API 清点若只信 OpenAPI 会把受保护接口误标为公开。

### 证据

- `audit/baseline/quality-gate-gaps/direct-openapi-check.log` — generated types stale
- `audit/baseline/quality-gate-gaps/readme-api-check.log` — documented api:check is missing
- `audit/api-contract/types.diff` — 579-line generated contract diff
- `audit/api-contract/summary.txt` — 32769 committed lines vs 33270 generated lines; diff exit 1
- `audit/subagents/d/evidence/openapi-impact.md` — current active-path impact and non-impact boundaries
- `apps/wechat/scripts/generate-api.mjs` — --check is implemented but not wired
- `audit/api-contract/openapi.json` — no securitySchemes and no operation security declarations
- `audit/subagents/e/evidence/fastapi-route-auth-inventory.csv` — 225 of 246 routes have transitive bearer dependencies in code

### 根因与范围

- 推定根因：OpenAPI 生成的更新命令、检查模式、package scripts、README 和活跃手写 wrapper 没有被一个强制合同门禁统一治理。
- 受影响页面或模块：

  - apps/wechat/miniprogram/api/generated/types.gen.ts
  - apps/wechat/scripts/generate-api.mjs
  - apps/wechat/package.json
  - apps/wechat/README.md
  - apps/wechat/miniprogram/api/ilink-channel.ts
  - apps/wechat/miniprogram/api/math-ink.ts
  - components/ilink-bot-sheet 与 practice 的相关调用
  - FastAPI OpenAPI auth metadata
  - audit/api-inventory.csv 的认证分类

### 推荐修复方向

1. 恢复并接入只读 api:check，CI/总检查必须比较而不改写提交文件
2. 逐步让活跃 wrapper 直接引用生成 schema，必要的 learner-safe projection 单独显式建模
3. 对必须手写的运行时 projection 增加 schema/fixture contract test
4. 生成物变化必须与后端 schema 变更同一提交审阅

### 推荐回归测试

1. 当前 direct --check 退出 0
2. 后端新增必填字段但不更新前端时总门禁失败
3. Hub Bot 字段缺失/null/完整 fixture 均按 OpenAPI 语义处理
4. Math Ink timing 字段与生成 command 类型一致
5. OpenAPI 必须声明 bearer security scheme，代码受保护路由与 operation security 集合对账

- 去重说明：不要按新增 Practice/HubBot/MathInk schema 拆单；当前没有证据把 legacy practice diff 写成活跃页面崩溃。
- 证据边界：确认的是合同门禁失效与生成物漂移，不是当前 diff 已导致具体用户页面崩溃。

## ROOT-GATE-001 — navigation validator 已使用主包+分包 helper 并通过

- severity：`P2`
- confidence：`high`
- category：quality-gate / route-inventory / coverage-governance
- 功能域：微信小程序全部注册路由与覆盖门禁
- 页面或业务链路：app.json 注册清单 → route manifest → 结构、视觉场景、交互合同校验 → npm run check
- 状态：`confirmed`
- 推荐波次：Wave 0

### 前置条件

1. 检出 commit 01752f1d7b8b606b6900160eebba0888d8def294
2. Node 24 且 apps/wechat 依赖已存在

### 复现步骤

1. 运行 node tools/audit/a/build-static-inventory.mjs，确认 36 个主包路由 + 28 个分包路由 = 64
2. 在 apps/wechat 运行 node scripts/validate-structure.mjs
3. 运行 node scripts/validate-visual-coverage.mjs
4. 运行 node scripts/validate-interaction-coverage.mjs
5. 检查 apps/wechat/package.json 的 check 及 README 中 structure:check

### 期望 / 实际 / 影响

- 期望行为：所有路由消费者使用同一份主包+分包注册清单，区分 58 个业务路由和 6 个资产入口；三个覆盖门禁可通过并由唯一总检查调用。
- 实际行为：navigation validator 已使用主包+分包 helper 并通过；structure、visual coverage、interaction coverage 仍只按 app.pages 判断，分别报 route manifest 不一致、合法分包页未注册/未知；package check 不调用三个门禁，README 声称的 structure:check 也不存在。；官方 verify_wechat/CI 只进入 npm run check，不执行 554 项 Vitest、9 项云函数测试、OpenAPI 新鲜度或四个未接线 validator；根 README 仍写 53 页/3 tabs，实际为 64 注册路由/4 tabs。
- 用户影响：22 个迁入业务分包的页面没有按真实注册口径接受这三类门禁，覆盖数字还能把 88 个视觉状态误当页面数；页面迁移后遗漏会持续留到人工测试才暴露。 即使本地存在大量测试和场景，官方合并门禁也可能从未执行它们；文档口径进一步掩盖真实覆盖分母。

### 证据

- `audit/subagents/a/static-inventory.json` — 64 registered = 58 business + 6 asset entries; no disk/registration mismatch
- `audit/subagents/a/logs/route-validation-results.json` — navigation passes while structure/visual/interaction validators fail on divergent route semantics
- `audit/baseline/quality-gate-gaps/unwired-validate-structure.log` — route manifest mismatch reproduced
- `audit/baseline/quality-gate-gaps/unwired-validate-visual-coverage.log` — legal subpackage page reported unregistered
- `audit/baseline/quality-gate-gaps/unwired-validate-interaction-coverage.log` — legal subpackage page reported unknown
- `audit/baseline/quality-gate-gaps/package-script-inventory.json` — three validators are not referenced by package scripts
- `audit/baseline/summary.md` — CI/package/README wiring and exact pass/fail counts
- `.github/workflows/ci.yml` — WeChat job calls verify_wechat only
- `scripts/verify_wechat.sh` — entrypoint only runs npm run check
- `README.md` — stale 53-page/3-tab count
- `apps/wechat/scripts/validate-structure.mjs` — uses main-package app.pages semantics
- `apps/wechat/scripts/validate-visual-coverage.mjs` — uses main-package app.pages semantics
- `apps/wechat/scripts/validate-interaction-coverage.mjs` — uses main-package app.pages semantics

### 根因与范围

- 推定根因：注册页、业务页、资产入口和视觉场景没有单一带分类的架构权威；分包迁移只更新了一个消费者，总质量门禁又未强制运行其余消费者。
- 受影响页面或模块：

  - 全部 64 个注册路由的覆盖统计
  - 22 个迁入业务分包的页面
  - apps/wechat/scripts/validate-structure.mjs
  - apps/wechat/scripts/validate-visual-coverage.mjs
  - apps/wechat/scripts/validate-interaction-coverage.mjs
  - apps/wechat/package.json
  - apps/wechat/miniprogram/app/navigation/routes.manifest.json
  - .github/workflows/ci.yml
  - scripts/verify_wechat.sh
  - README.md
  - 全部 Vitest 与 cloud tests 的合并门禁

### 推荐修复方向

1. 建立单一 typed/structured route inventory，显式标记 business 与 asset_entry
2. 所有 validator 统一消费 registeredPages(appConfig) 或同一权威清单
3. 明确 manifest 是全部注册路由还是业务路由
4. 把结构、视觉覆盖、交互覆盖接入唯一总检查并校正文档命令
5. 定义一个唯一 verify 入口，执行 type/lint、Vitest、cloud、route/structure/visual/interaction/asset 和 OpenAPI 检查

### 推荐回归测试

1. 三条当前失败的 validator 命令均退出 0
2. 任意页面在主包/分包间迁移后所有消费者返回相同集合
3. 删除任一业务页四件套、视觉场景或交互合同后总门禁精确失败
4. 6 个资产入口显示显式 N/A，不计为业务交互覆盖
5. CI 日志必须出现 554 Vitest、9 cloud tests 及所有覆盖 validator 的明确数量

- 去重说明：不要按 profile/qualification、positions 两个示例页拆单；不要把同一 structure 日志中的未校准 WXSS selector 输出并入本 finding。
- 证据边界：静态与命令级确认；不等同于逐页真实渲染。

## ROOT-IDEMP-CLIENT-001 — 三个页面均对相同 body 生成不同 key

- severity：`P2`
- confidence：`high`
- category：idempotency / unknown-outcome / duplicate-write
- 功能域：AI 岸岸、今日计划、督学设置
- 页面或业务链路：写请求交给 wx.request → transport fail/timeout → 用户以相同输入重试
- 状态：`confirmed`
- 推荐波次：Wave 1

### 前置条件

1. 用户已登录
2. 首次请求可能已到达并由服务端提交，但客户端未收到响应
3. 用户未修改逻辑操作输入

### 复现步骤

1. 岸岸：提交问题，让首次 wx.request 走 fail，恢复原问题并再次提交，比较 body/key
2. 今日容量：确认同一 preview，让首次请求走 fail，再次确认，比较 basedOn body/key
3. 督学偏好：保存同一 draft，让首次请求走 fail，再次保存，比较 basedOn body/key

### 期望 / 实际 / 影响

- 期望行为：同一逻辑操作和同一 canonical input 的未知结果重试复用原 Idempotency-Key，从服务端 replay 已有结果；只有明确改输入、取消或确定性终止后才新建 key。
- 实际行为：三个页面均对相同 body 生成不同 key；岸岸还把 status 0 断言为“没有发送成功”；容量和偏好若首次已提交，新 key 绕过 replay 并使相同 basedOn version 命中 409。
- 用户影响：AI 提问可能形成第二个计额/provider command并失去第一份回答的安全恢复路径；容量或提醒偏好可能已经保存但 UI 报失败，随后重试只显示冲突，用户无法确认真实状态。

### 证据

- `tools/audit/d/state-contract.audit.test.ts` — same body/different key reproduced for companion, capacity and coach preference
- `audit/subagents/d/evidence/test-run.txt` — three unknown-outcome cases passed
- `apps/wechat/miniprogram/pages/anan/index.ts` — new key per ask
- `apps/wechat/miniprogram/pages/day-adjustment/index.ts` — new key per confirm
- `apps/wechat/miniprogram/pages/notification-settings/index.ts` — new key per save
- `backend/app/domains/companion/service.py` — provider uncertainty explicitly requires stable-key replay and does not refund quota
- `backend/tests/test_companion_external.py` — same-key recovery bills provider and quota once
- `backend/app/domains/planning/service.py` — command replay precedes capacity version CAS
- `backend/app/domains/coach_notifications/repository.py` — command replay precedes preference version CAS

### 根因与范围

- 推定根因：公共写层没有以 scope+input fingerprint 管理 logical operation attempt；页面的 submitting/saving 只能防并发双击，transport failure 后原 key 和 outcome uncertainty 都被丢弃。
- 受影响页面或模块：

  - 动态确认：pages/anan/index
  - 动态确认：pages/day-adjustment/index
  - 动态确认：pages/notification-settings/index
  - api/client.ts 与 platform/command-id.ts

### 推荐修复方向

1. 引入 operation attempt：scope、canonical fingerprint、idempotency key、owner session、status
2. timeout/status 0 保留原 attempt 并显示结果待确认
3. 成功、确定性拒绝、用户改输入或明确取消后才清除/替换 key
4. 核心写跨页面/进程持久化，复用 TodayController、PositionStateStore、mock pending store 模式

### 推荐回归测试

1. commit succeeded + response lost + same-input retry 只产生一个服务端 command
2. companion provider billing 与 quota 只增加一次
3. capacity/preference 版本只增加一次且重试拿到原结果
4. 改输入必须新 key；取消后不得 replay
5. app 重启/返回页面后恢复 pending attempt

- 去重说明：岸岸、今日容量、督学偏好三个页面合并为客户端 operation-attempt 根因；刷题内岸岸留在 hypotheses；后端 verdict/XP 另列 BACKEND-COMMAND-001。
- 证据边界：前端请求行为已动态复现；后端 stable-key replay/CAS 由正式源码与既有测试证明，未执行 response-loss 跨服务 E2E，因此按 P2 收敛。

## ROOT-REQUEST-001 — report-center 先写入新 projection，随后被第一个旧响应覆盖，最终 status 仍是 ready

- severity：`P2`
- confidence：`high`
- category：request-lifecycle / cancellation / stale-state-race
- 功能域：报告中心及其他 onShow 聚合读取页
- 页面或业务链路：同一页面实例重复 onShow/load → 后发请求先完成 → 先发请求迟到
- 状态：`confirmed`
- 推荐波次：Wave 1

### 前置条件

1. 同一页面实例允许两个相同资源请求同时在途
2. 第二个请求先返回更新 projection
3. 第一个请求后返回旧 projection

### 复现步骤

1. 实例化 report-center 页面
2. 连续触发两次 onShow
3. 先完成第二个 /api/v2/reporting/center 请求并断言新数据已渲染
4. 再完成第一个请求并检查最终 center

### 期望 / 实际 / 影响

- 期望行为：只有 latest request 且仍匹配当前 page/session owner 的响应可以提交状态；失去 owner 的请求应取消或被忽略。
- 实际行为：report-center 先写入新 projection，随后被第一个旧响应覆盖，最终 status 仍是 ready；ApiClient 丢弃 wx.request 返回的 RequestTask，页面没有 generation/in-flight owner。
- 用户影响：report-center 会在已经显示较新 projection 后退回旧模考状态、报告入口和下一步动作，用户可能基于过期信息继续操作；刷新或重进是替代路径。

### 证据

- `tools/audit/d/state-contract.audit.test.ts` — older report-center response overwrites newer response
- `audit/subagents/d/evidence/test-run.txt` — stale-response reproduction passed
- `apps/wechat/miniprogram/pages/report-center/index.ts` — onShow/loadCenter has no latest generation guard
- `apps/wechat/miniprogram/api/client.ts` — RequestTask returned by wx.request is not exposed or retained
- `audit/subagents/d/state-risk-matrix.csv` — structurally matching onShow pages and evidence levels

### 根因与范围

- 推定根因：公共请求合同没有 cancellation、page disposal、latest generation 或 session ownership；较新的 controller 有局部实现，但直接调用 API 的 Page 没有强制模板。
- 受影响页面或模块：

  - 动态确认：pages/report-center/index
  - 公共能力缺口：api/client.ts 不暴露 RequestTask/cancellation

### 推荐修复方向

1. ApiClient 提供可取消句柄或 AbortSignal
2. 页面/controller 使用单调 generation 并在提交前验证 latest、not disposed、session owner unchanged
3. onHide/onUnload 取消不再需要的请求
4. 把 Today/positions/xingce runtime 的 ownership 模式沉淀成公共 page loader

### 推荐回归测试

1. 第二请求先返回、第一请求后返回时保留第二结果
2. onUnload 后响应不得 setData
3. 登录账号切换后旧响应不得提交
4. silent refresh 失败保留可用旧数据但显示刷新错误
5. 多接口聚合不得混合两个 generation

- 去重说明：正式实际行为只覆盖 report-center；ability、me、membership、notification-settings、privacy、private-knowledge、profile/archive 仅保留为回归扫描候选，不计已确认影响页。
- 证据边界：只有 report-center 动态复现；其他同构页面属于静态回归范围/hypothesis。

## ROOT-TESTDATA-001 — factory 仍模拟旧 mutable question-table 合同

- severity：`P2`
- confidence：`high`
- category：test-infrastructure / frozen-content-contract / regression-governance
- 功能域：计划、刷题、结果、错因、媒体、结算及行测验收
- 页面或业务链路：shared test seed → active frozen release → Xingce plan/task → intended assertion
- 状态：`confirmed`
- 推荐波次：Wave 0

### 前置条件

1. 运行 Git-tracked backend suite 的标准临时 PostgreSQL fixture

### 复现步骤

1. 运行 failure-shared-planning-release-gate.log 中三条定向测试
2. 观察 plan 200 但只包含 essay，公共 helper 查找 xingce 时 StopIteration
3. 检查 shared seed 只插 questions/options，而新选择器要求 active vocab/release/binding/snapshot
4. 对照 pytest-failure-clusters.csv 的 52 个 node id

### 期望 / 实际 / 影响

- 期望行为：公共 factory 构造满足当前 frozen-release authority 的数据，使每条测试到达自身业务断言。
- 实际行为：factory 仍模拟旧 mutable question-table 合同；52 个正式测试在 seed、release activation、binding validation 或 task lookup 阶段退出，没有执行原本断言。
- 用户影响：大量测试数存在但核心行测回归实际上没有执行，新缺陷只能在人工测试中继续暴露。这是一条测试基础设施问题，不代表 52 个线上 Bug。

### 证据

- `audit/subagents/e/evidence/pytest-failure-clusters.csv` — 52 tracked failures map to one frozen-release fixture root
- `audit/subagents/e/evidence/failure-shared-planning-release-gate.log` — representative plan omits Xingce before assertions
- `audit/subagents/e/evidence/failure-media-and-timing-targeted.log` — media/timing tests fail at same shared helper
- `audit/baseline/backend-pytest.xml` — 993 total; 69 failed including 52 tracked cases in this cluster

### 根因与范围

- 推定根因：生产内容权威变为 active vocabulary + release-scoped binding + immutable snapshot，但复用 test factory/acceptance fixture 没有随合同原子升级或版本化。
- 受影响页面或模块：

  - 52 个 node id，详见 failure cluster CSV
  - backend/tests shared Xingce/planning seed builders
  - 计划、行测 attempt/result/cause/media 的回归门禁

### 推荐修复方向

1. 建立唯一 canonical frozen-release test factory
2. 旧 builder 统一委托它；stale/invalid release 使用显式 negative factory
3. 先恢复测试到达率，再判断是否仍有真实业务断言失败

### 推荐回归测试

1. 52 条测试均到达预期业务断言
2. factory 自检 release fingerprint/binding/snapshot/delivery eligibility
3. 禁止静默 fallback 到 legacy product.questions

- 去重说明：52 个失败只建一条；严重度在总审计按“门禁失效、未证实运行时故障”收敛为 P2。
- 证据边界：确认测试基础设施漂移；不声称对应 52 个生产功能均损坏。

## STATE-ANAN-001 — contextStatus=ready、errorMessage 为空，并显示“主目标待确认”“今日 0 项任务”“还没有确认主目标岗位”

- severity：`P2`
- confidence：`high`
- category：loading-empty-error-success conflation
- 功能域：AI 岸岸
- 页面或业务链路：岸岸首页聚合 learning profile + today plan
- 状态：`confirmed`
- 推荐波次：Wave 2

### 前置条件

1. learning-brain profile 成功并返回有效版本/证据
2. GET /api/v2/plans/today 返回 503

### 复现步骤

1. 向岸岸页面注入成功 profile 和失败 today 响应
2. 调用 loadContext
3. 检查 contextStatus、errorMessage、目标与任务文案

### 期望 / 实际 / 影响

- 期望行为：显示 profile 可用但今日计划未载入的 partial/error 状态；不得对目标或任务是否存在下结论。
- 实际行为：contextStatus=ready、errorMessage 为空，并显示“主目标待确认”“今日 0 项任务”“还没有确认主目标岗位”；提问入口仍按完整 ready 工作。
- 用户影响：已有目标与计划的用户会看到相反事实，并被引导重复确认岗位；AI 上下文可用性被错误表达。

### 证据

- `tools/audit/d/state-contract.audit.test.ts` — planning 503 is presented as no goal/zero tasks
- `audit/subagents/d/evidence/test-run.txt` — partial-context reproduction passes
- `apps/wechat/miniprogram/pages/anan/index.ts` — catch(() => null) erases today error before projection

### 根因与范围

- 推定根因：聚合页面用 null 同时表达资源为空和请求失败，没有 absent/empty/unavailable/stale 的 tagged state。
- 受影响页面或模块：

  - pages/anan/index
  - companionContextView

### 推荐修复方向

1. 使用 allSettled/tagged result 保留每个来源状态
2. 依赖失败来源的 insight 不生成确定性结论
3. partial 状态提供独立重试，不阻断仍安全的 profile 信息

### 推荐回归测试

1. profile 成功/today 失败
2. profile 失败/today 成功
3. 真实无目标与 today 失败的文案不得相同
4. stale cache + refresh failure

- 去重说明：当前只有岸岸动态确认，不上升为所有聚合页的重复问题。
- 证据边界：动态隔离测试确认；无真实 API。

## UI-CAPTURE-001 — 四档真实模拟设备均出现橙色错误条覆盖相册文字和快门的情况

- severity：`P2`
- confidence：`high`
- category：UI/UX-responsive-layout
- 功能域：拍照搜题
- 页面或业务链路：/pages/capture-import/index；相机初始化失败后的拍照搜题错误态
- 状态：`confirmed`
- 推荐波次：Wave 3

### 前置条件

1. 已登录
2. 微信开发者工具真实渲染
3. 设备为 iPhone 5（systemInfo 320×568）、HUAWEI nova 14 Ultra（systemInfo/window 363×817）、iPad 竖屏（768×1024）或 iPad 横屏（1024×768）
4. 进入默认“单题”拍照态并由 camera 组件返回相机未启动错误

### 复现步骤

1. 在微信开发者工具选择 iPhone 5，打开 /pages/capture-import/index，等待相机初始化失败反馈出现
2. 观察页面底部“相机没有启动成功，请检查系统相机权限后重试。”提示、相册入口、快门和隐私说明的位置
3. 切换到 HUAWEI nova 14 Ultra、iPad 768×1024 竖屏和 iPad 1024×768 横屏，重复上述步骤
4. 对照 manifest：长安卓快门 top=759.234375、height=69.109375，底部约 828.34 > windowHeight 817；iPad 竖屏快门 top=912、height=72，横屏 top=656、height=72，虽在 viewport 内但截图均显示错误条与相册/快门相交

### 期望 / 实际 / 影响

- 期望行为：错误反馈应完整可读且不遮挡相册或快门；主要/替代操作和隐私说明应位于可视区及安全区内，在相机失败时仍能清楚地选择恢复路径。
- 实际行为：四档真实模拟设备均出现橙色错误条覆盖相册文字和快门的情况；iPhone 5 上提示文字溢出胶囊，长安卓上快门约有 11px 越出 window 底部且隐私说明不可见。iPad 竖屏和横屏的快门虽位于 viewport 内，错误条仍横跨相册与快门。顶部“微信发图”仍提供替代路径，因此核心功能并非完全不可用。
- 用户影响：相机失败正是用户最需要理解原因并选择恢复动作的时刻，但错误反馈与操作控件相互遮挡，降低可读性并可能阻断相册/快门点击；该问题跨小屏、长安卓和平板双方向复现。用户仍可改走顶部微信发图，故定级 P2。违反响应式布局、主操作可达性、错误恢复可见性和安全区适配原则。

### 证据

- `audit/subagents/c/screenshots/small-iphone5-core/h01-capture-search-idle.png` — 320×568 实际截图；错误条覆盖相册文字和快门，提示末行溢出。
- `audit/subagents/c/screenshots/long-android-nova14ultra-core/h01-capture-search-idle.png` — 363×817 实际截图；错误条与快门重叠，快门越出底部，隐私说明不可见。
- `audit/subagents/c/logs/small-iphone5-core-manifest.json` — h01 状态 captured；systemInfo iPhone 5 320×568；快门 top=500.9375、height=60.46875。
- `audit/subagents/c/logs/long-android-nova14ultra-core-manifest.json` — h01 状态 captured；systemInfo HUAWEI nova 14 Ultra 363×817；快门 top=759.234375、height=69.109375；viewport scrollHeight=880。
- `apps/wechat/miniprogram/pages/capture-import/index.wxml:117` — 相册、快门、错误提示和隐私说明相邻渲染。
- `apps/wechat/miniprogram/pages/capture-import/index.wxss:631` — .camera-error 使用 absolute、固定 bottom 与 z-index:3；手机宽度下未纳入正常布局。
- `audit/subagents/c/screenshots/tablet-ipad-landscape-b2/h01-capture-search-idle.png` — 768×1024 iPad portrait 实际截图；历史 raw profile 名含 landscape，但 manifest systemInfo/routeSystemInfo 均为 portrait；错误条覆盖相册与快门。
- `audit/subagents/c/logs/tablet-ipad-landscape-b2-manifest.json` — h01 captured；iPad 768×1024、deviceOrientation=portrait；快门 top=912、height=72、bottom=984。
- `audit/subagents/c/screenshots/tablet-ipad-landscape-real-b2/h01-capture-search-idle.png` — 1024×768 iPad landscape 实际截图；错误条横跨相册与快门。
- `audit/subagents/c/logs/tablet-ipad-landscape-real-b2-manifest.json` — h01 captured；iPad 1024×768、deviceOrientation=landscape；快门 top=656、height=72、bottom=728。

### 根因与范围

- 推定根因：拍照态在小于 700px 宽度时没有使用受 viewport 高度约束的纵向 flex 布局；取景区、固定最小高度控制区和隐私文案累加后超过可用高度，而错误提示又以 absolute + 固定 bottom + z-index 叠加在操作区。页面根节点 overflow:hidden，使溢出的快门/隐私说明无法通过滚动恢复。
- 受影响页面或模块：

  - pages/capture-import/index
  - pages/capture-import/index.wxml
  - pages/capture-import/index.wxss
  - 相机失败错误态（已在 iPhone 5、HUAWEI nova 14 Ultra、iPad 竖屏与 iPad 横屏复现）

### 推荐修复方向

1. 把相机错误反馈纳入正常文档流或为控制区预留显式错误槽；以 windowHeight/safe-area 约束取景区与底部控制区，避免仅按宽度触发平板布局；保证错误态下相册、快门、微信发图及隐私说明互不遮挡。不要用隐藏溢出、缩小点击区或吞掉相机错误规避。

### 推荐回归测试

1. 在 iPhone 5 320×568、HUAWEI nova 14 Ultra 363×817、iPad portrait 768×1024 与 iPad landscape 1024×768 重放 camera init error
2. 断言错误文案完整位于容器内
3. 断言错误条与相册入口、.camera-control、.camera-shutter 几何交集均为 0
4. 断言快门 bottom ≤ safeArea.bottom 且隐私说明与替代入口可见、可点击
5. 在标准 iPhone 12/13 Pro 回归正常相机态、权限拒绝态和相册路径

- 去重说明：C 已按公共视觉根因去重。
- 证据边界：当前 commit 微信开发者工具真实渲染。

## UI-PRACTICE-ANSWER-CARD-001 — iPad 竖屏下浮层整体向右偏移，右侧 72px 超出 viewport

- severity：`P2`
- confidence：`high`
- category：UI/UX-responsive-layout
- 功能域：刷题
- 页面或业务链路：/pages/practice/index；答题中的“答题卡”底部浮层
- 状态：`confirmed`
- 推荐波次：Wave 3

### 前置条件

1. 已登录并进入刷题 answering 状态
2. 微信开发者工具选择 iPad 竖屏，systemInfo/window 为 768×1024
3. 打开答题卡

### 复现步骤

1. 在 iPad 768×1024 portrait 打开 /pages/practice/index?mode=targeted&planTaskId=visual-plan-task-001
2. 触发“答题卡”并观察浮层右侧的图例、题目统计与“继续第 3 题”主按钮
3. 对照 manifest 几何：.answer-card__sheet left=280、width=560，right=840 > windowWidth 768；.answer-card__cta left=300、width=520，right=820 > 768
4. 切换同一 iPad 到 1024×768 landscape 重放；此时 sheet right=840、CTA right=820，均不越界

### 期望 / 实际 / 影响

- 期望行为：答题卡浮层应在所有支持的平板方向内完整居中或受 viewport 约束；图例、状态摘要、关闭入口和主按钮均应完整可读、可点击。
- 实际行为：iPad 竖屏下浮层整体向右偏移，右侧 72px 超出 viewport；右侧图例/统计被裁切，主按钮右侧 52px 被裁切。1024×768 横屏不越界，标准 390px 手机布局也不触发该断点问题。
- 用户影响：用户在刷题过程中无法完整读取答题卡右侧状态，主操作的部分点击区域不可见，增加误解与误触风险；关闭与继续仍有可见区域，存在替代操作空间，故定级 P2。违反响应式容器约束、信息完整性和主操作可达性原则。

### 证据

- `audit/subagents/c/screenshots/tablet-ipad-landscape-b1/c09-xingce-answer-card.png` — 768×1024 iPad portrait 实际截图；右侧图例、内容与 CTA 明显被 viewport 裁切。历史 raw profile 名误含 landscape，运行时为 portrait。
- `audit/subagents/c/logs/tablet-ipad-landscape-b1-manifest.json` — c09 captured；deviceOrientation=portrait；sheet left=280,width=560,right=840；CTA left=300,width=520,right=820；semanticHorizontalOverflow/primaryHorizontalOverflow 均为 true。
- `audit/subagents/c/screenshots/tablet-ipad-landscape-real-b1/c09-xingce-answer-card.png` — 1024×768 iPad landscape 对照截图；答题卡内容与 CTA 完整可见。
- `audit/subagents/c/logs/tablet-ipad-landscape-real-b1-manifest.json` — 横屏 c09 sheet right=840、CTA right=820，semanticHorizontalOverflow/primaryHorizontalOverflow 均为 false。
- `apps/wechat/miniprogram/pages/practice/index.wxss:715` — 基础规则为 .answer-card__sheet 同时 left:0、right:0。
- `apps/wechat/miniprogram/pages/practice/index.wxss:1238` — @media (min-width:768px) 在保留 left:0 的同时设置 right:50%、width:100%、max-width:560px、transform:translateX(50%)。

### 根因与范围

- 推定根因：768px 断点的居中规则与基础定位规则叠加后成为过约束布局：left:0 未清除，width/max-width 固定为 560px，再执行 translateX(50%)，最终把浮层左边移到 280px。竖屏可用宽度 768px 时右边达到 840px；横屏宽度 1024px 时同一错误几何尚未越界，因此问题具有方向/临界断点特征。
- 受影响页面或模块：

  - pages/practice/index
  - pages/practice/index.wxss
  - 答题卡 answer-sheet 状态（iPad 768×1024 portrait 已证实；1024×768 landscape 未越界）

### 推荐修复方向

1. 让平板断点使用单一、可证明的居中约束，例如明确的 50% 左锚点配合负 50% 自身位移，或使用受 viewport padding 约束的 inset/auto margin；清除与新规则冲突的 left/right 值，并保证 768px 临界宽度下 sheet 与 CTA 均不越界。不要用 overflow:hidden 掩盖内容。

### 推荐回归测试

1. 在 767、768、800 与 1024px 宽度重放 c09 answer-sheet
2. 断言 sheet.left ≥ safeArea.left、sheet.right ≤ safeArea.right
3. 断言 CTA 和关闭按钮完整可见且点击区域不被裁切
4. 回归 iPad portrait/landscape、标准 iPhone 390px，以及 c10 pause/c15 岸岸上下文等同页 overlay 状态

- 去重说明：C 已按公共视觉根因去重。
- 证据边界：当前 commit 微信开发者工具真实渲染。

## UI-ESSAY-TITLE-001 — 标题末字“楚”在 390px、320px 和 363px 三种真实渲染宽度上均成为单字孤行。

- severity：`P3`
- confidence：`high`
- category：UI/UX-typography
- 功能域：AI 批改 / 申论评分依据
- 页面或业务链路：/pages/essay-evidence/index；评分依据 ready 态
- 状态：`confirmed`
- 推荐波次：Wave 4

### 前置条件

1. 已登录
2. 评分依据数据为 ready
3. 系统默认字号
4. 微信开发者工具真实渲染

### 复现步骤

1. 在 iPhone 12/13 Pro（390×844）打开 /pages/essay-evidence/index?taskVersionId=visual-essay-task
2. 观察标题“为什么这样评，一条一条说清楚”的换行
3. 在 iPhone 5（320×568）和 HUAWEI nova 14 Ultra（systemInfo 363×817）重复
4. 三档设备均可看到末字“楚”单独占据下一行

### 期望 / 实际 / 影响

- 期望行为：核心页面标题应按语义成组换行，避免单个汉字成为孤行，并保持清晰、紧凑的信息层级。
- 实际行为：标题末字“楚”在 390px、320px 和 363px 三种真实渲染宽度上均成为单字孤行。
- 用户影响：不影响评分内容和操作完成，但破坏首屏标题扫描节奏、浪费垂直空间并削弱专业感。违反标题可读性、语义换行和响应式排版原则，定级 P3。

### 证据

- `audit/subagents/c/screenshots/standard-390x844/f12-essay-grounding-evidence.png` — 390px 宽实际截图，末字“楚”孤行。
- `audit/subagents/c/screenshots/small-iphone5-core/f12-essay-grounding-evidence.png` — 320px 宽实际截图，末字“楚”孤行。
- `audit/subagents/c/screenshots/long-android-nova14ultra-core/f12-essay-grounding-evidence.png` — 363px 宽实际截图，末字“楚”孤行。
- `apps/wechat/miniprogram/pages/essay-evidence/index.wxml:2` — 固定标题文案“为什么这样评，一条一条说清楚”。
- `apps/wechat/miniprogram/features/essay-workbench/page-shell.wxss:19` — 共享 .workbench-title 的 max-width、字号和字距组合产生稳定孤行。

### 根因与范围

- 推定根因：该固定标题长度与共享 .workbench-title 的 max-width:650rpx、字号及字距组合在常见手机宽度上形成稳定的单字余行，且没有面向中文标题的平衡换行或文案断句策略。
- 受影响页面或模块：

  - pages/essay-evidence/index
  - features/essay-workbench/page-shell.wxss（共享标题样式；本轮仅在 essay-evidence 文案上证实）

### 推荐修复方向

1. 优先调整标题文案断句或该页标题可用宽度/字号，使换行按完整短语发生；如修改共享样式，先检查所有 essay-workbench 页面，避免为单页修复引入新的标题溢出。不要按设备硬编码多个换行版本。

### 推荐回归测试

1. 对 f12 ready 态执行 320、363、390px 三档真实截图回归
2. 断言标题不存在单字行且不横向溢出
3. 回归 f05/f06/f07/f08/f10/f11 等共享 workbench-title 页面

- 去重说明：C 已按公共视觉根因去重。
- 证据边界：当前 commit 微信开发者工具真实渲染。
