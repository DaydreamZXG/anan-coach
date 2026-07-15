# 治国 / 公考搭子 — 项目交接文档

> 最后更新：2026-07-07  
> 维护者：zxg  
> 状态：R1 可内测主链已完成并已发布生产后端：开衙卷宗 -> 卡片批阅 -> 官阶/民心反馈 -> 政务战报 -> 府衙复盘；R3/R4 已修复题干富文本、画横线、翻页题干残留和选项重复 label；R5 已补齐战报后的下一步闭环，后端本地测试绿，生产 `/health` 绿，iOS Simulator 构建绿。

---

## 0. 一句话定位

面向公考备考的**官场生存刷题游戏**。痛点是“枯燥、坚持难、普通题库无情绪记忆点”，产品用真实公考题库包装成开衙、披阅奏折、治理辖区、升迁或被御史台盯上的强情绪训练体验。主 KPI 仍是分差缩小 / 模考提升 / 上岸率，但第一吸引力是“这个不像粉笔”。

世界观：考生 = 县令（主公），AI 搭子 = 师爷「沈砚」，术语统一古风（今日政务=真题卷宗、会试=模考、仕途=品级页）。iPhone 首屏 canonical path 是 `开衙 -> 动态政务卷宗 -> 卡片批阅 -> 官阶/民心反馈 -> 政务战报`。

---

## 1. 仓库结构

```
新项目/
├── apps/ios/Gongkao/        # iOS App（SwiftUI, 单 target）
├── backend/                 # FastAPI 后端
│   ├── app/domains/         # 按领域划分（knowledge/practice/rank_system/...）
│   ├── app/main.py          # create_app 入口
│   ├── tests/               # pytest，需真实 DB schema
│   └── ...
├── data/                    # 题库导入相关数据
├── docs/                    # 文档（本文件在此）
├── ops/                     # 运维脚本
├── reports/                 # 报告产出
├── exports/                 # 模拟器截图等
└── scripts/ tools/          # 各类工具脚本
```

---

## 2. 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.10+ / FastAPI / SQLAlchemy / uvicorn |
| 数据库 | PostgreSQL（本机 5432，库名 `gongkao_dazi`） |
| 题库库 | `QBANK_IMPORT_DATABASE_URL`（单独的题库导入源） |
| AI | Gemini 反向代理（AIProxy），OpenAI 兼容 `/chat/completions` |
| iOS | SwiftUI / Swift 6 / iOS 17+ / 单 Scheme `Gongkao` |
| 部署 | 非 git，**rsync 改动文件 → systemctl restart** |

---

## 3. 部署与运维

### 服务器
- `root@101.96.198.253`
- SSH 密钥：`~/.ssh/gongkao_dazi_ed25519`
- 后端路径：`/srv/gongkao-dazi/backend`
- 生产 API：`https://kg.xingguoxian.cn`
- 健康检查：`https://kg.xingguoxian.cn/health`

### 服务
- systemd 单元名：**`gongkao-dazi-api`**（active）
- ⚠️ 用户口中的 "gongkao-api" 是 **inactive 别名**，真正生效的是 `gongkao-dazi-api`。
- 启动命令：`uvicorn app.main:app @127.0.0.1:8000`

### 数据库
- 库名 `gongkao_dazi`，本机 PostgreSQL 5432
- psql 连接时需把连接串里的 `postgresql+psycopg://` 改成 `postgresql://`
- `DATABASE_URL` 在 `/etc/gongkao-dazi/db.env`

### 部署流程（非 git）
```bash
# 1. rsync 改动的文件（示例）
rsync -az -e "ssh -i ~/.ssh/gongkao_dazi_ed25519 -o StrictHostKeyChecking=no" \
  <本地文件绝对路径> \
  root@101.96.198.253:/srv/gongkao-dazi/backend/<对应目录>/

# 2. 重启
ssh -i ~/.ssh/gongkao_dazi_ed25519 root@101.96.198.253 \
  "systemctl restart gongkao-dazi-api && sleep 2 && systemctl is-active gongkao-dazi-api"

# 3. 验证
curl -s https://kg.xingguoxian.cn/health
```

