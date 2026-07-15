# 方舟长程众测任务书：考公搭子 AI 驱动去游戏化 + 岸岸重塑

**任务代号：** `GONGKAO-AI-DEGAMIFY-ANAN-V1`  
**日期：** 2026-07-11  
**仓库根目录：** 本 monorepo（考公搭子 / gongkao-dazi）  
**权威产品定案：** `docs/aegis/specs/2026-07-11-ai-driven-degamify-an-an-spec.md`  
**产品构想（外部）：** 用户本机  
`/Users/zxg/Desktop/未命名文件夹/纯AI驱动考公APP产品方案.docx`  
（若评测环境无该路径，以仓库内 Spec 为准。）

**用途：** 同一任务并行测评 4 个模型；每个模型需通过大量工具调用完成可验证交付（目标量级 **≥500 tool calls / 模型**，以实质读写搜测改为主，禁止空转）。

**数据档位：** **B** — 岗位全量结构化 + 题库结构/抽样（无全量题图）。说明见 `data/DOMAIN-DATA-README.md`。

---

## 0. 给模型的开场提示（可整段复制）

```text
你在「考公搭子」仓库执行长程工程任务 GONGKAO-AI-DEGAMIFY-ANAN-V1。

硬约束：
1. 先完整阅读并遵守：
   docs/aegis/specs/2026-07-11-ai-driven-degamify-an-an-spec.md
   data/DOMAIN-DATA-README.md（若存在）
2. 产品主轴：AI 上岸顾问（选岗/分差/计划/解惑/复盘），不是官场 RPG。
   产品以「岗位数据 + 题库结构 + 学习行为」为基座懂用户；改造时分差/选岗/讲解必须能对上真实字段语义，禁止假造无关数据结构。
3. 去掉游戏化 RPG 展示与经济（品级/XP/仕途/民心/授品/升迁解锁等）。
4. 陪伴角色名为「岸岸」：现代吉祥物形象 + 学长感解惑助教；禁止沈砚/师爷/主公/县令。
5. UI 改为现代 AI 产品风；字体必须使用仓库本地 Maple Mono CN：
   apps/ios/Gongkao/Gongkao/Resources/Fonts/（禁止再联网下载字体）。
6. 不换技术栈（保持 FastAPI + SwiftUI iOS）；不新建 Flutter/RN 工程。
7. 不砍 AI/学习主能力；companion 解惑 API 要保留并改为岸岸人设。
8. 域数据用法：
   - 岗位全量：import-staging/positions/
   - 题结构抽样：data/question-bank-sample/（约 1500 题级 JSONL + taxonomy；无题图）
   - 用于理解复杂度并接地气实现分差/选岗展示/讲解；禁止要求下载 5GB 全量题图或重建生产库。
9. 按 Phase 0→N 推进；每阶段有产物与验证；禁止无目标全仓 rename。
10. 验证：backend pytest；iOS iPhone + iPad simulator build（CODE_SIGNING_ALLOWED=NO）。
11. 大量使用工具做：读代码、搜索、读域数据样例、改文件、跑测试、修失败——不要只写文档交差。

从 Phase 0 开始：测绘现状 + 浏览岗位/题抽样字段，对照 Spec 做差距清单，再进入实现。
```

---

## 1. 任务目标（Success Definition）

在**不更换技术栈**的前提下，把当前「治国/仕途/沈砚」气质的 iOS + FastAPI 应用，改造成：

| 层 | 目标状态 |
| --- | --- |
| 产品 | AI 驱动上岸顾问（对齐纯 AI 方案 + 仓库 Spec） |
| 游戏化 | 用户可见路径中 **无** 品级/XP/仕途/授品/民心等 RPG |
| 陪伴 | **岸岸** 可解惑（citations/quota 保留），学长话术，有吉祥物位 |
| 视觉 | 现代 AI UI + Maple 字体生效 |
| 工程 | pytest 绿；iPhone + iPad `xcodebuild` 成功 |
| 文档 | README / 关键文案与 Spec 一致；交付证据包 |

**非目标：**

- 不做 Android  
- 不重写题库/OCR 大算法  
- 不接新支付通道（已有 commerce 可修展示，不扩 scope）  
- 不做封地/排行榜/御史玩法  
- 不为凑 tool call 做无意义格式化或循环 list  

---

## 2. 锁定决策（模型不得自行改判）

