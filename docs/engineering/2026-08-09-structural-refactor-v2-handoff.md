# 岸岸项目第二期结构性重构交接

> 状态：第二期 P0/P1 完成；P2 评估完成、未实施
> 分支：`codex/structural-refactor-v2`
> 第二期起点：`3768145bd0f9faca05dd2ff360336f0c88b00d70`
> P0 检查点：`eafeee797`（publication protocol 测试基线）
> 最终源码检查点：`6c2914a83`（Planning Repository 拆分）
> 最终治理单元：`RU-20260809-structural-refactor-v2`
> 冻结哈希：`sha256:b9d18955a0790f0446745a61180fd396bc0d6b8bd4a2f332aa1437d78c52c129`

## 结论

第二期关闭了后端 publication protocol 的四项历史失败，并把 3018 行的 `PlanningRepository` 按真实职责拆成稳定 facade、共享纯合同和五个单责 repository 模块。用户可见行为、公开 API、OpenAPI、数据库语义、迁移、SQL、参数、锁、事务、版本选择和结果映射均未改变。

`learning_brain` 与 `cause_agent` 的 provider 确有技术重复，但现有真实 transport 失败矩阵不足以证明抽取安全，因此本期正确结果是“评估完成、未实施”。没有留下半抽象代码。

本期没有修改小程序、支付、`data_rights`、对象存储、鉴权、申论双模型或模型供应商配置；没有数据库迁移或生产数据回填。未部署后端、未 preview/upload 小程序、未推送远端。

## P0：publication protocol 基线

四项历史失败的共同根因是测试请求固定 `expected_revision="20260807_100"`，而测试数据库按当前迁移图升级到唯一 head `20260808_102`。运行时 guard 会读取 `alembic_version` 并严格要求数据库 revision 等于请求 revision，因此四项测试都在到达目标断言前正确 fail closed。

修复只把 `backend/tests/test_commercial_data_publication_protocol.py` 的冻结 revision 更新为 `20260808_102`，没有修改 `backend/app/db/session.py`、checkpoint engine、publisher、迁移、ACL 或发布配置，也没有放宽断言。现行合同继续保持：

- SQLAlchemy checkout 取得 shared advisory lock，exclusive publication 等既有工作排空；
- standalone guard 绑定 application commit、environment、receipt 与数据库 revision；
- Companion checkpoint engine 继承 production publication guard；
- 行锁超时保持 200ms，`55P03` 后释放 advisory lock；
- 常规 MVP `get_engine()` 仍不安装企业 publication gate。

## P1：Planning Repository 新边界

`backend/app/domains/planning/repository.py` 从 3018 行收缩为 42 行稳定 facade。`PlanningRepository()` 仍为零参数构造，并 eager 创建 `XingceQuestionRepository`；跨域调用者没有修改。

| 模块 | 所有权 | SQL 模板 |
|---|---|---:|
| `repository.py` | facade、五个 mixin 组合、旧兼容 re-export | 0 |
| `repository_support.py` | 5 个 snapshot dataclass 与 13 个共享纯 helper | 0 |
| `capacity_repository.py` | 用户行锁、profile、偏好与容量版本 | 11 |
| `candidate_input_repository.py` | 目标/能力、策略、错因方向、行测候选输入 | 10 |
| `essay_snapshot_repository.py` | 申论 published/current/mastery/transfer 快照 | 9 |
| `plan_storage_repository.py` | 计划 current/history/replay 投影与 append | 8 |
| `execution_repository.py` | 执行事实、任务锁、command replay、evidence gate 与 append | 13 |

总计仍为 48 个 `text(...)` 入口和 51 个可执行 SQL 模板。`execution_evidence_exists` 的完整约 456 行 evidence gate 原块移动，没有内部重构。

### 兼容入口

以下符号继续从 `app.domains.planning.repository` 导入：

- `PlanningRepository`、`PlanningContractError`；
- `ProfileSnapshot`、`PreferenceSnapshot`、`CapacitySnapshot`、`GoalAbilitySnapshot`、`ActiveStrategyDecision`；
- 测试与既有模块使用的 `_review_is_learning_eligible`、`_weakest_dimension`、`_strict_cause_decision_version_id`，以及原有共享 helper。

以下语义差异被原样保留，未做“统一查询”重写：

- `current_task_for_execution` 只接受 current plan；`owned_task_for_execution` 支持历史 replay；
- `current_plan_for_fingerprint`、`current_plan`、`plan_for_capacity`、`plan_for_execution` 分别服务输入幂等、current head、历史 capacity replay 和 execution replay；
- `essay_snapshot(..., goal=...)` 的既有未使用参数保留；
- `_essay_mastery_snapshot` 继续直接使用 `datetime.now(UTC)`；
- repository 模块没有新增 `begin`、`commit`、`rollback`、`flush` 或 savepoint，事务仍由原 route/service caller 拥有。

### 保真与行为证据

- 从第二期起点 `3768145bd` 独立计算的 47 个函数完整 AST hash 与拆分后完全相同；
- 5 个 snapshot dataclass AST 完全相同；
- 51 个 SQL 模板哈希多重集完全相同，覆盖 SQL 字面量、参数表达式、`FOR UPDATE`、结果读取和动态 statement 分支；
- Planning 全域没有 `planning → xingce_learning` 反向 import，新模块也不反向 import facade、service 或 router；
- stale different-command 真实走 `TestClient → auth → router → service → PostgreSQL`，返回既有 `409/revision_conflict`，且不追加 capacity、plan 或 outbox；
- concurrent replan 使用两个独立 PostgreSQL session，`pg_stat_activity.wait_event_type='Lock'` 证明第二事务确实等待用户行锁，释放后只有一个 successor/plan/outbox，另一请求按 stale contract 回滚。