### 2026-07-07 R2 生产修复记录
- 现象：用户反馈“登录报错”。模拟器复现后发现 Apple 登录态已存在并进入主界面，真正报错为 `服务器返回 404：{"detail":"Not Found"}`，发生在 iOS 首页调用 `POST /api/v1/dockets/open-court`。
- 根因：iOS 已切到 R1 新主链，但生产 OpenAPI 尚未发布 `dockets` / `review-sessions` / `progression` 路由。
- 已处理：执行 `scripts/server_setup_learning_ai_tables.sh` 非破坏性补齐 `learner.dockets`、`learner.docket_cards`、`learner.verdicts`；rsync 后端新领域代码与路由；重启 `gongkao-dazi-api`。
- 验证：`https://kg.xingguoxian.cn/openapi.json` 已包含新路由；未登录 `POST /api/v1/dockets/open-court` 返回 401（不再 404）；模拟器现有 Apple 登录态重启后 `open-court` 返回 200 并渲染 10 封政务急件。

### 2026-07-07 R3 题干富文本修复记录
- 现象：用户反馈“画横线部分”题干没有横线。复现题为 `它们像一群的小侦探...填入画横线部分...`，视觉缺少空格处横线。
- 根因：`product.questions.stem_html` 中有 `<u>...</u>`，但旧 Docket 合同只返回纯文本 `stem`；iOS 首屏只渲染 `Text(card.stem)`。补齐合同后，iOS HTML importer 又会把 `<u>` 内纯空白折叠成不可见空白。
- 已处理：`docket` 后端合同新增并持久化 `stemHtml` / `materialHtml` / option `html`，复用旧 docket 时从 `product.questions` 左连接取最新 HTML；iOS 新增 `QuestionRichText`，统一用于卷宗、复盘、摸底和会试题干/材料/选项；对 `<u>` 内纯空白按题库 HTML 语义渲染成可见答题横线。
- 同轮 UI 修正：`DocketView` 只给 ScrollView 内部内容预留 iOS 浮动 Tab 栏底部余量，避免卷宗内容画到 Tab 栏玻璃后；不要给外层 ScrollView 加 bottom padding，否则会形成固定遮挡空白。
- 验证：生产 OpenAPI `DocketCard` 已含 `stemHtml` / `materialHtml`；生产 `gongkao-dazi-api` active 且 `/health` ok；XcodeBuildMCP `build_run_sim` succeeded；模拟器语义快照显示 `像一群______的小侦探`；截图证据见 `exports/2026-07-07-docket-underline-full.jpg` 和 `exports/2026-07-07-underline-evidence.jpg`。

### 2026-07-07 R4 卷宗翻页/选项展示修复记录
- 现象：用户反馈“下一题题目没变选项变了，而且两个 ABCD”。模拟器复现为 `2/10` 时题干仍是“小红帽/小侦探”，但选项变成另一题；选项同时显示左侧圆标 `A` 和正文 `A、...`。
- 抽题规则澄清：`今日开衙` 不是每点下一题实时随机；后端开衙时一次性从题库抽取 10 张卡并持久化为当天 docket，同一天复用同一批。
- 根因：生产当天旧 docket 的 option payload 缺少 option html 且正文带 `A、` 前缀；同时 iOS `ReviewCardSurface` 翻页时没有按 `cardId` 重建整张卡，SwiftUI 富文本题干可能沿用上一题 `Text(AttributedString)`。
- 已处理：后端 `DocketOption` 规范化 `text`，复用旧 docket 时从 `product.question_options` 回填 option html；iOS `DocketOption.displayText` 与旧行测/会试页一致去掉 label 前缀；`ReviewCardSurface` 增加 `.id(card.cardId)`，翻页时重建卡片。
- 验证：后端 9 个相关测试 passed；生产 `DocketRepository.list_cards()` 读取当天 docket 时第 1/2 题的题干与选项已各自匹配且选项无重复 label；生产服务 active，`/health` ok；XcodeBuildMCP `build_sim` succeeded。当前工具无法可靠滚动/点击模拟器进入第二题，未把“UI 上手点第二题”伪装成已验。

