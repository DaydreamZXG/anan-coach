# 会话交接文档（v0 → 下一位设计/工程 Agent）

- 日期：2026-07-06
- 交接原因：用户对当前 Agent 的设计能力不满意，更换模型继续做 iPad 端与全局视觉设计
- 阅读优先级：先读「当前最痛的问题」和「协作纪律」，再看其余

## 一、产品与目标背景

- 产品：「公考搭子」——中国公务员考试（国考/省考）备考 iOS/iPadOS App，SwiftUI 原生
- 产品人格：懂你为什么错的备考搭子。核心差异化是 GenUI 卡片流（今日页由后端 feed 域动态编排任务卡，每张卡带"为什么给你排这个"的 reason）
- 情绪价值定位（用户明确拍板）：打不过粉笔/华图的题量，就把情绪价值给满。梗：称用户"局长"（预支尊称）。边界纪律：玩笑止于称呼，不承诺上岸、不编造数据、低正确率不虚假夸奖
- 近期目标：参加 Trae 创造力大赛，出 demo + 推广视频，iPad 横屏为主要录制载体
- 距 demo 的主要缺口：演示种子数据脚本（未开工）、iPad 视觉打磨（进行中，即本次交接核心）、视频脚本

## 二、当前最痛的问题（用户最新反馈，按优先级）

1. 全局视觉是"毛胚房"：用户原话"除了登录页其他都太丑太没设计感"。我做了一版「公文风错题本」设计系统（暖黄纸底）被否——"颜色很丑，反人类"。已回退为冷调中性灰底+纯白卡。设计语言尚未真正立住，需要重新定方向
2. iPad 三栏书桌（StudyDeskView）留白过多、信息密度低，横竖屏都不够商业产品水准
3. 逐页视觉套用未完成：feed 卡片、工作台、模考页、引导流程都还是功能性拼装

## 三、代码结构速查（iOS 端）

目录：`apps/ios/Gongkao/Gongkao/`

| 文件 | 职责 | 本会话改动 |
|---|---|---|
| AppShellView.swift | 根壳。iPhone=TabView 三分区；iPad=NavigationSplitView 侧边栏+detail。登录门控（!isAuthenticated 显示 WelcomeView）、模考 fullScreenCover、做题 fullScreenCover、庆祝 overlay 都挂在这两个壳的根部 | 大改 |
| WelcomeView.swift | 登录欢迎页："欢迎局长回家"+轮换应援语+Apple 登录。用户唯一满意的页面 | 新建 |
| StudyDeskView.swift | iPad 三栏书桌（左=学习对象/右=搭子面板/中=DeskCanvas 卡片流画布）。已移除内嵌做题分支 | 中改 |
| TaskExecutionView.swift | 带练做题页（PracticeExecutionView）+ 逐题反馈（CoachFeedbackCard，轮换暖文案）。沉浸式=fullScreenCover 全屏接管，顶栏极简（叉号退出/居中进度/草稿纸）。题干衬线字体 | 大改 |
| CoachComponents.swift | DaziPalette 设计令牌（唯一颜色来源）+ paperCard 修饰器 + DocketHeader + GoalStripView（-0 分已修为"待校准"） | 大改 |
| CelebrationView.swift | 任务完成庆祝层：真实正确率分档文案（大于等于 85% 彩带粒子；低分温柔接住）。挂双壳根部 | 新建 |
| AppModels.swift | AppState 单一状态源：openTask 按 kind 路由（mock_exam 走模考流程/mock_exam_review 走复盘/默认走带练）、会话对错统计（sessionCorrect/sessionAnswered）驱动庆祝层、mockExamRoute | 中改 |
| MockExamFlowView.swift | 模考沉浸流程（takeWeekly / viewReview(sessionId)），120 分钟倒计时 | 未动 |
| FeatureViews.swift | TodayView（iPhone 今日页，feed 卡片流）、WorkbenchView、ProfileView | 小改 |
| FeedCardViews.swift | FeedCardList：feed 卡片渲染（任务卡/模考卡/复盘卡/收藏卡等） | 未动 |

关键机制：
- 做题沉浸式：`fullScreenCover(item: $appState.activeTask)`，iPhone 挂 iPhoneShell、iPad 挂 iPadShell，两处都要维护
- pbxproj 手工管理：新增 Swift 文件必须在 `project.pbxproj` 注册四处（BuildFile/FileReference/group/Sources），有先例可 grep CelebrationView

## 四、设计系统现状（半成品，接手重点）

