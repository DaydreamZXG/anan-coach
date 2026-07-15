# 官场生存刷题游戏：产品与技术架构计划

日期：2026-07-07  
状态：设计计划，待用户确认后进入实施计划  
适用范围：iOS App、FastAPI 后端、题库/申论/AI/官阶/首页编排  

## 0. 结论：不全仓重做，但产品内核必须重做

本项目不建议从空仓重做。已有资产是真壁垒：5 万级行测真题、申论题、岗位与进面分、知识点地图、方法卡、AI 审计、认证、iOS 工程、后端部署都值得保留。

但也不能继续在现有 today/feed/rank/AppState 上打补丁。正确策略是：

- 保留资产层、数据层、基础服务层。
- 重做产品体验层和编排层。
- 退役旧首页直连、固定任务列表、XP 作为主引擎、分差英雄指标等旧产品方向。
- 用新的“动态政务卷宗”作为唯一首页与执行入口。

一句话：**保留发动机，重做驾驶舱和游戏规则。**

## 1. 新产品定义

这是一个基于真实公考题库的冷酷官场生存刷题游戏。

用户不是在“刷题”，而是在“披阅奏折、治理一方、升迁或失势”。所有游戏行为都必须落到真实学习动作：做真题、复盘错题、攻克弱项、写申论、理解方法。

### 1.1 北极星

让考生愿意每天回来“开衙”，并在真实题目训练中获得强情绪反馈。

### 1.2 不再采用的旧定位

- 不再把产品定义为普通“游戏化坚持工具”。
- 不再把“分差”放在首页唯一英雄位。
- 不再把 XP 当主叙事。
- 不再把 today task list 当产品首页。
- 不再做普通题库菜单。

### 1.3 新主叙事

- 5 万行测真题 = 日常政务。
- 2 千申论真题 = 突发国难 / Boss 战。
- AI = 幕僚，不是客服。
- 官阶 = 权力、界面质感、信息视野、AI 权限。
- 错题率、断更、弱项逃避 = 御史台、民心、辖区风险。

## 2. 用户吸引力设计

### 2.1 第一眼钩子

不要说“AI 公考提分”。头部机构已经占据这个叙事。

主钩子应是：

> 5 万道公考真题，做成了一款会骂题、会护短、会降你官的官场刷题游戏。

用户必须在 30 秒内看到差异：

1. 打开即“今日有何政务”，不先给菜单。
2. 免注册先批 3 封急件。
3. 第一题答完立刻盖印。
4. 第三题后授“九品县令”。
5. 第十题后生成可分享战报。

### 2.2 传播资产

产品内必须内置可截图事件，而不是事后靠运营硬编：

- 御史台暗访通知。
- 幕僚痛斥出题人。
- 怪题焚毁判决。
- 九品县令破防战报。
- 从粗纸竹简升到黑金军机处。
- 同榜进士榜中的官阶称谓。
- 申论 Boss 战“平灾奏折”评级。

每个事件都生成一张竖版分享图：题目来源、用户判决、幕僚评语、官阶变化、二维码/口令。

### 2.3 留存机制

日常留存靠“开衙”，不是打卡：

- 每日幕僚派发政务卷宗。
- 行测三类急件：钱粮、刑狱、民情。
- 申论 Boss：汛情、瘟疫、舆情、乱港等大事件。
- 连续高质量处理弱项，官声上升。
- 逃避弱项或长期错误，御史台介入。

### 2.4 付费吸引力

推荐订阅名：首席幕僚府。

可售卖：

- 无限幕僚献计。
- 申论 Boss 深度批阅。
- 上帝视角解析。
- 弱项案卷周报。
- 怪题审判与焚毁记录。
- 高阶 UI 主题与官阶仪仗。

不建议售卖“AI 替你答题保级”。它短期刺激强，但会伤害学习可信度和公平感。更稳的替代品：

- 复议令：御史台失败后给一次补考机会。
- 免参牌：保留一次 UI/官阶展示权，但不改变真实学习记录。
- 锦囊：解锁一次秒杀思路，不直接代答。

