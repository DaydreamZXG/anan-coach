# 交接文档：治国游戏化方案与当前进度

- 日期：2026-07-07
- 交接原因：用户更换工具接手项目开发
- 接手对象：任意 AI 编程工具 / 工程师
- 阅读顺序：先读「0. 一分钟速览」和「5. 当前阻断与下一步」，再看其余

---

## 0. 一分钟速览

| 维度 | 状态 |
|------|------|
| 项目 | 「公考搭子」iOS/iPadOS 备考 App，SwiftUI 原生 + Python FastAPI 后端 |
| 当前大方向 | 从普通刷题工具升级为「治国理政」游戏化体验（古风皮肤 + 官场升迁系统 + 星露谷式 UI 架构） |
| 后端 | ✅ 100% 完成，已部署到生产服务器并验证通过 |
| iOS 前端 | ⚠️ 代码已写好但**未验证编译**；6 个文件已创建/修改，Xcode 工程文件实际是好的（前一会话误判为损坏） |
| 阻断点 | 需要在 Xcode 中打开工程 → 编译验证 → 术语全局替换 → 今日政务卡片包装 |
| 主计划文档 | [docs/aegis/plans/2026-07-06-governance-gamification-master-plan.md](file:///Users/zxg/Documents/新项目/docs/aegis/plans/2026-07-06-governance-gamification-master-plan.md) |
| 服务器 | SSH `root@101.96.198.253`，密钥 `~/.ssh/gongkao_dazi_ed25519`，数据库 `gongkao_dazi` |
| 生产 API | `https://kg.xingguoxian.cn` |

---

## 1. 项目背景与「治国」方案核心

### 1.1 产品定位
- 产品名：「公考搭子」
- 平台：iOS + iPadOS（SwiftUI 原生），后端 Python FastAPI + PostgreSQL
- 用户画像：中国公务员考试（国考/省考）备考者
- 核心壁垒：5.5 万题题库 + 岗位进面分数据 + AI 解析

### 1.2 「治国」游戏化方案（用户拍板）

把刷题工具包装成「治国理政」体验，**只换皮不重写**：

| 维度 | 改造方式 |
|------|---------|
| 世界观 | 古风术语映射（刷题→批阅奏折、资料分析→核算钱粮、判断推理→堪破冤案 等） |
| 升迁系统 | 九品到一品官制，经验值/民心值/精力值驱动 |
| UI 架构 | 参考星露谷：顶部状态栏（品级+民心+精力+进度）+ 底部 6 格快捷栏（6 大板块） |
| 视觉皮肤 | 古风非像素：羊皮纸/木纹质感、宋楷标题、朱砂红/竹简色/水墨黑/青瓷绿配色 |
| 反馈机制 | 对话框打字机效果（AI 幕僚）、升级金光动画、品级晋升印章 |
| 选岗流程 | **保留不动**，选岗=定目标分（治绩目标），贯穿全 App |
| Generative UI | **不做实时 AI 生成**，用混合模式（规则引擎抽题 + SwiftUI 本地模板 + AI 仅用于错题解析和预生成点评） |

### 1.3 用户强调的铁律（不可违背）

1. **行测 6 大板块不能改不能忘**：常识判断 / 言语理解与表达 / 数量关系 / 判断推理 / 资料分析 / 政治理论（2025 年起新增政治理论，共 6 个）
2. **注册选岗流程不删不减**：选岗的目的是锚定目标分数，作为治绩目标贯穿全 App
3. **不做真实地区古风映射**：不搞「赣州府」这种，选岗只定目标分，不映射真实地名
4. **不做 AI 实时生成卡片**：用混合模式替代（见上表）
5. **App 未上架，直接全面替换 UI**：不需要主题切换模式（保留现代风+古风），直接用古风替换
6. **iOS 原生 SwiftUI 开发**：改代码需重新编译，无 JS 式热更新
7. **服务器 baseURL 必须用生产地址** `https://kg.xingguoxian.cn`
8. **专业目录数据硬编码在 iOS App**：基于 2022 教育部学科目录，不依赖网络
9. **岗位进面分唯一标识**：position_code + 部门/地区 联合唯一（避免跨省 code 复用）
10. **所有中间件用 async/await**，不用 callback 风格

### 1.4 术语映射表（前后端各维护一份）

| 现有术语 | 古风术语 |
|---------|---------|
| 今日任务 | 今日政务 |
| 行测练习 | 批阅奏折 |
| 常识判断 | 体察民情 |
| 言语理解与表达 | 释义圣旨 |
| 数量关系 | 统筹工程 |
| 判断推理 | 堪破冤案 |
| 资料分析 | 核算钱粮 |
| 政治理论 | 研读朝政 |
| 申论 | 平灾奏折 |
| 模考 | 会试 |
| 错题 | 误判案卷 |
| 每日目标 / 目标分数 | 治绩目标 |
| 学习时长 | 理政时长 |
| 摸底测试 | 初任考评 |
| 岗位 | 官职 |
| 选岗 | 择官选任 |
| 完成引导 | 领旨赴任 |

---

## 2. 九品官制体系

| 品级 | 名称 | 经验阈值 | UI 主题 | 解锁特权 |
|-----|------|---------|--------|---------|
| 九品 | 县令 | 0 | 竹简 bamboo | 基础做题 |
| 八品 | 县丞 | 500 | 竹简 bamboo | 错题本 |
| 七品 | 知县 | 1,500 | 粗纸 rough_paper | 每日政务卡片 |
| 六品 | 知州 | 3,000 | 粗纸 rough_paper | AI 幕僚(基础) |
| 五品 | 知府 | 6,000 | 水墨 ink | 雷达图+AI 总结 |
| 四品 | 道员 | 10,000 | 水墨 ink | 申论 Boss 战 |
| 三品 | 布政使 | 16,000 | 水墨 ink | 上帝视角(部分) |
| 二品 | 巡抚 | 25,000 | 黑金 gold | AI 无限解析 |
| 一品 | 首辅 | 40,000 | 黑金 gold | 全部解锁 |

**经验值规则：**
- 答对 1 题行测：+10 XP
- 答对 1 题申论：+30 XP
- 连击 5 题：额外 +20 XP
- 每日完成全部政务：+50 XP
- 模考完成：+100 XP
- 模考超目标分：+200 XP

**民心值规则：**
- 答对：+1 民心
- 答错：-1 民心
- 连续 3 天不刷题：-10 民心/天
- 民心 < 30：UI 背景变暗红
- 民心 < 0：触发降级判定

---

## 3. 后端已完成内容（100% 完成并部署）

### 3.1 代码文件

| 文件 | 行数 | 职责 |
|------|------|------|
| [backend/app/domains/rank_system/schemas.py](file:///Users/zxg/Documents/新项目/backend/app/domains/rank_system/schemas.py) | — | Pydantic 模型：`RankConfigItem`, `OfficialRankProfile`, `XpAwardResult` |
| [backend/app/domains/rank_system/repository.py](file:///Users/zxg/Documents/新项目/backend/app/domains/rank_system/repository.py) | — | SQL 数据访问层：`get_rank`, `create_rank`, `add_xp_and_fame`, `update_rank`, `log_event`, `touch_active_date`, `list_all_rank_configs` |
| [backend/app/domains/rank_system/service.py](file:///Users/zxg/Documents/新项目/backend/app/domains/rank_system/service.py) | — | 业务逻辑：`initialize_rank`（注册时调用）, `award_xp`（答题后调用）, `get_profile`, `list_rank_configs` |
| [backend/app/domains/rank_system/router.py](file:///Users/zxg/Documents/新项目/backend/app/domains/rank_system/router.py) | 36 | API 端点 |

**关键常量（service.py）：**
```python
XP_PER_CORRECT = 10
XP_PER_ESSAY_CORRECT = 30
XP_COMBO_BONUS = 20
XP_COMBO_THRESHOLD = 5
FAME_CORRECT = 1
FAME_WRONG = -1
```

### 3.2 API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/rank/profile` | 返回当前用户官职档案（`OfficialRankProfile`） |
| GET | `/api/v1/rank/configs` | 返回 9 条品级配置（`list[RankConfigItem]`） |

### 3.3 集成点（Hook）

**1. 注册完成时初始化官职** — [backend/app/domains/onboarding/router.py](file:///Users/zxg/Documents/新项目/backend/app/domains/onboarding/router.py)

在 `complete_onboarding` 端点中：
```python
# 初始化官职记录（九品县令）
rank_service = RankService(repository=RankRepository(session))
rank_service.initialize_rank(user_id=current_user.user_id)
```

**2. 答题后奖励 XP** — [backend/app/domains/practice/router.py](file:///Users/zxg/Documents/新项目/backend/app/domains/practice/router.py)

在 `submit_practice_answer` 端点中：
```python
# 答题后更新官职经验值和民心值
rank_service = RankService(repository=RankRepository(session))
rank_service.award_xp(
    user_id=current_user.user_id,
    correct=feedback.correct,
    is_essay=False,
)
```

**3. 路由注册** — [backend/app/api/router.py](file:///Users/zxg/Documents/新项目/backend/app/api/router.py)

已添加：
```python
from app.domains.rank_system.router import router as rank_router
router.include_router(rank_router)
```

### 3.4 数据库表（服务器已创建并有数据）

服务器：`root@101.96.198.253`，数据库：`gongkao_dazi`

| 表 | schema | 说明 | 状态 |
|----|--------|------|------|
| `official_ranks` | learner | 用户官职记录（每用户一行） | ✅ 已建 |
| `official_rank_config` | product | 9 条品级配置 | ✅ 已建+已插数据 |
| `rank_events` | learner | 升降级事件日志 | ✅ 已建 |
| `module_locks` | learner | 模块锁定（智能熔断用） | ✅ 已建 |
| `censor_events` | learner | 御史台事件 | ✅ 已建 |

**完整建表 SQL 见主计划文档第「数据库设计」章节。**

### 3.5 后端验证结果（已通过）

- ✅ 新用户注册 → 自动初始化为九品县令（xp=0, fame=50, stamina=100）
- ✅ 答对一题 → XP +10，民心 +1
- ✅ 5 题连击 → 额外 +20 XP
- ✅ 答错一题 → 民心 -1
- ✅ 9 条品级配置数据正确（九品县令 0 XP → 一品首辅 40000 XP）

---

## 4. iOS 前端状态（代码已写好，未验证编译）

### 4.1 ⚠️ 重要澄清：Xcode 工程文件没有损坏

前一会话总结称 `project.pbxproj` 被 Python 脚本损坏。**经本次交接前核对，这是误判**：

```
$ plutil -lint Gongkao.xcodeproj/project.pbxproj
Gongkao.xcodeproj/project.pbxproj: OK

$ git diff --stat Gongkao.xcodeproj/project.pbxproj
 apps/ios/Gongkao/Gongkao.xcodeproj/project.pbxproj | 122 ++++++++++++++++++++-
 1 file changed, 120 insertions(+), 2 deletions(-)

$ grep -c "GuzhuDesignSystem.swift\|RankModels.swift\|StatusBarView.swift\|ModuleHotbarView.swift" Gongkao.xcodeproj/project.pbxproj
16
```

4 个新文件每个需要在 pbxproj 中注册 4 处（PBXBuildFile / PBXFileReference / PBXGroup / PBXSourcesBuildPhase），共 16 处引用，全部已正确添加。**接手者可以直接用 Xcode 打开工程尝试编译**，不需要先 `git checkout` 恢复。

### 4.2 已创建的新文件（4 个，未 git add，状态为 `??`）

| 文件 | 行数 | 职责 |
|------|------|------|
| [apps/ios/Gongkao/Gongkao/GuzhuDesignSystem.swift](file:///Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao/GuzhuDesignSystem.swift) | 243 | 古风设计系统核心：`GuzhuPalette`（颜色）、`GuzhuModule`（6 大板块枚举）、`GuzhuTerms`（术语映射表） |
| [apps/ios/Gongkao/Gongkao/RankModels.swift](file:///Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao/RankModels.swift) | 55 | iOS 端官职数据模型，对应后端 `OfficialRankProfile`，含 CodingKeys 映射 snake_case |
| [apps/ios/Gongkao/Gongkao/StatusBarView.swift](file:///Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao/StatusBarView.swift) | 127 | 顶部治绩状态栏组件：品级徽章 + 民心 + 精力 + 今日进度 |
| [apps/ios/Gongkao/Gongkao/ModuleHotbarView.swift](file:///Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao/ModuleHotbarView.swift) | 56 | 底部 6 格快捷栏组件（星露谷风格），6 个 `GuzhuModule` 按钮 |

### 4.3 已修改的现有文件（2 个，状态为 `M`）

| 文件 | 改动内容 |
|------|---------|
| [apps/ios/Gongkao/Gongkao/AppModels.swift](file:///Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao/AppModels.swift) | 1) 添加 `@Published var rankProfile: RankProfile?`；2) 在 `loadInitialData()` 中并行加载 `/api/v1/rank/profile` |
| [apps/ios/Gongkao/Gongkao/AppShellView.swift](file:///Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao/AppShellView.swift) | 重写主界面骨架：iPhone 用 `VStack` 包含 StatusBarView + TabView + ModuleHotbarView；iPad 用 NavigationSplitView + StatusBarView；术语替换（今日→今日政务、工作台→文卷阁、我的→仕途档案）；问候语用官职名 |

### 4.4 GuzhuDesignSystem.swift 关键内容

```swift
enum GuzhuPalette {
    static let paper = Color(light: Color(red: 0.93, green: 0.89, blue: 0.80),
                             dark: Color(red: 0.15, green: 0.13, blue: 0.11))
    static let cinnabar = Color(light: Color(red: 0.78, green: 0.24, blue: 0.18),
                                dark: Color(red: 0.90, green: 0.40, blue: 0.30))
    static let celadon = Color(...)   // 正确反馈
    static let ochre = Color(...)     // 提醒
    static let azurite = Color(...)   // 链接
    static func moduleColor(_ module: String) -> Color { ... }
}

enum GuzhuModule: String, CaseIterable, Identifiable {
    case minqing = "体察民情"      // 常识判断
    case shengzhi = "释义圣旨"      // 言语理解与表达
    case gongcheng = "统筹工程"    // 数量关系
    case yuanan = "堪破冤案"        // 判断推理
    case qianliang = "核算钱粮"    // 资料分析
    case chaozheng = "研读朝政"    // 政治理论
}

enum GuzhuTerms {
    static let moduleMap: [String: String] = [
        "常识判断": "体察民情", "言语理解与表达": "释义圣旨",
        "数量关系": "统筹工程", "判断推理": "堪破冤案",
        "资料分析": "核算钱粮", "政治理论": "研读朝政",
    ]
}
```

### 4.5 RankModels.swift 关键内容

```swift
struct RankProfile: Decodable {
    let userId: String
    let rankCode: String
    let rankLevel: Int
    let rankName: String
    let xp: Int
    let fame: Int
    let stamina: Int
    let consecutiveDays: Int
    let demerits: Int
    let uiTheme: String
    let nextRankName: String?
    let nextRankXpThreshold: Int?
    let xpToNext: Int?
    let unlockFeatures: [String]
    // CodingKeys 映射 snake_case → camelCase
}
```

### 4.6 AppModels.swift 改动（并行加载 rank profile）

```swift
async let rank: RankProfile = client.get(
    "/api/v1/rank/profile",
    accessToken: accessToken
)
// ...
self.rankProfile = try? await rank
```

### 4.7 iOS 端代码结构速查（全量）

目录：`apps/ios/Gongkao/Gongkao/`

| 文件 | 职责 |
|------|------|
| GongkaoDaziApp.swift | App 入口 |
| APIClient.swift | HTTP 客户端 |
| KeychainStore.swift | Token 安全存储 |
| AuthModels.swift | 认证模型 |
| AppModels.swift | AppState 单一状态源 |
| AppShellView.swift | 主界面壳（已改为古风） |
| WelcomeView.swift | 登录欢迎页 |
| OnboardingFlowView.swift | 引导流程（选岗+摸底） |
| OnboardingModels.swift / OnboardingStore.swift / OnboardingSteps.swift / OnboardingBaselineSteps.swift | 引导流程组件 |
| MajorCatalog.swift | 专业目录硬编码数据 |
| StudyDeskView.swift | iPad 三栏书桌 |
| TaskExecutionView.swift | 做题沉浸式页面 |
| MockExamView.swift | 模考流程 |
| FeatureViews.swift | Today/Workbench/Profile 页 |
| FeedCardViews.swift / FeedModels.swift / FeedFixtures.swift | Feed 卡片流 |
| CoachComponents.swift | DaziPalette 旧设计系统（保留） |
| CelebrationView.swift | 完成庆祝层 |
| CompanionAskView.swift | AI 搭子问答 |
| CaptureQuestionView.swift | 拍照识题 |
| WorkbenchDetailView.swift | 工作台详情 |
| DesignSystemPreview.swift | 设计系统预览 |
| DaziLogoView.swift | Logo 组件 |
| **GuzhuDesignSystem.swift** | **古风设计系统（新）** |
| **RankModels.swift** | **官职数据模型（新）** |
| **StatusBarView.swift** | **顶部治绩状态栏（新）** |
| **ModuleHotbarView.swift** | **底部 6 格快捷栏（新）** |

**Xcode 工程管理铁律**：新增 Swift 文件必须在 `project.pbxproj` 注册 4 处（PBXBuildFile / PBXFileReference / PBXGroup / PBXSourcesBuildPhase），可 grep `GuzhuDesignSystem` 作为已注册范例参考。

---

## 5. 当前阻断与下一步

### 5.1 当前阻断点

**无真正阻断**。前一会话误判 pbxproj 损坏，实际文件语法 OK 且 4 个新文件引用已正确添加。接手者可直接打开 Xcode 编译。

### 5.2 下一步执行顺序（建议）

#### Step 1：编译验证（优先级最高）
```bash
cd /Users/zxg/Documents/新项目/apps/ios/Gongkao
xcodebuild -project Gongkao.xcodeproj -scheme Gongkao -destination 'platform=iOS Simulator,name=iPad' build
```
如有编译错误，优先修复 `GuzhuDesignSystem.swift` / `RankModels.swift` / `StatusBarView.swift` / `ModuleHotbarView.swift` 中的类型/符号问题。常见可能问题：
- `Color(light:dark:)` 扩展是否存在（检查 `CoachComponents.swift` 中 `DaziPalette` 是否有类似构造器，若无需要在 `GuzhuDesignSystem.swift` 中改用 iOS 标准写法）
- `RankProfile` 的 `CodingKeys` 是否完整
- `StatusBarView` / `ModuleHotbarView` 引用的 `appState.rankProfile` 是否正确

#### Step 2：术语映射表全局替换
在 `StudyDeskView.swift`、`TaskExecutionView.swift`、`FeatureViews.swift`、`FeedCardViews.swift` 等页面中，把硬编码的「今日任务」「行测练习」等替换为 `GuzhuTerms.moduleMap[...]` 查表结果。**注意：6 大板块的原始 key 不能改，只改显示文案**。

#### Step 3：今日政务卡片包装
把现有 `taskstream` 输出的任务包装成「卷宗卡片」UI（翻开动画 + 古风边框 + 朱砂印章），替换现有 feed 卡片样式。

#### Step 4：iPad 横屏适配优化
用户主要用 iPad 横屏。`AppShellView.swift` 的 iPad 分支用 `NavigationSplitView` + `StatusBarView`，需要确保横屏信息密度合理（参考星露谷信息架构，不要单侧大留白）。

#### Step 5：阶段 2-4 功能（见主计划文档）
- 阶段 2（游戏化闭环）：心流滑动做题、智能熔断、御史台巡视、AI 幕僚话术
- 阶段 3（差异化功能）：上帝视角、申论 Boss 战、同榜进士排行榜
- 阶段 4（变现）：IAP 订阅（首辅月度/年度）+ 消耗品（锦囊、免死金牌）

---

## 6. 服务器与部署信息

| 项 | 值 |
|----|-----|
| SSH | `ssh -i ~/.ssh/gongkao_dazi_ed25519 root@101.96.198.253` |
| 数据库 | PostgreSQL，库名 `gongkao_dazi` |
| API 服务 | systemd 管理，`systemctl restart gongkao-api` |
| 反向代理 | Caddy |
| 生产 baseURL | `https://kg.xingguoxian.cn` |
| 后端代码部署路径 | 服务器上对应 `/root/` 或 `/opt/` 下（首次接手需 SSH 上去 `systemctl status gongkao-api` 确认） |

**后端代码同步方式**：本地修改后用 `scp` 或 `rsync` 推送到服务器，然后 `systemctl restart gongkao-api`。具体同步脚本见 `scripts/` 目录。

---

## 7. 用户偏好与协作纪律

### 7.1 用户画像
- 角色：产品经理 / UI-UX 设计师
- 沟通语言：中文
- 工作习惯：定期审计 App 界面，主动反馈功能问题，要求彻底修复

### 7.2 UI/UX 偏好（必须遵守）
- iPad 横屏：纠错页、做题页用双栏布局；其他页面不要做成「放大版手机版」
- 参考星露谷 UI：温暖、有质感、不装腔作势；状态栏 + 快捷栏信息架构
- 古风视觉皮肤（非像素风）：羊皮纸/木纹质感、宋楷标题、朱砂红/竹简色/水墨黑/青瓷绿
- 卡片圆角统一 12px
- 操作后给出清晰选项（继续 / 返回）
- 拍照识题、裁剪交互要直观
- 考试页面沉浸式：全屏布局、固定提交按钮、最小化导航
- 反对单侧大留白的不平衡布局
- 要求数据准确可靠，反对用错误数据或共享数据糊弄
- 加载页要有鼓励性 slogan（情绪价值）

### 7.3 协作纪律
- 改动前先读文件，理解现有代码再改
- 不要过度工程化，只做被要求的改动
- 不要主动创建文档文件（除非用户明确要求）
- iOS 改代码后需重新编译，无热更新
- 后端 baseURL 必须用生产地址，不要用 localhost
- 6 大板块是死的，不能多不能少
- 选岗流程不能删，选岗=定目标分

---

## 8. 关键文件清单（接手必读）

### 8.1 计划与规范文档
| 文件 | 用途 |
|------|------|
| [docs/aegis/plans/2026-07-06-governance-gamification-master-plan.md](file:///Users/zxg/Documents/新项目/docs/aegis/plans/2026-07-06-governance-gamification-master-plan.md) | **治国方案唯一执行权威文档**，含完整数据库 SQL、4 阶段路线图、风险对策、不做清单 |
| [docs/aegis/handoffs/2026-07-06-v0-session-handoff.md](file:///Users/zxg/Documents/新项目/docs/aegis/handoffs/2026-07-06-v0-session-handoff.md) | 前一次视觉设计交接文档（含 DaziPalette 旧设计系统背景） |
| [docs/aegis/handoffs/2026-07-07-governance-gamification-handoff.md](file:///Users/zxg/Documents/新项目/docs/aegis/handoffs/2026-07-07-governance-gamification-handoff.md) | **本文档** |

### 8.2 后端核心文件
| 文件 | 用途 |
|------|------|
| [backend/app/domains/rank_system/](file:///Users/zxg/Documents/新项目/backend/app/domains/rank_system/) | 官场升迁系统 domain（4 件套） |
| [backend/app/api/router.py](file:////Users/zxg/Documents/新项目/backend/app/api/router.py) | 路由注册总入口 |
| [backend/app/domains/onboarding/router.py](file:///Users/zxg/Documents/新项目/backend/app/domains/onboarding/router.py) | 注册完成时初始化官职的 hook |
| [backend/app/domains/practice/router.py](file:///Users/zxg/Documents/新项目/backend/app/domains/practice/router.py) | 答题后奖励 XP 的 hook |

### 8.3 iOS 核心文件
| 文件 | 用途 |
|------|------|
| [apps/ios/Gongkao/Gongkao/GuzhuDesignSystem.swift](file:///Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao/GuzhuDesignSystem.swift) | 古风设计系统（新） |
| [apps/ios/Gongkao/Gongkao/RankModels.swift](file:///Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao/RankModels.swift) | 官职数据模型（新） |
| [apps/ios/Gongkao/Gongkao/StatusBarView.swift](file:///Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao/StatusBarView.swift) | 顶部状态栏（新） |
| [apps/ios/Gongkao/Gongkao/ModuleHotbarView.swift](file:///Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao/ModuleHotbarView.swift) | 底部快捷栏（新） |
| [apps/ios/Gongkao/Gongkao/AppShellView.swift](file:///Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao/AppShellView.swift) | 主界面壳（已改古风） |
| [apps/ios/Gongkao/Gongkao/AppModels.swift](file:///Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao/AppModels.swift) | AppState（已加 rankProfile） |

### 8.4 Xcode 工程
| 文件 | 状态 |
|------|------|
| [apps/ios/Gongkao/Gongkao.xcodeproj/project.pbxproj](file:///Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao.xcodeproj/project.pbxproj) | 已修改（+120/-2），4 个新文件 16 处引用已添加，`plutil -lint` 通过 |

---

## 9. Git 状态摘要

当前分支有大量未提交改动（含本次会话和之前多次会话的累积）。关键未提交项：

- `M apps/ios/Gongkao/Gongkao.xcodeproj/project.pbxproj`（4 个新文件引用已加）
- `M apps/ios/Gongkao/Gongkao/AppModels.swift`（加 rankProfile）
- `M apps/ios/Gongkao/Gongkao/AppShellView.swift`（改古风壳）
- `?? apps/ios/Gongkao/Gongkao/GuzhuDesignSystem.swift`（新文件，未 git add）
- `?? apps/ios/Gongkao/Gongkao/RankModels.swift`（新文件，未 git add）
- `?? apps/ios/Gongkao/Gongkao/StatusBarView.swift`（新文件，未 git add）
- `?? apps/ios/Gongkao/Gongkao/ModuleHotbarView.swift`（新文件，未 git add）
- `?? backend/app/domains/rank_system/`（整个 domain 未 git add）
- `M backend/app/api/router.py`（注册 rank_router）
- `M backend/app/domains/onboarding/router.py`（初始化 hook）
- `M backend/app/domains/practice/router.py`（XP 奖励 hook）

**建议接手者第一步：先 `git add` 后端 rank_system + 修改的 router 文件并提交，再处理 iOS。**

---

## 10. 验证清单（接手后自检）

- [ ] SSH 上服务器，`systemctl status gongkao-api` 确认服务正常
- [ ] `curl https://kg.xingguoxian.cn/api/v1/rank/configs`（带 token）确认返回 9 条品级配置
- [ ] Xcode 打开 `Gongkao.xcodeproj`，确认能正常打开（不报「Unable to read project」）
- [ ] `xcodebuild ... build` 编译通过
- [ ] iPad 模拟器运行，确认顶部状态栏 + 底部快捷栏显示
- [ ] 新用户注册流程走完，确认官职初始化为九品县令
- [ ] 做一题行测，确认 XP +10、民心 +1
- [ ] 6 大板块在快捷栏中都存在且顺序正确

---

**交接完毕。如有疑问，主计划文档是唯一权威来源。**