`DaziPalette`（CoachComponents.swift 顶部）：
- brand 墨蓝 rgb(0.16,0.23,0.42)、alert 印泥红、positive 墨绿、paper 冷调中性灰底 rgb(0.961,0.963,0.973)、card 纯白、cardStroke 极浅墨蓝描边、ink 墨色正文
- 全局已完成：系统灰底/系统白卡已被 sed 全量替换为令牌（改令牌一处即全局生效）
- `paperCard()` 修饰器：统一卡片语言（白卡+细描边+浅投影），但只有做题页题干卡在用，其他页面未套用
- `DocketHeader`：公文风小节头（红竖条+衬线标题），已定义但全 App 尚未使用
- 题干用衬线（fontDesign(.serif)），UI 字无衬线——这个分层用户没有反对，可保留
- 被否决的方案：暖黄纸底（大屏显脏）。用户"反人类"评价主要指颜色

## 五、后端与数据（本会话相关部分）

- 生产：101.96.198.253，SSH key `/Users/zxg/.ssh/gongkao_dazi_ed25519`，库 `gongkao_dazi`（PostgreSQL，schema：product / canonical_qbank 等）
- 生产代码落后本地：生产有 mock_exam 域但没有 feed 域。录 demo 连本地后端即可，不阻塞；上线前需部署
- 方法卡：`product.method_cards`。资料分析 13 张 draft（fable 产）+ 言语 10 张 draft（GLM 5.2 产，本会话校验入库，质量合格）。draft 状态不对用户曝光，需产品负责人回测后转 approved
- 生产库写入权限：API 用户对 method_cards 只读，导入要用 `sudo -u postgres` 走本地 socket（`DATABASE_URL='postgresql+psycopg:///gongkao_dazi'`）
- GLM 协作规格：`docs/aegis/specs/2026-07-06-glm-yanyu-cards-handoff.md`（执行文件）+ 同日 fable 的规格（词表权威）。注意：tag_path 用「言语理解与表达/...」三段式，「行测/...」前缀是废案

## 六、环境与工作流

- 工作区：用户 Mac，经 Loca MCP 操作（Loca_exec_command / Loca_apply_patch）。v0 沙盒的 Write 工具不落用户盘，改文件必须用 Loca_apply_patch；Loca 连接偶发断开，断了让用户重连
- iPad 模拟器：iPad Pro 11 寸，UDID `79D44165-8380-4975-A760-F036019C412B`；iPhone 17：`5A64A49A-F0C6-4A99-9BCB-518A79AF65C3`
- 编译部署：`cd apps/ios/Gongkao && xcodebuild -project Gongkao.xcodeproj -scheme Gongkao -destination 'id=<UDID>' -derivedDataPath .build build`，然后 `xcrun simctl install/launch`（bundle id `com.xingguoxian.gongkaodazi`）
- 模拟器截图传不回沙盒，让用户手动截图贴到对话里
- 协作方注意：codex 当前状态差，大活别切给它；GLM 5.2 产内容质量不错，可继续用规格约束的方式协作

## 七、待办清单（按用户价值排序）

1. 重新定义全局设计语言并逐页推平（本次换模型的直接原因）——先出方向给用户确认再动手，别自作主张铺全局
2. iPad 书桌信息密度与留白优化（横竖屏）
3. 演示种子数据脚本：造一个"备考第 5 周考生"账号（连续学习天数/模考记录/错题聚类/校准数据），否则 feed 卡片流录屏干瘪、reason 全是"数据不足"降级
4. 新号全流程验证：登录到引导到设目标到今日页（用户账号是老号，流程没走过）
5. 模考复盘页开场暖话、连续学习里程碑庆祝（情绪价值第二批，已规划未做）
6. 方法卡回测流程（用户的债，需要提醒但别催）
7. 生产部署 feed 域（上线前）
8. 视频脚本与分镜（2-3 分钟：痛点、目标设定、卡片流、模考、拍照收题、复盘反哺闭环）

## 八、协作纪律（用户偏好，务必遵守）

- 中文回复；姿态是共事的工程师同事，不是客服。用户批评时认账、给根因，别道歉刷屏
- 诚实原则高于一切：不虚假夸奖、不编造数据、draft 内容不曝光。情绪价值等于真实成就的热烈庆祝加挫折的温柔接住
- 用户是产品唯一决策人，设计大方向先问再做；小修直接做完给结果
- 每轮改完必须编译加部署到模拟器再汇报，别只说"改好了"
- 改动要交代根因（为什么错）而不只是改了什么
