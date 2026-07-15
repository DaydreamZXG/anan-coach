# 交接文档：UI 重构完成情况 + codex 后端任务书（fable → codex）

- 日期：2026-07-03
- 作者：fable（UI 重构已完成并编译通过）
- 读者：codex（负责后端修复任务）
- 关联文档：
  - `fable-ui-diagnosis.md`（UI 诊断与设计方案，已实现）
  - `fable-backend-audit.md`（后端审计，P0-P3 分级，本任务书的依据）

---

## 第一部分：fable 已完成的 UI 重构（勿动区域）

### 改动清单

| 文件 | 操作 | 内容 |
|---|---|---|
| `apps/ios/Gongkao/Gongkao/CoachComponents.swift` | 新增 | 共享设计组件：`DaziPalette`（设计令牌）、`GoalStripView`（目标条）、`CoachBriefCard`（搭子主判断强卡片）、`TrackTimelineBar`（双轨比例时间条）、`TaskPathView`/`TaskStepNode`（垂直步骤流，支持 compact 变体）、`PrimaryCTABar`（主 CTA） |
| `apps/ios/Gongkao/Gongkao/TaskExecutionView.swift` | 新增 | 全屏任务执行页：`PracticeExecutionView`（行测带练，错因优先、长解析折叠于 `CoachFeedbackCard`）、`EssayExecutionView`（申论四步：作答→点评→重写→对比，带步骤条） |
| `apps/ios/Gongkao/Gongkao/FeatureViews.swift` | 重构 | TodayView 改为教练驾驶舱（目标条+主判断卡+双轨条+步骤流+底部悬浮 CTA+navigationDestination 推入执行页）；删除 TodayHero、TrackMixPanel、TodayTaskList、TaskRow、PracticeExecutionPanel、PracticeFeedbackView、EssayExecutionPanel；工作台 SourceBasePanel 指标砖降级到对象列表之后 |
| `apps/ios/Gongkao/Gongkao/StudyDeskView.swift` | 重写 | iPad 三栏职责分离：左栏（目标条+今日路径 compact 步骤流+学习对象库）、中栏（纯执行画布，任务态渲染 TaskExecutionView，空闲态渲染主判断卡+CTA）、右栏 `DeskCoachPane`（数据驱动：搭子判断/来源/下一步/配额，全部读真实 AppState 字段，占位文案已删）；竖屏两栏 + "搭子"浮钮唤出 sheet |
| `apps/ios/Gongkao/Gongkao/AppModels.swift` | 小改 | `BackendTodayTask` 增加 `Hashable`；AppState 增加 `activeTask`、`completedTaskIDs`、`isLastQuestion`、`openTask(_:)`、`completeActiveTask()`（纯 UI 导航状态，无契约变更） |
| `apps/ios/Gongkao/Gongkao.xcodeproj/project.pbxproj` | 小改 | 注册两个新 Swift 文件（该工程用显式文件引用，**新增 Swift 文件必须手工注册 pbxproj 四处**，切记） |

### 验证状态

- `xcodebuild -scheme Gongkao -destination 'generic/platform=iOS Simulator' build`：**BUILD SUCCEEDED**（Xcode 26.3）
- 后端未做任何改动，pytest 无需重跑
- 未跑 `scripts/full_verify.sh` 全量（其最后一行依赖机器私有路径 `/Users/zxg/.codex/...`，见审计 P2-7）
- 待办：iPhone 17 / iPad Pro 13-inch 模拟器截图核对（建议人工过一遍五个界面状态）

### codex 注意：UI 侧的约定（不要破坏）

1. **UI 契约未变**：新 UI 消费的仍是现有 API 字段（headline/rationale/reason/sourceGrounding/nextAction/quota 等）。后端修复时**保持这些字段名和结构不变**，只把内容从硬编码换成真实数据。
2. **不要把执行面板加回首页**。任务执行只存在于全屏推入页（iPhone）和中栏画布（iPad）。
3. **措辞纪律**：taskstream 仍是硬编码剧本（P1-1），UI 上刻意使用了中性文案"搭子的安排"而非"AI 为你分析"。在 taskstream 真个性化之前，**不要在任何文案里宣称"AI 分析了你的数据"**。
4. `completedTaskIDs` 目前是客户端内存态，重启即失。这是已知的临时方案，等 P1-2（答题落库）完成后应改为服务端下发任务完成状态——见下方任务 T2 的第 4 条。

---

## 第二部分：codex 任务书（按顺序执行）