## 3. 第一原则审查

First-principles invariants:

- Non-negotiable goal：把真实公考训练包装成强情绪、可传播、可留存的官场生存体验。
- Non-negotiable constraints：题目与解析必须真实可追溯；AI 不得编造正确性；商业版不做隐藏兜底、不做半成品体验；架构必须有单一 owner。
- Historical assumptions to delete：固定题库菜单、普通 today list、分差首页独占、XP 刷量升官、旧客户端兼容优先。

Owner / retirement matrix:

- New canonical owner：`docket` 动态政务卷宗编排层。
- Old owner：`taskstream/today` 旧任务计划直接供 UI；`feed/today` 旧包装层；iOS `TodayView`/`StudyDeskView` 双轨首页。
- Compat-only carrier：无。当前 App 未上架，不需要为旧客户端保留主路径。
- Delete-first / retirement trigger：新 `docket/today` 和卡片执行链完成验收后，旧 today/feed UI 主路径删除。

Verdict：采用“保留资产、重做产品内核”的路径。

## 4. 反熵治理

Anti-Entropy Declaration:

- Deletion Class：code-retirement / contract-carrying code。
- Old Path/Object：旧 today 端点直连 UI、旧 feed 包装层、XP 主视觉、AppState 全局巨对象、iPhone/iPad 两套首页主逻辑。
- New Canonical Owner：`docket` 后端编排；iOS `DocketFeature`；`ProgressionService`。
- Expected Preserved Behavior：真实抽题、答题判分、错题记录、申论批阅、能力校准、认证、AI 审计。
- Expected Retired Behavior：旧任务列表作为首页、前端回退旧布局、XP 作为主要升迁逻辑、分差无数据时显示 0。
- External Boundary Touched：no，未上架无外部兼容责任。
- Source-of-Truth Data Risk：possible。数据库表不自动删除，只先停用旧路径。
- User Confirmation Required：yes，仅当后续涉及 DROP TABLE/DROP COLUMN 或真实数据迁移删除时需要单独确认。

Retirement Decision:

- Path：delete-first for internal code；confirmation-first for persistent data。
- Why：继续兼容旧首页会让“四不像”长期存在；但真实用户/题库/学习记录不可无确认删除。
- Non-edits：本计划阶段不删库、不迁移生产数据、不改线上服务。

## 5. 目标架构

### 5.1 后端分层

保留 FastAPI + PostgreSQL + SQLAlchemy。商业版补齐 Alembic 迁移、CI、测试数据库、结构化日志和环境分层。

建议领域边界：

| 领域 | 职责 | 处理方式 |
| --- | --- | --- |
| `knowledge` | 题库、知识点、抽题、真题溯源 | 保留，补强为题目资产 owner |
| `essay` | 申论题、批阅、重写 | 保留，改造成 Boss 战执行链 |
| `companion` | 幕僚人格、AI 献计、吐槽、护短 | 保留并强化，必须结构化输出 |
| `docket` | 动态政务卷宗：首页、卡片队列、今日派案 | 新 canonical owner |
| `review_session` | 行测卡片滑动执行、提交、结算 | 新建或从 `practice` 抽离 |
| `progression` | 官阶、政绩、民心、御史台、UI 权限 | 从 `rank_system` 重构而来 |
| `calibration` | 分差、能力估计、弱项识别 | 保留，降为考试锚点和编排输入 |
| `memory` | 学习者画像、历史、对象库 | 保留，收敛为学习记录 owner |
| `commerce` | 订阅、权益、消耗品、发票/订单 | 新建，后置 |

### 5.2 新核心 API

第一阶段只设一个首页主入口：

```http
POST /api/v1/dockets/open-court
```

语义：用户“开衙”。服务端根据官阶、弱项、近期表现、题库权重、御史台状态生成今日动态卷宗。该接口是 command endpoint：当天已开衙时返回现有活跃卷宗，未开衙时创建新卷宗。

