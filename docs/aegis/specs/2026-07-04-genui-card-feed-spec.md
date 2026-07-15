# 规格：GenUI 卡片流（意图驱动首页）

- 日期：2026-07-04
- 作者：fable（规格）；决策：产品负责人
- 执行者：codex（服务端编排 + 契约）、fable（iOS 词表渲染器）
- 关联：onboarding 规格（planner R0 时间预算、第 10 节模考复盘、第 11 节能力校准）

## 1. 架构决策

采用 GenUI 第二档："意图驱动 + 受控组件词表"（Server-Driven UI 的 AI 加强版）。

- UI 组件全部原生预制；AI/规则引擎决定的是"此刻给用户看什么、按什么顺序、说什么话"，不是"长什么样"；
- 服务端返回结构化卡片序列（JSON），客户端按封闭词表渲染；
- 明确排除第一档（LLM 现场生成界面代码）：延迟、成本、UI 质量、App Store 动态下发审核风险，四项皆不可接受。

## 2. 三条纪律（防玩具化）

1. 词表封闭：卡片 type 为枚举，服务端不得下发词表外类型；客户端遇未知类型渲染降级卡（标题+文案+可选跳转），禁止崩溃。这同时是未来平滑加卡的机制：旧客户端见新卡自动降级。
2. 执行页不生成：做题、模考、申论作答等沉浸态保持固定布局。GenUI 用于"决定你去哪"，不用于"你正在做的事"。
3. 可解释：每张卡必须带 reason（为什么此刻出现）与数据来源标注，沿用全项目诚实纪律；planBasis=rules_v1 时不得自称智能分析。

## 3. 端点与契约

新端点 GET /api/v1/feed/today（替代今日页对旧 today 端点的直接布局映射）：

```json
{
  "generatedAt": "…",
  "planBasis": "rules_v1",
  "cards": [
    {
      "cardId": "…",
      "type": "goal_gap",
      "priority": 100,
      "reason": "距考试 89 天，你与目标差 9.5 分",
      "dataLine": "基于 1 次摸底 + 214 题练习 + 2 次模考",
      "payload": { }
    }
  ]
}
```

- cards 按 priority 降序即渲染顺序；服务端负责排序，客户端不重排；
- payload 结构因 type 而异，各 type 的 payload schema 在服务端 schemas 中逐一定义并出现在测试断言中；
- reason 必填；dataLine 在涉及能力估计时必填（对齐第 11 节校准规格）。

## 4. 卡片词表 v1（封闭枚举，14 种）

| type | 用途 | 主要 payload |
|---|---|---|
| goal_gap | 目标条：岗位/倒计时/分差 | targetScore, estimatedScore, daysLeft, positionName |
| coach_brief | 搭子今日主判断（每 feed 至多 1 张） | text, planBasis |
| task_practice | 行测练习任务 | taskId, moduleName, questionCount, estMinutes |
| task_essay | 申论任务 | taskId, questionType, estMinutes |
| task_method_card | 方法卡学习任务 | taskId, cardId, cardTitle |
| mock_exam_due | 本周模考待做 | sessionSpec, estMinutes |
| mock_review_pending | 模考复盘未查看（高优先级挂起） | sessionId, estimatedScore |
| wrong_cluster | 错题聚类提醒 | nodeId, displayPath, wrongCount |
| streak_restart | 中断重启（连续 N 天未学） | daysIdle, miniTaskId |
| calibration_note | 能力估计更新通知 | statusLabel, dataLine, delta |
| quota_notice | AI 配额状态 | remaining, resetAt |
| companion_prompt | 问搭子引导（场景化入口） | suggestedQuestion, contextObjectId |
| external_capture_nudge | 外部错题录入引导 | text |
| fallback | 未知类型降级壳（仅客户端使用） | title, body, deeplink? |

词表变更流程：新增卡型必须先改本规格，再动代码；服务端与客户端同一 PR 内对齐。

## 5. 编排规则（服务端，规则引擎起步）

- 编排器输入：learner profile、时间预算（R0）、能力校准状态、模考/复盘状态、连续学习天数、quota；
- 首屏优先级基线：mock_review_pending > streak_restart > coach_brief > goal_gap > 任务卡（按 planner 排程）> 其余；
- R0 约束沿用：任务卡合计时长不超 daily_minutes；
- coach_brief 文案可走 LLM 生成但必须缓存（同状态当日不重复生成），其余卡文案模板化——成本纪律：feed 生成不得产生按屏计的大模型调用；
- 编排决策本身 v1 全部走规则（planBasis=rules_v1 如实标注），LLM 参与编排另立规格再议。

## 6. 协作方式：契约先行，三阶段

阶段一（并行，无相互依赖）：

- codex：按第 3、4 节实现 /api/v1/feed/today 的 schemas + 编排器骨架，先只产出已有数据支撑的卡型（goal_gap / coach_brief / task_* / mock_review_pending / calibration_note），每种卡的 payload schema 配测试；
- fable：iOS 侧建 FeedCard 模型 + 词表渲染器（按 type 分发到预制组件）+ fallback 降级卡，先用本地夹具 JSON 驱动开发，不等服务端。

阶段二（对接）：

- codex 端点就绪后，fable 把今日页切到 feed 渲染器；旧 today 端点保留一个版本周期作回退开关；
- 联调基准：同一账号，feed 的任务卡集合必须与旧 today 端点任务一致（编排只改呈现层，不改 planner 排程）。

阶段三（扩词表）：

- streak_restart / wrong_cluster / companion_prompt / quota_notice / external_capture_nudge 随各自后端能力就绪逐个上线；
- 每加一种卡：先改本规格词表 → codex 加 schema+编排规则+测试 → fable 加渲染组件。

分歧仲裁：契约字段争议以本规格为准，规格没写的先补规格再写码——两个 agent 各自猜契约是协作最大的翻车源。

## 7. 验收标准

- codex：每种卡型有 payload schema 测试；编排器测试覆盖"模考复盘未看置顶""任务合计不超时间预算""无数据时 reason 诚实降级"三个场景；禁止硬编码卡序列；
- fable：本地夹具含全部 14 种卡 + 1 种词表外类型，渲染器对未知类型必须出 fallback 卡不崩溃；VoiceOver 可逐卡朗读 reason；
- 联调：真实账号 feed 与旧 today 任务集合一致性抽查（查库对比，不看接口自说自话）。