| 项 | 锁定值 |
| --- | --- |
| 人设 | 学长感虚拟助教 |
| 形象 | B1 现代吉祥物 + 教练解惑 |
| 名字 | **岸岸** |
| 视觉 | 现代 AI（非竹简朱砂默认） |
| 字体 | Maple Font / Maple Mono **CN** 嵌入，OFL-1.1 |
| RPG | 删除可见层；rank 可先休眠再删 |
| 栈 | FastAPI + SwiftUI，禁止换栈 |

冲突时优先级：

1. 本任务书验收表  
2. `docs/aegis/specs/2026-07-11-ai-driven-degamify-an-an-spec.md`  
3. 纯 AI 产品方案 docx（若可读）  
4. 旧治国/鹿鸣文档 → **作废冲突条款**

---

## 3. 仓库地图（Phase 0 必读）

```text
apps/ios/Gongkao/                 SwiftUI 客户端
backend/app/domains/              FastAPI 域（companion, rank_system, position_decision, knowledge, …）
backend/tests/                    pytest
docs/aegis/specs/                 产品规格（2026-07-11-…-spec.md）
import-staging/positions/         岗位全量结构化（B 档）
data/question-bank-sample/        题结构抽样 + taxonomy（B 档，无图）
data/method_cards/                方法卡
data/DOMAIN-DATA-README.md        域数据使用说明
apps/.../Resources/Fonts/         Maple Mono CN 本地字体
scripts/full_verify.sh            后端测试 + 双机型 iOS build
ops/                              部署模板（非本任务主线）
```

高相关现有文件（实现时以搜索为准，此表帮助启动）：

- 游戏化 UI：`StatusBarView`、`RankDetailView`、`RankModels`、`InvestitureView`、`CelebrationView`、`ShareReportView`
- 主题：`GuzhuDesignSystem.swift`、各处 `DaziPalette` / `GuzhuPalette`
- 陪伴：`CompanionAskView.swift`、`backend/app/domains/companion/*`
- 品级后端：`backend/app/domains/rank_system/*`
- 壳：`AppShellView.swift`、`StudyDeskView`、`DocketView`、Onboarding*

---

## 4. 阶段划分（为高 tool-call 与可验收设计）

每阶段结束必须留下：**变更说明 + 命令输出摘要 + 未竟项**。  
允许阶段内大量搜索/阅读；**禁止**跳过 Phase 0 直接大删。

### Phase 0 — 测绘与差距（只读为主，可写差距文档）

**动作：**

- 读 Spec、README、`data/DOMAIN-DATA-README.md`、companion/rank/onboarding/status 相关代码  
- 浏览域数据（不必全读）：  
  - `import-staging/positions/` 表头与若干行（岗位/进面分字段）  
  - `data/question-bank-sample/paper_questions.sample-1500.jsonl` 与 `taxonomy_nodes.jsonl` 各读样本  
  - 对照 `backend/app/domains/positions|position_decision|knowledge|calibration`  
- 全文搜索：`沈砚|师爷|主公|县令|品级|XP|仕途|民心|Guzhu|Investiture|official_rank|award_xp`  
- 产出 `docs/aegis/work/2026-07-11-degamify-anan/<model-or-run-id>/00-gap-analysis.md`

**通过：** 差距表覆盖：可见 RPG、人设文案、主题色、字体、companion prompt、分差顶栏、onboarding 收官；并简要记录「岗/题数据如何支撑分差与讲解」（字段级，非空话）。

### Phase 1 — 文案与人设后端

**动作：**

- companion system prompt / 回复风格改为岸岸学长解惑  
- 去掉 rank/fame 作为陪伴主上下文（可保留内部字段但不驱动叙事）  
- iOS 用户可见字符串词典替换（见 Spec §5）  
- 补/改 companion 相关测试

**通过：** 测试中不再依赖「师爷」叙事；`pytest` 相关用例通过；代码库用户文案无官场关键词（允许注释/历史 doc 暂留，但 **运行时 UI/API 用户消息** 必须净）。

### Phase 2 — 去 RPG 可见层

**动作：**

- StatusBar：目标岗 + **分差** + 今日进度 + 岸岸入口；移除品级/XP  
- 移除或断开：仕途页、授品、升迁庆祝、战报民心  
- 练习反馈：去掉 `+N XP` 展示，改为错因/进度类反馈（有数据用数据，无则中性完成态）  
- rank API：客户端停用；后端可保留但标注 deprecated 或停写展示路径

