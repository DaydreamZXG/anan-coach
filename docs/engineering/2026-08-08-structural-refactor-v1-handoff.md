# 岸岸项目第一期结构性重构交接

> 状态：第一期完成；代码、自动化、三设备模拟器与官方主包门禁证据齐全
> 分支：`codex/structural-refactor-v1`
> 实现基线：`10993e679be7731f684103ba144587d910b7442e`
> 最终源码检查点：`52568db3d8cf231e09eb3f3dedbfd088deb12a23`
> 最终治理单元：`RU-20260809-structural-refactor-v1-static-gate-closure`
> 冻结哈希：`sha256:c4bb00396d6c059354b986dc1d43ff8f7c824105bd1eee3c4dd82ac0f455ce57`

## 结论

第一期完成了三个既定边界的结构收拢，没有修改用户可见 UI、题目数据、公开 API、数据库既有语义、支付、对象存储、线上鉴权、模型供应商配置或申论双模型评审配置；没有迁移或回填线上数据。全程未部署后端、未 preview/upload 小程序、未推送远端。

- 行测作答会话由行测域写入，计划完成事实由 planning 写入；调用方向是行测编排到 planning 的单向事务调用，planning 源码不再导入 `xingce_learning`。
- 微信四类做题场景的共享实现有且只有 `apps/wechat/subpackage-shared-src/**` 这一中立人工源；业务分包副本只由脚本生成并失败关闭校验。
- Companion 的上下文、短期历史装配、模型回复和确定性动作边界已拆开；已经解析但从未进入在线执行的旧 requestedActions/next_actions 链已删除。

## 新边界

### 1. 行测完成状态机

| 事实 | 唯一所有者 | 写入时点与约束 |
|---|---|---|
| 作答会话 `active/submitted/settled`、revision、提交回执 | `xingce_learning/real_attempt` | 行测服务保留既有幂等键、CAS、同键异载荷拒绝和事务边界。 |
| 题量合同与选题查询 | `task_runtime/xingce_attempt` | `contracts.py` 与 `question_repository.py` 是中立实现；旧 `xingce_learning.contracts` 只做兼容 re-export。 |
| 计划执行 `in_progress/complete/abandoned` 与 late evidence | `planning/xingce_completion.py` | 新会话提交事务内产生 canonical terminal fact；行测只消费返回结果，不复制 planning 状态。 |
| 错因前证据 `pending_reflection → settled` | `xingce_learning/real_attempt/evidence.py` | 结算必须引用 canonical planning execution；重复错因、重放和 stale revision 不追加重复事实。 |
| 历史会话补偿 | `xingce_learning/real_attempt/completion.py` | 只有私有 scoring bundle 缺少 `sessionGeneration` 键时可进入；未知、null、非字符串或损坏 marker 统一用既有 `xingce_planning_conflict`/HTTP 409 失败关闭并回滚。 |

新会话标记为 `xingce-plan-completion-at-submit@v1`。已标记会话如果缺少 canonical terminal planning fact，不再回退历史补偿；历史无标记会话仍保留原完成、late-evidence、回放和 receipt 兼容。

### 2. 微信做题运行时

- 中立源：`apps/wechat/subpackage-shared-src/**`，共 123 个 manifest source，位于 `miniprogramRoot` 外，不进入主包。
- 生成结果：269 个唯一 target，其中 146 个跨包副本；practice、mock-exam、reflection、essay-task 不再充当人工母版。
- `sync-subpackage-shared.mjs --check` 同时拒绝重复 logical path/source/package/destination、中立源孤儿、四个 generated-owned 目录孤儿和缺少 package dependency closure；依赖解析不再静默回退到任意业务包。
- 中立运行时统一 annotation anchor/strokes、question/material surface、swipe navigation、content renderer、material reader 和 task shell；reflection 由单一 replay clock 负责 play/pause/seek/erase/surface/unload。
- 页面只保留场景编排。草稿、题面笔迹、材料笔迹、action events、reader state 与 scroll position 沿用既有 checkpoint/API 字段和存储键。

关键行为证据已经实际驱动页面或组件方法，而非仅以设备标签或源码字符串代替：同页失败 PUT 复用原 Idempotency-Key；页面重建保留 command/CAS/evidence 并按既有租约取得新 resolved key；mock 三种 viewport 驱动横向表格与纵向材料滚动测量；reflection 卸载后 timer 不再写状态。

### 3. Companion 与项目 AI/Agent 链路

Companion 当前边界：

- `companion/context.py`：上下文快照、授权检索与 retrieval bundle；
- `companion/provider_context.py`：不可变短期历史规范化与 provider 请求装配；
- `companion/model_reply.py`：模型调用与 sourceRefs grounding；
- `companion/service.py`：命令、配额、模型账本、审计、outbox 和确定性 allowedActions 编排；
- iLink 的短期记忆读写仍由 `ilink_channel/short_term_memory.py` 与 `hub_bridge.py` 拥有，保持读取/写入 fail-open、账户隔离、TTL，以及只有发送成功后才写入的时序。本期没有修改 iLink 源码。

项目 AI/Agent 盘点：