## P2：AI 编排评估

### 已有公共设施

`backend/app/domains/ai/provider_contract.py` 已统一严格单对象 JSON、provider 审计合同、基于幂等能力的失败分类和请求指纹；`external_processing.py`、`model_cost.py` 与 `agent_runtime` 也已承载各自安全公共能力。

### 为何本期不抽取

`learning_brain/provider.py` 与 `xingce_learning/cause_agent/provider.py` 在 OpenAI-compatible POST、配置门禁、响应校验、usage 和元数据上真实重复，但端到端语义不同：

- Learning Brain 在线同步执行，任意 provider 失败均 rollback 后静默跳过，调用方不消费 `retryable`；
- Cause Agent 是 `max_attempts=3` 的持久 job/worker，`retryable` 会决定重试或终止，并拥有冻结 evidence、tool audit、ledger、quota、checkpoint 和 outbox；
- Companion 还有 prepared bytes、source grounding、`provider_uncertain` 重放窗口和不同 usage 容忍语义，明确不属于该抽取。

Learning Brain 当前没有真实 `MockTransport` 成功、坏 JSON、schema invalid、timeout/network、HTTP 状态、usage alias、request ID 和 model drift 矩阵；Cause 的真实 provider 测试也偏成功路径。直接抽取会把未冻结差异藏进公共层，因此没有实施。

### 可执行后续工作包

治理包：`WP-20260809-structural-refactor-v2-ai-followup`。它不属于本期 frozen release unit，开始前必须另建后继 release unit。

允许文件：

- 新建 `backend/app/domains/ai/structured_chat_transport.py`；
- `backend/app/domains/learning_brain/provider.py`；
- `backend/app/domains/xingce_learning/cause_agent/provider.py`；
- 新建 `backend/tests/test_learning_brain_provider.py`；
- `backend/tests/test_xingce_cause_agent_provider.py`。

实施顺序：先为两域补对称真实 transport 测试，覆盖成功、坏 JSON、schema invalid、timeout/network、408/429/5xx、不可重放、usage aliases/types、output capacity、model drift、request ID 和精确 body/header；再比较 prepared bytes、错误类型、usage/latency 与恢复消费者；只有全部相同的无领域语义部分才可下沉。

停止条件：需要修改 worker、service、ledger、quota、checkpoint、outbox、prompt、领域事实、Companion、iLink、申论评审或模型配置；两域 prepared bytes、失败类型、usage/latency 不能证明兼容；测试只能通过 fake provider 而没有执行真实 transport。

## 验证结果

### 治理与范围

- `RU-20260809-structural-refactor-v2` review 与 freeze 校验均为 0 errors、0 warnings；
- P0 冻结执行报告：`.product-governance/execution/reports/4b6dcc0ea0e4519b.1a31c3a8c500bb146081.json`，`passed=true`，6/6 命令通过；
- P1 冻结执行报告：`.product-governance/execution/reports/6a633e87a6f9b846.100e757e43f8adb2b7d5.json`，`passed=true`，6/6 命令通过且 9 个 source diff 全在 allowedPaths；
- `git diff --check` 通过，最终 tracked tree 干净，既有未跟踪用户文件未修改、删除或提交。

### 后端

- P0 独立回归：publication 整文件与 MVP runtime gate 共 13 passed；
- P1 冻结回归：结构门禁 2 passed、Planning routes 29 passed、stale 1 passed、真实 row-lock race 1 passed、行测状态机 4 passed、12 文件跨域矩阵 84 passed；
- 第三波测试审查：关键 selector 8 passed，补充 happy/empty/replay/nested rollback 与跨域 facade 矩阵 9 passed；
- 第一期行测/Companion 相关联合回归：146 passed、0 failed；
- 最终 `backend/tests`：1750 passed、0 failed；
- 既有 Starlette/httpx 与测试环境 warning 只记录，不当作失败解决。

### 静态与合同

- Ruff、compileall、结构 AST/SQL 门禁与 `git diff --check` 通过；
- 最终 OpenAPI 为 1,759,028 bytes，SHA-256 `52eea6f700c36425607a396744296835441820fad14e6b21f79788d521eb0267`，与第二期开始前逐字节相同；
- Alembic 仍为唯一 head `20260808_102`，`backend/migrations/**` 相对第二期起点无改动。

## 第三波审查与剩余债务

两个全新上下文只读审查 Agent 均判定无 P0/P1 当前实现问题，允许进入最终全量。唯一共同残余是静态结构测试没有直接锁定 `PlanningRepository` 的完整 MRO 与全部 33 个基线类方法可达性；审查已独立确认当前 MRO 含五个 mixin、`missing=[]`，并由真实 PostgreSQL 路径覆盖，因此记为非阻断测试加固项，不扩张本期 frozen 路径。

另外两项既有 publication 测试加固建议不属于本期 fixture 修复：shared/exclusive 用例可增加“竞争线程已到达锁请求”的同步证据；checkpoint guard 用例可显式断言 production identity 三元组传播。当前运行时代码未改，四条真实 PostgreSQL 路径均通过。

## 回滚

本期没有迁移或线上数据写入。需要回滚时在本地按依赖逆序执行 `git revert`，不要手工复制方法或改 SQL：

1. 回退本交接文档提交；
2. `6c2914a83` — Planning Repository 职责拆分与结构/并发测试；
3. `eafeee797` — publication protocol 测试迁移头更新。

回退 P1 后必须重跑 `test_planning_repository_split.py`、`test_planning_routes.py`、第一期 146 项相关回归与 backend 全量。回退 P0 后四项 publication 测试会重新因陈旧 revision 在目标断言前 fail closed；不得为保留旧夹具而放宽运行时 guard。

由于本期没有部署、上传或推送，不需要线上回滚、数据恢复或客户端回退。