**通过：** 主导航走查清单无 RPG 入口；iOS build 通过。

### Phase 3 — 现代主题 + Maple 字体

**动作：**

- 新增 `AppTheme` / `BrandPalette`（或等价），替换默认 Guzhu 竹简主题  
- 嵌入 Maple **CN** 字体文件 + Info.plist / 注册 + OFL 许可文本  
- 全局字体修饰；数字/分差可用等宽优势  
- 不嵌入 Nerd Font 图标包

**字体获取（评测环境 — 已预置，禁止依赖外网）：**

仓库已 vendored（云沙箱无需访问 GitHub）：

- `apps/ios/Gongkao/Gongkao/Resources/Fonts/MapleMono-CN-Regular.ttf`
- `apps/ios/Gongkao/Gongkao/Resources/Fonts/MapleMono-CN-Medium.ttf`
- `apps/ios/Gongkao/Gongkao/Resources/Fonts/MapleMono-CN-Bold.ttf`
- `apps/ios/Gongkao/Gongkao/Resources/Fonts/OFL-MapleMono.txt`
- 说明：`third_party/maple-font/README.md`（来源 v7.9 CN-unhinted）

模型必须用上述本地文件完成 iOS 字体注册与 UI 应用；**禁止**再下载 140MB 全量 zip。不要嵌入 Nerd Font 包。

**通过：** 关键界面使用 Maple；构建成功；许可文件在仓库内。

### Phase 4 — 岸岸 UI 与解惑入口

**动作：**

- 伙伴页/问 AI 页：现代对话 UI + 岸岸吉祥物（可用高质量占位图 + 统一组件，注明 asset 来源）  
- 题目/任务流「让岸岸讲」入口  
- 首页一句话建议气泡（可接现有 feed/taskstream/companion）  
- 保持 citations、quota、429 会员提示

**通过：** 解惑主路径可编译可点；无师爷 UI。

### Phase 5 — Onboarding 与信息架构打磨

**动作：**

- 收官改为：目标组合 + 分差 → 认识岸岸 → 第一条任务  
- Tab/文案：今日训练、训练、岸岸、我的（允许在现有 Tab 上演进，不必强行四 Tab，但信息架构语义要对齐 Spec）  
- README 一句话定位更新

**通过：** 新用户路径无授官；README 与 Spec 一致。

### Phase 6 — 清理、回归、证据包

**动作：**

- 删除未引用死代码（确认无引用后）  
- 全量 `backend` pytest  
- iPhone + iPad `xcodebuild`  
- 可选：`scripts/full_verify.sh`（若 aegis 脚本路径在环境不存在，跳过该行并注明）  
- 写 `90-evidence.md`：命令、关键 diff 统计、验收勾选表、剩余风险

**通过：** 见 §5 验收表全绿或诚实 partial + 理由。

---

## 5. 验收表（评分员用）

| # | 标准 | 证据 |
| --- | --- | --- |
| A1 | Spec 被阅读并作为约束 | gap 文档引用 Spec 章节 |
| A2 | 用户可见无品级/XP/仕途/授品/师爷沈砚 | 代码搜索 + UI 入口清单 |
| A3 | 顶栏/首页以分差或等价 AI 进度为主 | 截图或代码引用 |
| A4 | 岸岸解惑路径存在且非官场人设 | companion UI + prompt |
| A5 | 现代主题，非默认竹简官场 | Design system 文件 |
| A6 | Maple CN 嵌入 + OFL | 字体路径 + 许可文件 |
| A7 | `pytest` 全量或约定子集通过 | 日志 |
| A8 | iPhone simulator build 成功 | xcodebuild 日志 |
| A9 | iPad simulator build 成功 | xcodebuild 日志 |
| A10 | 未换栈、未砍学习主能力 | 架构说明 |
| A11 | 交付证据包完整 | work 目录 |
| A12 | 分差/选岗/讲解相关改动能指向真实域字段或 API（非纯占位假数据） | gap 或代码引用 positions/knowledge/calibration |

**致命失败（直接低分）：**

- 新建另一技术栈工程当「重写」  
- 删掉 companion/练习/选岗主能力却无替代  
- 只交文档不改运行时  
- 恢复或加强治国 RPG  
- 伪造测试通过  