| 链路 | 当前分类 | 本期处理 |
|---|---|---|
| 小程序 Companion | 在线 | 保留，使用拆分后的 CompanionService。 |
| 微信 iLink Bot | 在线 | 保留，共用 CompanionService；通道与记忆写入时序不变。 |
| learning_brain / 中控大脑 | 在线候选编排 | 保留；与 cause_agent 的相似 JSON/provider 编排记为二期债务。 |
| 行测 cause_agent | 在线 worker | 保留；checkpoint/actionTrace/scratch evidence 集成测试通过。 |
| 申论 review worker | 在线多模型 worker | 保留；模型和双模型评审合同未改。 |
| coach notifications | 在线确定性 worker，非模型 Agent | 保留。 |
| 外部资料识别、回答与 private knowledge | 在线 worker/provider 管线 | 保留。 |
| Companion requestedActions/next_actions | 已证明死链 | 删除 `CompanionActionRequest`、Companion 专用 `BoundedToolRuntime`、`execute_companion_actions` 和 `requests=()` 包装。 |
| 岗位决策 `nextActions` | 在线公开投影 | 与 Companion 死链不同，明确保留。 |

## 验证结果

### 治理与范围

- 最终治理执行报告：`.product-governance/execution/reports/3ad7e70f7d4ea6a9.8100b1075f098a22d06f.json`，`passed=true`；2 个完整 Vitest selector、lint、typecheck 全部通过。
- 最终 freeze 验证：0 errors、0 warnings。
- `git diff --check` 通过；相对实现基线的改动只属于三个工作包、对应测试、生成副本和本交接，没有用户既有未跟踪文件被覆盖、删除或提交。

### 后端

- 最终相关联合回归：146 passed、0 failed、1 个既有 Starlette 弃用警告，574.74 秒。见 `audit/structural-refactor-v1/third-wave/backend-final-targeted.json`。
- 覆盖新/历史会话、未知 marker 回滚、重复提交、同键断网重试、同键异载荷、CAS stale、重复错因、planning import guard、Cause evidence、targeted selector、普通 Companion 对话、短期记忆、超时/模型失败/无效动作合同。
- 第三波全量在中间提交上为 1738 passed、5 failed。4 项是零产品改动基线已存在的 publication protocol 测试；第 5 项是迁移后测试仍 monkeypatch 旧 repository，现已改到真正的中立所有者并在最终联合回归通过。没有冒充“最终全量已重跑”。

既有 4 项失败：

1. `test_shared_checkout_blocks_exclusive_until_existing_work_drains`
2. `test_runtime_guard_binds_application_commit_and_environment`
3. `test_companion_checkpoint_inherits_the_production_publication_guard`
4. `test_companion_checkpoint_inherits_timeouts_and_releases_on_row_lock`

它们都位于 `backend/tests/test_commercial_data_publication_protocol.py`，属于既有发布协议门禁与当前迁移头/请求集合不一致，不在本期三个域的实现范围。

### 微信自动化与官方工具

- 最终 `npm run check` 全通过：格式、typecheck、lint、tokens、交互、控件、设计源、导航、结构、视觉覆盖、分包、OpenAPI、21 项 Node、130 个文件/907 项 Vitest、9 项 Cloud runtime。
- 分包结果：15 个分包；资源 188390/204800 bytes；主包内 subpackage-only 文件 0；跨分包运行时 import 0；269 个生成 target/146 个跨包副本一致。
- 官方开发者工具 2.02.2608062 / SDK 3.17.0 在最终源码检查点完成 phone、iPad portrait、iPad landscape 复签：每种设备 4/4 关键路径通过、`runtimeFailures=[]`。Code Quality 在 2026-08-09 01:11 重新扫描，`PACKAGE_SIZE_LIMIT` 显示“主包不含插件 <1.5M / 已通过”。详见 `audit/structural-refactor-v1/third-wave/summary.json`。面板不公开精确 compiled bytes；依赖分析的 1626.3KB 是中间提交上含 type-only 未使用源码的 raw dependency metric，不能代替 compiledPkg 结论。

## 回滚

本期没有数据库迁移或线上数据写入，回滚只需在本地按依赖逆序 `git revert`，不要直接删除中立源或手工修改生成副本：

1. `52568db3d` — 最终测试静态门禁；
2. `f2de81c3a` — 页面级/后端 evidence 验收闭包；
3. `5fdc94aec` — 未知会话代际失败关闭与生成孤儿/冲突校验；
4. `660546761` — 行测题量合同兼容 re-export；
5. `67c5337e7` — 微信中立源与运行时；
6. `25705ee56` — 行测状态所有权；
7. `a329b85fb` — Companion 边界与死动作清理。

回退微信检查点后必须运行 `npm run subpackage-shared:sync` 和 `npm run package:validate`；回退行测检查点后必须重跑三项状态机 selector。由于未部署、未上传、未推送，不需要线上数据回滚。

## 剩余债务与二期工作包

1. **历史 adapter 移除前取证**：只读统计生产 `scoring_bundle.sessionGeneration` 的缺失/合法/未知分布；禁止记录题目或用户内容。停止条件：发现未知 marker、无可审计统计权限或需要生产写入。只有缺失会话自然耗尽并经迁移决策后才能删除 adapter。
2. **Planning repository 细分**：把候选选择、执行事实和查询投影进一步拆仓；先冻结 SQL/result 契约和事务测试。停止条件：需要改表义、公开 API 或与支付/data_rights 共改共享文件。
3. **AI 编排复用评估**：仅评估 learning_brain 与 cause_agent 的 JSON 校验/provider 调用公共底座；不得合并各自事实、账本、配额或 worker 状态机，也不得触碰仍在线的岗位 `nextActions`。
4. **既有 publication protocol 门禁**：独立修复 4 个基线失败并重跑后端全量；不得把它们归因于本次结构重构或顺手改运行时发布守卫。
5. **支付、data_rights 与其他 P2**：保持冻结，另建依赖闭合工作包；若需要共享 migration、线上权限或外部协调，先停止并取得用户决策。

以上二期项均未留下半重构代码。后续开发者可从本文件、最终治理报告和第三波审计目录继续，不需要依赖本次对话上下文。