返回：

```json
{
  "docketId": "2026-07-07:user",
  "rankSnapshot": {},
  "courtState": {},
  "cards": [
    {
      "cardId": "affair-1",
      "type": "daily_affair",
      "track": "xingce",
      "moduleName": "资料分析",
      "worldLabel": "核算钱粮",
      "questionId": "q_...",
      "title": "州府钱粮急件",
      "narrative": "某县上报税粮增减，请大人批阅。",
      "interaction": "swipe_verdict",
      "sourceGrounding": "product.questions"
    }
  ],
  "shareHooks": []
}
```

行测提交：

```http
POST /api/v1/review-sessions/{docketId}/cards/{cardId}/verdict
```

申论 Boss：

```http
POST /api/v1/boss-events/start
POST /api/v1/boss-events/{eventId}/submit
```

官阶与辖区：

```http
GET /api/v1/progression/profile
GET /api/v1/progression/court-state
POST /api/v1/progression/censorate/challenge
```

### 5.3 编排规则

`docket` 不能只是随机抽题。它需要一个可测试的规则引擎：

1. 必须优先派发弱项和近期错题相关题。
2. 已达到 85% 胜率的模块进入“恩准休沐”，不再默认派题。
3. 长期逃避弱项触发御史台预警。
4. 申论 Boss 每周至少一次，但不挤占日常行测卡片。
5. AI 只生成叙事、幕僚话术、解析风格，不决定题目正确性。
6. 每张卡必须有 `sourceGrounding`，能追溯到题库或学习记录。

### 5.4 iOS 架构

继续 SwiftUI 原生，不换跨端框架。理由：现有 iOS 工程已能跑；产品强依赖手势、动画、触感、系统分享、推送和 App Store 质感，原生更适合。

建议模块：

| 模块 | 职责 |
| --- | --- |
| `AuthFeature` | Apple 登录、会话 |
| `OnboardingFeature` | 初授官、目标与基线 |
| `DocketFeature` | 开衙首页、动态卷宗 |
| `ReviewCardsFeature` | Tinder-like 行测卡片、盖印、连击 |
| `BossEssayFeature` | 申论 Boss 战 |
| `CompanionFeature` | 幕僚问答、献计、吐槽 |
| `ProgressionFeature` | 官阶、民心、御史台、辖区 |
| `ShareFeature` | 战报图、分享链路 |
| `CommerceFeature` | 订阅与消耗品 |

状态层拆分：

- `SessionStore`
- `DocketStore`
- `ReviewSessionStore`
- `ProgressionStore`
- `CompanionStore`
- `EssayBossStore`
- `CommerceStore`

`AppState` 不再作为万能总线，只保留 app shell 级路由。

### 5.5 视觉与动效技术

视觉方向：Notion 式留白 + 冷金石印章 + 极简古风。不是满屏宫廷装饰。

技术建议：

- 主 UI：SwiftUI。
- 手势卡片：SwiftUI Gesture + matchedGeometryEffect。
- 印章/飞离/墨焰：第一版用 SwiftUI Canvas + 粒子/遮罩；高阶资产可引入 Rive 或 Lottie，但必须本地打包，不运行时生成 UI。
- 分享图：iOS 本地渲染 SwiftUI view 到 image，避免服务端生成图造成延迟。
- 触感：`UIImpactFeedbackGenerator` 和 `UINotificationFeedbackGenerator`。

## 6. 数据模型方向

新增或重构的核心表：

| 表 | 用途 |
| --- | --- |
| `learner.dockets` | 每次开衙生成的卷宗 |
| `learner.docket_cards` | 政务卡片队列与执行状态 |
| `learner.verdicts` | 用户对每张题卡的判决/答案 |
| `learner.court_states` | 辖区状态、民心、风险色调 |
| `learner.progression_events` | 官阶、政绩、御史台、特权变更事件 |
| `learner.share_artifacts` | 可分享战报事件 |
| `commerce.entitlements` | 订阅与权益 |
| `commerce.consumables` | 复议令/免参牌/锦囊 |

