# 治国 App · UI 问题彻底修复记录（2026-07-07）

> 基于 `2026-07-07-ui-audit.md` 的审计结论，本轮把暴露的 UI 问题一次性解决。
> 配套代码改动已全部通过 `xcodebuild`（iPhone 17 模拟器，BUILD SUCCEEDED）。

## 一、P0 · 双设计系统统一（核心）

**问题**：`GuzhuPalette`（古风竹简+朱砂，用在顶栏/侧栏）与 `DaziPalette`（现代白底+蓝紫渐变，用在今日页/Feed/模考/庆祝/各种卡片）并存，用户感觉"像两个 App 拼一起"，直接破坏「治国」世界观沉浸感。

**修法**：`GuzhuPalette` 设为唯一真源，`DaziPalette` 重写为**桥接层**——每个语义令牌返回对应的古风值，调用点零改动即获一致外观：

| DaziPalette 令牌 | 桥接到 | 说明 |
|---|---|---|
| `paper` / `card` / `ink` / `cardStroke` | 同名 GuzhuPalette | 底色/卡片/正文/描边统一为竹简米黄+墨色 |
| `brand` | `cinnabar` 朱砂红 | 主行动色由蓝紫→朱砂 |
| `accentBlue` / `accentPurple` | `azurite` 石青蓝 | 链接/数字强调 |
| `positive` | `celadon` 青瓷绿 | 正确/正面反馈 |
| `alert` | 新古风朱红 | 与 brand 区分，不混淆 |
| `highlightGradient` | 朱砂→赭石渐变 | CTA 主渐变 |
| `track(_:)` | `moduleColor(_:)` | 双轨色统一 |

> 共影响 18 个文件约 200 处调用，全部无需改动即统一。这是"彻底"且零风险的做法（不动调用点，只动两个定义文件）。

## 二、P1 · 人设与术语漂移

**问题**：人称呼叫一会儿"县令"（顶栏/侧栏）一会儿"局长"（问候语/庆祝层）；"今日任务"与"今日政务"并存。

**修法**：
- 全量 `局长` → `县令`（FeatureViews 问候语、CelebrationView 8 处、MockExamView 恭喜语）。
- `今日任务全部完成` → `今日政务全部完成`（FeatureViews / StudyDeskView / DesignSystemPreview 三处完成态标题）。
- MockExamView 恭喜语同步 `模考` → `会试`（对齐 `GuzhuTerms` 术语表）。

## 三、P2 · 归来推送（留存钩子补全）

**问题**：会话结束钩子（明日预告）已接，但"断更后召回"的推送完全没实现。

**修法**（内联进 `AppShellView.swift`，避免新增文件进 target 的工程风险）：
- `onAppear` 请求通知授权；`scenePhase == .background` 时调度本地通知。
- 文案**学习优先**：标题"县令，已一日未理政"；正文带**考试倒计时 + 分差**（"距国考约 N 天，当前距上岸还差 X 分。今日政务已备好，回来再进一步。"），无 cosmetic 奖励、不情感绑架。
- 每次离台先清旧的再排新的，避免堆叠。

## 四、P3 · 收尾验证

**后端惩罚机制安全排查（SSH）**：确认服务器**无任何** systemd timer / cron / Celery / APScheduler 在自动跑 `censor` / `module_locks` / `fame` 衰减。惩罚仅在用户答题动作时触发（非后台静默惩罚），且 UI 已不展示民心/精力——现有用户不会被静默惩罚。

**ModuleHotbar 选中态**：原 `selectedModule` 是死状态（选中无反应）。本轮：
- 改为可选类型 `GuzhuModule?`，默认 `nil`=「全部」；新增"全部"芯片。
- 选中态/高亮现在正确反映，并接入共享 `AppState.selectedModule`。
- ⚠️ **按板块过滤今日任务暂未实现**：`BackendTodayTask` 只有粗粒度 `track`（行测/申论），没有细分到 6 板块的字段，当前数据模型下按板块过滤会全部坍缩成"行测"。这是**后端数据建模限制**，非 UI 能干净解决——需在后端给任务打上 6 板块标签后，再在前端做 `TaskPathView` 过滤。已记入待办。

## 五、验证

- ✅ `xcodebuild` BUILD SUCCEEDED（iPhone 17 模拟器，CODE_SIGNING_ALLOWED=NO）
- ✅ 调色板桥接后全 App 视觉统一为古风
- ✅ 人设/术语一致
- ✅ 归来推送逻辑就绪（真机需用户授权通知）
- ⚠️ 按板块过滤待后端支持

## 改动文件清单

- `CoachComponents.swift` — DaziPalette 重写为 GuzhuPalette 桥接层（P0）
- `FeatureViews.swift` — 局长→县令、今日政务标题、移除未实现的过滤（P1/P3）
- `CelebrationView.swift` — 局长→县令（P1）
- `MockExamView.swift` — 县令+会试（P1）
- `StudyDeskView.swift` — 今日政务标题（P1）
- `DesignSystemPreview.swift` — 今日政务标题（P1）
- `AppShellView.swift` — 通知授权/调度 + 热bar 接入 AppState（P2/P3）
- `AppModels.swift` — 新增 `selectedModule`（P3）
- `ModuleHotbarView.swift` — 可选选中态 + 全部芯片（P3）
