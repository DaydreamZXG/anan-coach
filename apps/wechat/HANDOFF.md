# 前端重建交接文档（v0/frontend-rebuild 分支）

> 更新于 2026-07-23。上一任 AI（v0）在此分支上推倒重建了微信小程序前端。
> 新会话请先通读本文档，再看「待办」一节继续开发。

## 一、产品定位（必读，决定一切设计取舍）

这不是题库 App，是 **AI 全托管的私人备考教练**：
- 用户观察：考公人喜欢"大而全"（心理安慰），但规划普遍不合理（自欺）。
- 策略：**卖"大而全"（货架层可见），用"全托管"（动线只有一条）**。
- 核心闭环（唯一验收标准）：新用户 onboarding → 今日任务流 → 练习 → 每日结算 不卡壳。
- 岸岸（AI 伴学）要**敢于反对用户**并用数据验证（后端有 highImpactDecision 机制）。
- 社区共建：知识卡是唯一 UGC 单元，结构化、可被 AI 引用、可被计划引擎排进任务流。

## 二、分支情况

| 分支 | 说明 |
|---|---|
| `v0/frontend-rebuild` | **当前工作分支**，全新前端（未提交，全部在工作区） |
| `codex/kimi-k3-ui-redesign` | K3 的半成品前端（已废弃，但 api 契约层来自这里） |
| `backup/wechat-frontend-pre-redesign` | 旧版完整前端（44 页+岸岸6子包+e2e），要找旧实现来这里挖 |

## 三、设计系统（app.wxss）

- **冷静工具层 + 暖色岸岸**：暖色 `--warm: #e8833a` **只允许**出现在岸岸相关元素上（"暖色出现=有人在陪你"）。
- 工具层主色 `--primary: #2b6cb0`（冷静蓝），背景 `--bg: #f5f6f8`。
- 全部用 CSS 变量（--ink/--ink-2/--ink-3/--line/--good/--danger 等），禁止硬编码颜色。
- 通用类：`.page .card .h1 .h2 .text-2 .text-3 .num .btn .btn-primary .btn-ghost .btn-warm`。
- 自定义 TabBar（custom-tab-bar/），4 Tab：今日/能力/岸岸/我的，岸岸 Tab 激活时用暖色。
- 每个页面 `navigationStyle: custom`，页面顶部放 `.statusbar` 占位。
- Tab 页 onShow 必须调 `this.getTabBar().setData({ active: "xxx" })`。

## 四、已建页面（pages/）

| 页面 | 状态 | 说明 |
|---|---|---|
| today/ | ✅ | 任务流（单列时间流+序号轨道）、目标/分差条、岸岸"今天为什么这么排"暖色卡、结算入口。数据源 `fetchTodayTaskstream()` |
| ability/ | ✅ | 总估分+置信度、能力地形图（**不确定带**：样本量反推半宽 `60/sqrt(n)` 上限22，诚实表达）、知识卡广场入口、报告下钻列表 |
| anan/ | ✅ | 暖色对话界面、4个预设按钮、高影响建议横幅、allowedActions chips（点击=发送 label 继续对话）。数据源 `askCompanion()` |
| me/ | ✅ | 货架层：备考信息/我的知识卡/设置 分组菜单（多为占位跳转） |
| practice/ | ✅ | 计时、单选、进度条、交卷确认 → redirectTo 结算页。`startTaskSession/submitTaskSession`（乐观锁 revision） |
| daily-settlement/ | ✅ | 完成度环+统计+岸岸按完成度分档点评（当前只消费 URL 参数，未接结算后端接口） |
| method-cards/ | ✅ | 广场：赛道切换、保守分、"N人验证"、样本少警示、展开步骤/坑、收藏。`discoverMethodCards()` |
| method-cards/publish | ✅ | 大白话输入→`createMethodCardDraft()`（AI 后端负责结构化） |
| onboarding/ | ✅ | 四步：备考经历(**二战**)→资格身份→学习节奏→薄弱自评。完整走后端状态机（见下） |