### 2026-07-07 R5 战报后的下一步闭环
- 现象：用户反馈 10 题批完后“好像没有下一步”。原战报页只有“呈送战报”分享动作，缺少继续学习的主路径。
- 产品决策：战报不是终点，战报后的主动作必须进入“府衙复盘/归档”。有错题时主 CTA 为“去府衙复盘错卷”，全对时为“去府衙归档战报”；分享降为次级动作。
- 已处理：`CourtReportPanel` 增加主 CTA；`DocketView -> ReviewCardsView -> CourtReportPanel` 透传 `onOpenWorkbench`，点击后切到府衙并强制刷新工作台；后端 `memory` 从 `learner.verdicts` 生成 `docket_review` 工作台对象；iOS 府衙页改为“府衙案牍”表达，并支持 `docket_review` 类型与 `DocketReviewDetail`，可查看本次卷宗题目/材料/选项。
- 维护约束：当前 `DocketReviewDetail` 是“查看复盘”，不是“二次答题提交”。如果后续要支持错题重做，必须设计专门的 docket retry endpoint，不要伪装成旧 `practice/tasks/{taskId}/answers` 能覆盖卷宗复盘。
- 验证：`backend/.venv/bin/python -m pytest backend/tests/test_api.py::test_workbench_preview_uses_real_learner_objects backend/tests/test_review_session_verdict.py backend/tests/test_docket_open_court.py -q` 为 7 passed；生产 `/health` ok；XcodeBuildMCP `build_sim` / `build_run_sim` succeeded；`snapshot_ui` 看到真实卷宗页，截图证据见 `exports/2026-07-07-r5-open-court-snapshot.jpg`。

---

## 4. 后端架构

### 领域划分（app/domains/）

| domain | 职责 |
|--------|------|
| knowledge | 题库查询、知识点地图、加权抽题 |
| docket | **iPhone 首页 canonical owner**：开衙、10 题动态政务卷宗、题源溯源 |
| review_session | **卡片批阅 canonical owner**：卷宗卡作答、准/驳、判分入库 |
| progression | **官阶/民心 canonical owner**：包装 `rank_system`，负责政绩、民心、辖区风险事件 |
| practice | 旧行测任务流做题；内部/过渡路径，不再是 iPhone 首屏主链 |
| essay | 申论批改；后续改造成 Boss 战 |
| taskstream | 今日政务生成、明日预览、**每日轮换** |
| rank_system | 九品官阶持久化与配置底座；新业务入口走 `progression` |
| companion | AI 搭子（师爷）问答、外部错题摄入 |
| mock_exam | 会试（整卷模考）、复盘 |
| calibration | 能力校准估计 |
| memory | 学习者记忆、画像、完成记录 |
| auth | 认证（Sign in with Apple） |
| feed | 今日信息流卡片 |
| onboarding | 摸底、目标设定、授品 |
| method_cards | 政务锦囊 |
| positions | 岗位库 |
| position_decision | **AI 上岸顾问改造新增 owner**：基于岗位、目标分、当前估分输出冲/稳/保岗位组合、风险标签与下一步动作 |
| censor | 内容审核 |
| module_locks | 模块锁定 |

### 关键数据表
- `learner.profiles` — 用户画像（目标分、基线、学习模式…）
- `learner.official_ranks` — 官阶记录（rank_code/rank_level/xp/fame/consecutive_days…）
- `learner.practice_answers` — 行测作答记录
- `learner.dockets` — 每日开衙卷宗
- `learner.docket_cards` — 卷宗内政务卡片队列
- `learner.verdicts` — 卡片批阅答案/准驳记录
- `learner.mock_exam_sessions` — 会试记录（review_payload 含 estimatedScore）
- `learner.rank_events` — 品级事件日志
- `product.official_rank_config` — 九品配置（9 行，rank_code/rank_level/rank_name/xp_threshold/unlock_features/ui_theme/ai_privilege）
- `product.questions` — 题库主表
- `product.knowledge_map_nodes` — 知识点地图