---

## 6. 验证命令（默认）

在仓库根目录：

```bash
# 后端
backend/.venv/bin/python -m pytest backend/tests -q
# 若无 venv：
# python3 -m pytest backend/tests -q

# iOS（名称以本机 simulator 为准；任务书默认如下，失败则 list simulators 后替换）
xcodebuild build \
  -project apps/ios/Gongkao/Gongkao.xcodeproj \
  -scheme Gongkao \
  -destination 'platform=iOS Simulator,name=iPhone 17' \
  CODE_SIGNING_ALLOWED=NO

xcodebuild build \
  -project apps/ios/Gongkao/Gongkao.xcodeproj \
  -scheme Gongkao \
  -destination 'platform=iOS Simulator,name=iPad Pro 13-inch (M5)' \
  CODE_SIGNING_ALLOWED=NO
```

改 rank/companion 时至少跑相关测试文件，阶段结束跑全量 pytest。

---

## 7. 工作方法（抬高 tool-call 质量）

1. **先搜索再改**：每个子系统 rg/grep + 读文件，再 edit  
2. **小步提交式推进**：可按 phase 逻辑分组；若允许 git commit，message 用 `feat/fix:` 前缀  
3. **测红测绿**：改后端必 pytest；改 iOS 必 build  
4. **删除前引用检查**：无引用再删  
5. **禁止**：重复 cat 同一文件 50 次、无改动的 format-only 全仓扫  
6. **鼓励**：跨 iOS/backend 对照契约、补测试、修编译错误、更新 Spec 未尽的实现笔记  

**Tool-call 预期构成（供模型自检，非硬指标拆分）：**

- ~30% 搜索与阅读建立地图  
- ~40% 编辑实现  
- ~20% 测试/构建/修错  
- ~10% 文档与证据  

---

## 8. 交付目录结构（每个模型一份）

```text
docs/aegis/work/2026-07-11-degamify-anan/<run-id>/
  00-gap-analysis.md
  10-phase-log.md          # 各阶段时间线与决策
  90-evidence.md           # 验收表 + 命令输出摘要
  99-risks.md              # 剩余风险
```

代码改动在正常路径：`apps/ios/`、`backend/`、字体资源、`README.md` 等。

---

## 9. 评分维度建议（给众测方）

| 维度 | 权重建议 | 说明 |
| --- | --- | --- |
| 目标达成 | 35% | 验收表 A1–A11 |
| 架构纪律 | 15% | 未换栈、未双引擎、rank 处理合理 |
| 实现质量 | 20% | 编译/测试、错误处理、文案一致 |
| 过程有效性 | 15% | tool 用于推进而非空转；阶段清晰 |
| 证据完整 | 15% | gap/evidence/风险诚实 |

**过程分不等于 tool call 绝对数**，但 tool call **过少且交付空**、或 **极多却无 diff** 都应扣分。

---

## 10. 短版粘贴条（System / Task 各一份）

### System

```text
你是资深全栈工程师，在考公搭子（SwiftUI + FastAPI）仓库做长程改造。
服从 docs/aegis/specs/2026-07-11-ai-driven-degamify-an-an-spec.md 与任务书
docs/aegis/plans/2026-07-11-longrun-eval-ai-driven-an-an-brief.md。
AI 上岸顾问为主；岸岸为解惑伙伴；去 RPG；现代 UI + Maple CN 字体。
禁止换栈、禁止空转、禁止只写文档。用工具读搜改测直到验收通过。
```

### User Task

```text
执行 GONGKAO-AI-DEGAMIFY-ANAN-V1：按 Phase 0→6 完成去游戏化、岸岸人设与 UI、
现代主题、Maple 字体、分差向信息架构，并提交证据包与双端验证日志。
从 Phase 0 gap analysis 开始。
```

---

## 11. 维护者备注（你自己看，可不给模型）

- 本任务专为 **众测长程** 设计，不是让当前会话直接改完。  
- 无真实用户 → 允许 breaking；模型若过度做兼容 rank，可在评分时视为保守但非失败。  
- 字体构建可能耗 tool calls，属预期。  
- 若 4 模型并行，应用不同 `run-id` 工作目录或不同 git worktree，避免互踩。  
- 陪陪刷仓库 **不在** 本任务范围。  