所有页面都有 **demo 数据兜底**（后端不可达时显示演示数据+"演示数据"角标），保证可预览。

## 五、API 契约要点（api/endpoints.ts + api/generated/types.gen.ts）

- types.gen.ts 是 openapi 生成的，**3万+行，tsc 全量检查在部分环境会超时**。改代码后建议本地跑 `npm run typecheck`。
- 封装模式：`authedRequest`（v1）/ `authedV2`（v2，需 idempotencyKey 的用 `idemKey()`）。
- 上一任新增的封装：`askCompanion`、`discoverMethodCards`、`createMethodCardDraft`。
- **踩过的坑（重要）**：
  - `CompanionHighImpactDecision` 没有展示文案字段（只有 decisionType/status/evidenceRefs/prohibitedMutations），劝说文案在 `answer`，选项在 `allowedActions`（字段是 `kind/label/requiresConfirmation`，不是 actionType/payload）。
  - `GetAbilityEstimateApiV1CalibrationAbilityGetResponse` = `AbilityEstimate` 的别名。
  - tsconfig 是严格模式（noUncheckedIndexedAccess/exactOptionalPropertyTypes），数组索引访问要判 undefined。

## 六、Onboarding 状态机（已完整接通）

提交顺序（每步前 `fetchOnboardingProgress()` 拿乐观锁版本号）：
1. `putPreparationExperience`（retake/priorGuokaoExam/priorExamYears/firstSystematic/externalLearning）← **二战在这里**
2. `updateOnboardingProfile` + `completeQualificationMilestone`
3. `putStudyPreference`（weekday/weekend/longBlock/fragmentMinutes + fixedRestDay + mockExamDay + timezone）
4. `acknowledgeOnboarding(progressVersion)` → 若返回 `nextAction === "start_retake_recovery"` 提示二战恢复

app.ts onLaunch 里有首跑检测：`fetchOnboardingProgress().completed === false` → navigateTo onboarding。

## 七、待办（按优先级）

1. **本地验证**：微信开发者工具跑通 onboarding→今日→练习→结算 闭环；跑 `npm run typecheck`（上一任受工具时长限制未能全量跑完，关键类型已人工核对）。
2. **结算页接真实接口**：目前只消费 URL 参数，后端有 settlement 相关接口待接。
3. **复盘页**（今日流里 review 任务跳 `/pages/review/index`，页面未建）。
4. **申论链路**（essay 任务跳 `/pages/essay-material/index`，未建；旧版有6页可参考备份分支）。
5. **模考页**（能力页 CTA 跳 `/pages/mock-exam/index`，未建）。
6. **onboarding 可选后置**（用户未拍板）：岗位选择（position）、摸底测试（placement_test）——旧版有这两页，建议做成结算页/我的页里的引导入口而非强制步骤。
7. **我的页占位路由**：goal/study-preference/plan-history/notification/about 均未建。
8. **知识卡收藏落库**：广场"收藏"目前只是 toast，后端有 trials/feedback 接口可接。
9. 岸岸 `allowedActions` 的 `adjust_plan_preview` 等 kind 目前统一"点击=发送label"，后续可按 kind 分发真实动作。

## 八、工程注意事项

- 新页面**必须**同步注册进 `miniprogram/app.json` 的 pages，否则 navigateTo 静默失败（所有跳转都带了 fail toast 兜底）。
- 文案全中文、口语化、岸岸口吻（"今天就别硬扛了"），不要教务腔。
- 上一任用的是 Loca MCP 工具操作文件；apply_patch 对"删除+新增同名文件"和超大 patch 会失败，需拆分。
- 仓库根目录 reports/ 下有两个未跟踪的设计预览 html（calm-tech / gufeng-warm），已选定方向为"冷静工具+暖色岸岸"，可留作参考或删除。