### 九品体系（品级数字越大官越小，升迁 = level 递减）

| level | rank_name | xp_threshold | 解锁 |
|-------|-----------|-------------|------|
| 9 | 九品县令 | 0 | — |
| 8 | 八品县丞 | 500 | wrong_question_book 错题案卷簿 |
| 7 | 七品知县 | — | government_cards 政务锦囊全库 |
| 6 | 六品知州 | — | ai_counselor_basic 基础AI辅政 |
| 5 | 五品知府 | — | radar_chart 能力雷达图 / ai_summary AI周报 |
| 4 | 四品道员 | — | essay_boss 申论精批 |
| 3 | 三品布政使 | — | god_view_partial 封地沙盘（局部） |
| 2 | 二品巡抚 | — | ai_unlimited 搭子无限问政 |
| 1 | 一品首辅 | — | all_unlocked 全境解锁 |

### XP 规则（rank_system/service.py）
- `XP_PER_CORRECT = 10`
- `XP_PER_ESSAY_CORRECT = 30`
- `XP_COMBO_BONUS = 20`（每 5 连击触发，`XP_COMBO_THRESHOLD = 5`）
- `XP_MOCK_COMPLETE = 100`，`XP_MOCK_EXCEED_TARGET = 200`（会试 ≥60 分）
- `FAME_CORRECT = +1`，`FAME_WRONG = -1`（fame 不会低于 0）

### unlock_features 累计规则（P3b 改动）
`get_profile` 返回的 `unlock_features` 是**累计**：所有 `rank_level >= 当前 level` 的配置项 unlock_features 的并集（不是单品级）。接手时注意这点。

---

## 5. iOS 架构

### 关键文件

| 文件 | 职责 |
|------|------|
| `GongkaoDaziApp.swift` | App 入口 |
| `AppModels.swift` | AppState 全局状态、数据拉取、品级刷新逻辑 |
| `AppShellView.swift` | iPhone/iPad 主壳，StatusBar + Hotbar + 导航 |
| `StatusBarView.swift` | 顶栏：品级徽章 + 分差 + **XP 进度条**（P0a） |
| `RankDetailView.swift` | 仕途详情页（P0a） |
| `RankModels.swift` | RankProfile / RankConfigItem + `isUnlocked(_:)`（P3b） |
| `CelebrationView.swift` | CelebrationOverlay + **RankUpOverlay**（P0b） |
| `InvestitureView.swift` | 授品仪式四阶段动画（P2b） |
| `CompanionAskView.swift` | 问搭子页 + **师爷头像横幅**（P2a） |
| `TaskExecutionView.swift` | 做题页 + CoachFeedbackCard（含 +XP 飘字 P1b） |
| `OnboardingFlowView.swift` | 摸底流程，收尾弹授品仪式（P2b） |
| `CoachComponents.swift` | **DaziPalette 桥接层 + DaziGradient + 通用组件** |
| `GuzhuDesignSystem.swift` | **GuzhuPalette 真源 + SealStamp + GuzhuFont + 术语表** |
| `DocketModels.swift` / `DocketStore.swift` / `DocketView.swift` | iPhone 开衙首页与动态政务卷宗 |
| `ReviewCardsModels.swift` / `ReviewCardsStore.swift` / `ReviewCardsView.swift` | 一题一卡批阅、准/驳印章、progression 反馈 |
| `ShareReportView.swift` | 10 题完成后的政务战报与系统分享 |
| `WorkbenchDetailView.swift` | 府衙复盘详情；`docket_review` 展示卷宗题目/材料/选项 |