旧 `learner.official_ranks` 可以先作为 `progression` 的输入表保留，后续迁移到更完整的 progression schema。真实数据表删除必须另行确认。

## 7. 技术栈与工程治理

### 7.1 保留

- iOS：SwiftUI / iOS 17+ / 单 target。
- 后端：FastAPI / Python / SQLAlchemy / PostgreSQL。
- AI：OpenAI-compatible provider 层，继续保留 provider 抽象。
- 数据：product schema 题库与知识点地图。

### 7.2 必须补齐

- Alembic 迁移，不再靠散落 SQL 手工建表。
- 后端 CI：pytest + contract tests + minimal schema 同步。
- iOS CI：xcodebuild simulator build。
- API contract fixture：服务端 JSON fixture 驱动 iOS 解码测试。
- 结构化日志：docket generation、AI usage、answer verdict、progression event。
- 环境分层：local/staging/production，不再靠直接 rsync 成为唯一流程。
- Feature flags 只用于上线控制，不用于保留旧产品主路径。

### 7.3 AI 约束

- AI 幕僚必须接收结构化上下文，返回结构化结果。
- 题目正确性、答案、得分由确定性代码和题库决定。
- AI 可以改写解释、生成话术、总结套路、吐槽怪题，但不得编造题源、正确率、用户表现。
- “出题人审判”必须有依据：低正确率、争议标签、解析不一致、用户多次错误等。

## 8. 实施路线

### 阶段 A：权威重置与架构地基

目标：停止旧方向继续增长。

任务：

- 新建 `docket` 规格与 schema。
- 明确旧 `taskstream/feed` 的退役清单。
- 后端测试恢复绿色。
- AppState 拆分设计落地。
- 统一文档权威：旧轻量范围/轻坚持文件降为历史参考。

验收：

- 后端测试全绿。
- 新首页 owner 唯一。
- 没有“feed 失败回旧布局”的产品主路径。

### 阶段 B：开衙首页与 10 题政务卷宗

目标：用户打开即进入差异化体验。

任务：

- `POST /api/v1/dockets/open-court` 生成 10 张真题政务卡。
- iOS `DocketFeature` 首页。
- 免注册/轻登录前体验策略设计。
- 第一张卡在 30 秒内可答完。

验收：

- 真实题源可追溯。
- 无固定题库菜单。
- 首次体验 3 题内出现盖印反馈。

### 阶段 C：批阅卡片执行链

目标：把行测做题从普通表单变成“披阅奏折”。

任务：

- Tinder-like 卡片滑动。
- “准/驳”印章、飞离动效、连击墨焰。
- 答后幕僚短评。
- 错题入案卷。

验收：

- 一题一卡，手势稳定。
- 错题、正确率、时间、题源全部入库。
- 答对/答错文案不撒谎。

### 阶段 D：官阶与御史台

目标：让官阶成为权力系统，不是贴纸。

任务：

- 政绩/官声/民心/辖区风险模型。
- 官阶解锁 UI 质感与幕僚权限。
- 御史台预警、暗访、复议。
- 休眠旧 XP 主线。

验收：

- 官阶变化影响真实权限和 UI。
- 惩罚有预警、有补救、有学习依据。
- 不靠打卡天数作为唯一触发。

### 阶段 E：申论 Boss 战

目标：把 2000 申论真题变成高价值付费体验。

任务：

- Boss 事件模板：汛情、瘟疫、舆情、乱港等。
- 30 分钟平灾奏折。
- AI 深批、重写、朝野评价。
- 订阅权益接入。

验收：

- 写作链路完整。
- 批阅质量稳定，有结构化评分。
- Boss 战结束可生成分享战报。

### 阶段 F：吸引与商业化闭环

目标：让产品自带传播。

任务：

- 分享图系统。
- 同榜进士榜。
- 首席幕僚府订阅。
- 锦囊/复议令/免参牌。
- 新用户 3 题体验和邀请口令。