> 通用纪律（每条任务都适用，违反即返工）：
> - **禁止任何硬编码响应、假数据兜底、fake fallback**。数据不存在就返回空/404/加载失败，不准编造。
> - **禁止用"改测试"来让假数据通过**。测试必须断言真实数据库行为（查库断言行数/字段值），不准把 mock 固化成契约。
> - **不改 API 已有字段名**（camelCase alias 契约已被 iOS 消费）。只能新增字段，不能改名/删除。
> - 每个任务完成后运行 `cd backend && pytest`，并在工作目录写一份不超过 20 行的完成说明。

### T1（P0-2）：删除 workbench/preview 假数据

**位置**：`backend/app/api/routes.py:81-159`、`backend/tests/test_api.py:38-53`

**要求**：
1. `/api/v1/workbench/preview` 不得再返回任何硬编码学习对象。首期最小实现：从 `learner.essay_attempts` 生成真实的"申论复盘"对象（每条 attempt 一个对象，含真实日期与任务标题）；行测对象等 T2 落库后再加。
2. 无数据时返回空 `sections` 数组，前端已有空态处理逻辑可接。
3. `bootstrap` 的 `questionBank` 五个统计数字改为查 `product.*` 真实 count（可加 5 分钟内存缓存）。
4. **验收**：测试必须先插入 learner 数据再断言对象出现；断言空库时 sections 为空；grep 整个 `backend/app/` 无 "资料分析今日测验"、"55214" 等硬编码字面量。

### T2（P1-2）：行测答题落 learner.*

**位置**：`backend/app/domains/practice/service.py`

**要求**：
1. 新建表 `learner.practice_attempts`（learner_key、question_id、task_id、selected_answer、correct、answered_at；建表语句加入 `scripts/server_setup_learning_ai_tables.sh` 并同步文档）。
2. `submit_answer` 判分后写入该表，带 learner_key（practice 路由需要加 `X-Learner-Id` 依赖，复用 essay 的 `require_learner_key`）。
3. 写库失败要抛错（500），**不准静默吞掉**——落库就是这个功能的意义。
4. 新增 `GET /api/v1/taskstream/today` 响应字段 `completedTaskIds: [String]`（新增字段，允许）：根据当日 practice_attempts / essay_attempts 计算。iOS 侧 fable 已留 `completedTaskIDs`，之后会接。
5. **验收**：测试用真实 Postgres（或 testcontainers）插入并查库断言行数与字段；断言缺 learner key 时 401。

### T3（P1-4 + P1-3）：配额边界修复

**位置**：`backend/app/domains/ai/audit.py`、`backend/app/domains/essay/service.py`

**要求**：
1. `reserve_quota` 的 INSERT 分支必须检查 `limit_count >= 1` 才允许首次占用；limit=0 时任何调用都返回配额不足。
2. AI 调用失败（`AIProviderConfigurationError` / `AIProviderResponseError` / 超时）时返还本次配额（`used_count - 1`，不低于 0），interaction_logs 照记 failure。
3. **验收**：为 `reserve_quota` 写真实数据库测试覆盖：并发扣减不超限、limit=0 首次调用被拒、失败返还后可再次调用、跨日重置。这段 SQL 是全系统商业上最关键的代码，测试缺失即不通过。

### T4（P1-7 顺带）：README 与路由清单对齐（P3-1）

小任务：更新 `backend/README.md` 路由清单为实际路由。

### 明确不做（等待人工决策，不要自作主张）

- **P0-1 身份体系（Apple 登录）**：涉及 iOS + 后端双侧契约，需要先出技术方案评审，不在本任务书内。
- **P1-1 taskstream 真 planner**：依赖 T2 的数据积累，且属于决策逻辑设计，由 fable 出规格后再排期。
- **P2-1 AI 上游迁移**：商业决策，不动。
- 任何 UI / SwiftUI 改动：不属于 codex 范围。

---

## 第三部分：环境备忘

- iOS 工程：`apps/ios/Gongkao/Gongkao.xcodeproj`，Swift 6，deployment target iOS 17，pbxproj 为显式文件引用（新增文件要注册四处：PBXBuildFile、PBXFileReference、PBXGroup children、Sources phase）。
- 编译验证命令：`cd apps/ios/Gongkao && xcodebuild -project Gongkao.xcodeproj -scheme Gongkao -destination 'generic/platform=iOS Simulator' build`
- 后端测试：`cd backend && pytest`
- `scripts/full_verify.sh` 最后一行依赖机器私有路径，在非 zxg 机器上会失败（修复属 P2-7，可顺手做：将该行改为存在性检查后跳过）。