### 设计系统（铁律）
- **`GuzhuPalette` 是唯一颜色真源**（古风竹简米黄 + 朱砂）。`DaziPalette` 是桥接层，只转发，**不再引入新色值**。新增颜色必须只走 GuzhuPalette。
- 关键色：`cinnabar`(朱砂)、`ochre`(赭石)、`vermilion/vermilionDeep`(绛红，CTA 用)、`celadon`(青瓷绿，正反馈)、`ink/faintInk`(墨色文字)、`paper/card`(底色)。
- 渐变：`DaziGradient.brandLinear`(朱砂→赭石，大卡片)、`DaziGradient.actionLinear`(绛红→深朱，CTA)。
- 卡片语言：`.parchmentCard()`（宣纸卷质感）。

### 术语 / 人设约定
- 人设：考生 = **县令/主公**；AI 搭子 = **师爷·沈砚**（字明远）。
- 术语映射（GuzhuTerms.moduleMap）：常识判断→体察民情、言语→释义圣旨、数量关系→统筹工程、判断推理→堪破冤案、资料分析→核算钱粮、政治理论→研读朝政、行测→批阅奏折。
- 统一术语：今日政务(非任务)、会试(非模考)、仕途(非品级页)。

### iOS 签名
- Apple ID：6786934387（未上架）
- Team ID：BRG82P5ZB7
- 证书：`Apple Development: xiangguo Zheng (U8X5B5U92A)`
- Bundle ID：`com.xingguoxian.gongkaodazi`
- ⚠️ pbxproj 曾因 TEAM 空 + CODE_SIGN_IDENTITY="-" 导致 Sign in with Apple error 1000，已修复为 Apple Development/Distribution。改 pbxproj 要小心 ID 冲突。

### pbxproj 加新源文件（4 处引用）
1. PBXBuildFile（`xxx in Sources`）
2. PBXFileReference（`path = Xxx.swift`）
3. Group children 列表
4. PBXSourcesBuildPhase files 数组

最近用的 ID：ShareReportView=16A/30A。下一个可用 16B/30B。

---

## 6. 本次 R1 改动清单（文件级）

### R1 后端（已部署生产，2026-07-07 R2）

| 文件 | 改动 |
|------|------|
| `domains/progression/*` | 新增 progression owner，统一官阶/民心/辖区风险事件，底层复用 `rank_system` 持久化 |
| `review_session/service.py` `review_session/schemas.py` | verdict 响应增加 `progression`，答题后写入 XP/fame 并返回幕僚叙事 |
| `docket/service.py` `docket/router.py` | 首页卷宗改由 `progression` 读取官阶/辖区状态，不直接调用 `rank_system` |
| `practice/router.py` `essay/router.py` `mock_exam/service.py` | 主执行链改走 `ProgressionService`，不再直接实例化/依赖 `RankService` |
| `api/router.py` | 挂载 `/api/v1/progression/profile`、`/api/v1/progression/court-state` |
| `tests/test_progression_contract.py` | 增加 owner 边界扫描，防止主链回流旧 rank |

### R1 iOS（BUILD/RUN SUCCEEDED）

| 文件 | 改动 |
|------|------|
| `ReviewCardsModels.swift` | 解码后端 `progression` 结算 |
| `ReviewCardsStore.swift` | 提交 verdict 后触感反馈；末题可进入完成态 |
| `ReviewCardsView.swift` | 答题后展示政绩/民心叙事；末题进入战报，不再卡住“收卷” |
| `ShareReportView.swift` | 新增“今日政务战报”分享图渲染与系统分享面板 |
| `DocketView.swift` | verdict 后刷新全局品级；卷宗 ID 变化时重建卡片状态 |
| `Gongkao.xcodeproj/project.pbxproj` | 加 `ShareReportView.swift` 四处引用 |

### R0/P0–P3 历史改动（部分已部署生产）

### 后端（已部署生产）