验收：

- 每日有可分享事件。
- 付费不破坏学习可信度。
- 用户能说出“这个不像粉笔”。

## 9. 风险与取舍

| 风险 | 判断 | 处理 |
| --- | --- | --- |
| 游戏过重抢学习注意力 | 可控 | 所有奖励必须绑定真实题目行为 |
| 惩罚机制引发反感 | 高风险但有记忆点 | 预警、复议、学习依据，不做羞辱 |
| AI 话术过火 | 中风险 | 人格 sharp，但事实必须克制 |
| 代码继续双轨 | 最高风险 | 新 owner 完成后 delete-first 退旧路径 |
| 付费道具像 pay-to-win | 高风险 | 不卖代答，卖解释/复议/权限 |
| 全仓重做拖垮进度 | 高风险 | 保留资产层，重做体验层 |

## 10. 产品决策

本节采用 Codex 推荐决策，原则是：强情绪、强传播、强记忆点，但不破坏真实学习可信度。

### 决策 1：首页 owner

采用 `POST /api/v1/dockets/open-court` 作为唯一首页主入口。

- `docket` 是新 canonical owner。
- `/taskstream/today` 和 `/feed/today` 退出客户端主路径。
- `taskstream` 中仍有价值的排程逻辑迁入 `docket` 或降为内部 helper。
- iOS 不再保留“feed 失败回旧 today 布局”的产品路径。

理由：这个 App 的第一秒必须是“开衙”，不是“加载任务列表”。保留旧入口会继续制造四不像。

### 决策 2：分差位置

分差正式降级为“军机密报 / 仕途档案 / 幕僚简报”指标，不再做首页英雄指标。

首页英雄位改为：

- 当前官阶。
- 今日政务卷宗。
- 辖区风险 / 御史台状态。
- 幕僚一句锋利判断。

理由：分差是理性目标，但不是这个产品的第一吸引力。用户被吸进来靠官场生存叙事，被留下来后再用分差做现实锚点。

### 决策 3：付费道具

不做“AI 替答题保级”。

采用三类道具：

- 锦囊：解锁一次幕僚秒杀思路 / 出题人陷阱判断，但用户仍需自己作答。
- 复议令：御史台失败后获得一次补考机会。
- 免参牌：保留一次 UI 特效或幕僚访问权，但不改写学习记录、不代替答题。

理由：代答很刺激，但会把产品从“学习游戏”打成“花钱作弊”。商业化要卖爽感和洞察，不卖虚假成绩。

### 决策 4：官阶惩罚强度

允许真实剥夺 UI 特效和部分 AI 权限；官阶降级作为高压事件保留，但必须有预警、挑战和复议。

执行规则：

- 轻度问题：御史台预警，只改变氛围和文案。
- 中度问题：临时封存 UI 特效、限制高阶幕僚能力，完成指定高难卷宗后恢复。
- 重度问题：触发御史台暗访，失败后罚俸 / 降官声；连续失败才降一级。
- 所有惩罚必须可通过真实做题恢复，付费只能给补考或保留展示，不改变真实学习结果。

理由：这个产品要有肉疼感，否则官场生存不成立。但惩罚必须“狠而公平”，不能让用户觉得系统在羞辱或勒索。

### 决策 5：首发平台重点

第一阶段主攻 iPhone，iPad 只保留可用布局，不投入桌面化体验。

理由：核心体验是滑动卡片、盖印动效、触感、分享图和碎片化开衙，天然属于 iPhone。iPad 的桌面化府衙等主循环稳定后再做。

## 11. 下一步

如果以上方向确认，下一份文档应是实施计划：

- 先修测试和架构地基。
- 再建 `docket` 后端与 iOS `DocketFeature`。
- 然后迁移行测卡片执行链。
- 最后退役旧 today/feed/AppState 主路径。

按本决策重置产品与架构前，不应继续堆新功能，否则项目会继续维持“四不像”。