| 文件 | 改动 |
|------|------|
| `rank_system/repository.py` | `consecutive_correct_count`（连击统计） |
| `rank_system/service.py` | `award_xp_amount`（固定 XP）；**`get_profile` 累计解锁** |
| `practice/router.py` `practice/schemas.py` | submit 接 combo+award_xp，`AnswerFeedback.xp_gained` |
| `essay/router.py` | review 后 +30 XP |
| `mock_exam/service.py` `mock_exam/router.py` | submit 后 +100/+200 XP |
| `companion/repository.py` | get_context JOIN 官阶，注入 rank 数据 |
| `companion/service.py` | `_build_persona_prompt` 师爷人格 + rank 注入 context |
| `ai/provider.py` | `answer_companion_question` 加 `persona` 参数 |
| `knowledge/repository.py` | `get_top_knowledge_map_node` 加 `module_name` 过滤 |
| `knowledge/service.py` | `select_weighted_xingce_practice` 加 `module_name` + 回退 |
| `taskstream/service.py` | `_daily_rotation_module` 每日轮换 + `_rationale` 周末文案 |

### iOS（BUILD SUCCEEDED）

| 文件 | 改动 |
|------|------|
| `StatusBarView.swift` | XP 进度条 + 品级点击 |
| `RankDetailView.swift`（新） | 仕途详情页 |
| `CelebrationView.swift` | RankUpOverlay |
| `InvestitureView.swift`（新） | 授品仪式 |
| `CompanionAskView.swift` | 师爷头像横幅 + 空状态问候 |
| `RankModels.swift` | `isUnlocked(_:)` |
| `AppModels.swift` | rankConfigs/refreshRank/rankUp 触发 |
| `AppShellView.swift` | StatusBar 传参 + 详情页/升迁 overlay |
| `OnboardingFlowView.swift` | 收尾弹授品仪式 |
| `TaskExecutionView.swift` | +XP 飘字 |
| `Gongkao.xcodeproj/project.pbxproj` | 加 RankDetailView/InvestitureView 引用 |

---

## 7. 当前数据状态

- 题库：**55214 题**，解析 53011
- 九品配置：9 行（全部在线）
- rank_system / calibration / censor / module_locks / method_cards：已建并在线
- `select_weighted_practice_questions`：已实现
- iOS：Shell + StatusBar + Hotbar + Rank + Companion + MockExam + Onboarding + GuzhuDesignSystem 全部就位

---

## 8. 开发与构建命令

### 后端
```bash
cd /Users/zxg/Documents/新项目/backend
python3 -m py_compile <改动文件>          # 语法检查
pytest tests/                            # 跑测试（需 DB schema）
```

### iOS
```bash
# ⚠️ cwd 不跨 Bash 调用持久，必须用绝对路径或 cd
xcodebuild \
  -project /Users/zxg/Documents/新项目/apps/ios/Gongkao/Gongkao.xcodeproj \
  -scheme Gongkao \
  -sdk iphonesimulator \
  -destination 'id=5A64A49A-F0C6-4A99-9BCB-518A79AF65C3' \
  -derivedDataPath /Users/zxg/Documents/新项目/apps/ios/DerivedData \
  build

# 安装 + 启动 + 截图（模拟器 UDID: 5A64A49A-F0C6-4A99-9BCB-518A79AF65C3）
APP=/Users/zxg/Documents/新项目/apps/ios/DerivedData/Build/Products/Debug-iphonesimulator/Gongkao.app
xcrun simctl boot 5A64A49A-F0C6-4A99-9BCB-518A79AF65C3 2>/dev/null
xcrun simctl install 5A64A49A-F0C6-4A99-9BCB-518A79AF65C3 "$APP"
xcrun simctl launch 5A64A49A-F0C6-4A99-9BCB-518A79AF65C3 com.xingguoxian.gongkaodazi
xcrun simctl io 5A64A49A-F0C6-4A99-9BCB-518A79AF65C3 screenshot exports/xxx.png
```

---

## 9. 已知问题 / TODO

1. **休眠惩罚无定时任务**：censor/module_locks/fame 衰减在 SSH 确认无 cron 自动执行（设计上不主动惩罚，符合"不抢学习注意力"原则，但若要加需手动建定时任务）。
2. **AI 代理偶发 520**：Gemini 反向代理偶发 520，非代码问题，属代理稳定性。
3. **iOS 未上架**：仅模拟器/真机调试，App Store Connect 未提交。
4. **会试复盘未查看会阻塞**：taskstream 逻辑里 unviewed mock review 会顶替当日任务，属设计意图。
5. **卷宗复盘暂不支持二次作答**：`docket_review` 当前只做查看与归档；要做错题重练需新增专门后端提交合同。

---

## 10. 踩坑备忘

- **xcodebuild cwd**：Bash 工具的 cwd 不跨调用持久，`xcodebuild -project` 必须用绝对路径或每次 `cd`。
- **PreferenceKey 并发安全**：Swift 6 下 PreferenceKey 的 `defaultValue` 用 `static let`（非 @MainActor property / nonisolated var），否则编译不过。
- **UINotificationFeedbackGenerator**：`.success/.warning/.error` 三种，**没有 `.impact`**；要冲击感用 `UIImpactFeedbackGenerator(style:).impactOccurred()`。
- **rsync 方向**：本地 → 远程，源用绝对路径，目标 `root@host:/srv/...`。
- **psql 连接串**：`postgresql+psycopg://` 要改成 `postgresql://` 才能连。
- **pbxproj ID**：新增文件用未占用的大数字 ID，4 处引用要齐全，否则 "cannot find in scope"。
- **题干横线不能靠纯文本猜**：`画横线部分` 的 canonical source 是 `product.questions.stem_html/material_html/option_html`；后端必须透传 HTML，iOS 统一走 `QuestionRichText`。不要在前端按“画横线”文案插线。
- **Docket 选项正文不应包含 label**：`DocketOption.label` 已是独立字段；`DocketOption.text/html` 要表示纯正文。旧题库 `option_text` 可能含 `A、`，Docket 层必须规范化，iOS 层显示用 `displayText` 兜住视觉一致性。
- **浮动 Tab 让位位置**：给滚动内容加 bottom clearance，不给外层 ScrollView 加 bottom padding；后者会把页面压短，在 Tab 背后露出怪异遮挡块。
- **战报后必须有主学习动作**：10 题完成后主 CTA 进入府衙复盘/归档，分享只能是次级动作；不要把“分享战报”当成学习闭环。

---

## 11. 产品铁律（务必遵守）

1. 游戏化**只服务坚持**，不抢学习注意力；仪式短促克制。
2. 主 KPI = 分差缩小 / 模考提升 / 上岸率，**非 DAU/时长**。
3. 不撒谎：低正确率绝不说"表现很棒"，庆祝文案分档基于真实正确率。
4. 调色板只走 GuzhuPalette；人设/术语不漂移。

---

## 12. 2026-07-07 接手后增量

- **今日政务板块过滤已闭环并部署生产**：`TodayTask.moduleName` 由后端正式下发，行测任务使用 `practice_selection.module_name`，申论等非六大板块任务为 `null`。
- **feed 不再从标题猜模块**：`feed/service.py` 改为读取 `TodayTask.moduleName`；行测任务缺字段会直接报错，避免静默猜测。
- **iOS 已消费 moduleName**：`BackendTodayTask.moduleName` 解码后用于今日政务列表和底部 CTA 过滤；无该板块任务时显示空状态。
- **验证**：后端相关 pytest 6 passed；iOS Simulator `Gongkao` BUILD/RUN SUCCEEDED；生产 `gongkao-dazi-api` 已重启且 `/health` 返回 ok。
- **题干 HTML/画横线已闭环**：`docket` API 暴露题库 HTML；iOS `QuestionRichText` 统一渲染 `<u>` 题干空线；`DocketView` 增加底部 Tab 可视区；生产 schema 与模拟器视觉均已验证。
- **卷宗翻页/选项展示已修**：当天旧 docket 读取时回填 option html 并去重 label；iOS 卡片翻页按 `cardId` 重建，避免题干富文本残留上一题。
- **战报后下一步已闭环**：完成 10 封卷宗后可直接去府衙复盘错卷/归档战报；后端 `memory` 生成 `docket_review`，iOS 工作台可进入卷宗复盘详情。
